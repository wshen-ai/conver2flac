import os
import sys
import asyncio
import json
import struct
import subprocess
from pathlib import Path
from typing import List, Tuple, Optional
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from mutagen.flac import FLAC, Picture
from mutagen.mp3 import MP3 as MP3File
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TRCK, TDRC
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QSpinBox, QProgressBar,
    QTextEdit, QFileDialog, QGroupBox, QMessageBox, QListWidget,
    QListWidgetItem, QAbstractItemView, QSplitter, QComboBox
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, pyqtSlot, QObject, QSettings
)
from PyQt5.QtGui import QDragEnterEvent, QDropEvent

# NCM格式常量
CORE_KEY = bytes.fromhex('687A4852416D736F356B496E62617857')
META_KEY = bytes.fromhex('2331346C6A6B5F215C5D2630553C2728')

# 输出格式配置
OUTPUT_FORMATS = {
    'FLAC': {
        'extension': '.flac',
        'ffmpeg_args': ['-c:a', 'flac', '-compression_level', '8'],
        'description': '无损格式',
    },
    'MP3': {
        'extension': '.mp3',
        'ffmpeg_args': None,  # 动态根据码率生成
        'description': '有损压缩',
    },
}

MP3_QUALITY = {
    '高 (320kbps)': ['-c:a', 'libmp3lame', '-b:a', '320k'],
    '中 (192kbps)': ['-c:a', 'libmp3lame', '-b:a', '192k'],
    '低 (128kbps)': ['-c:a', 'libmp3lame', '-b:a', '128k'],
}

def find_ffmpeg() -> str:
    """查找 ffmpeg.exe：优先 EXE 同目录，其次 PATH"""
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
    else:
        exe_dir = Path(__file__).parent
    local_ffmpeg = exe_dir / 'ffmpeg.exe'
    if local_ffmpeg.exists():
        return str(local_ffmpeg)
    import shutil
    found = shutil.which('ffmpeg')
    if found:
        return found
    return None

class NCMDecrypter:
    """NCM文件解密器"""
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.file_size = self.file_path.stat().st_size
        self.offset = 0
        self.key_box = None
        self.metadata = None
        self.cover_data = None
        self.audio_data = None
        self.audio_format = None

    def read_bytes(self, count: int) -> bytes:
        with open(self.file_path, 'rb') as f:
            f.seek(self.offset)
            data = f.read(count)
            self.offset += count
            return data

    def decrypt(self, progress_cb=None, is_cancelled=None) -> Tuple[bytes, dict, bytes, str]:
        header = self.read_bytes(8)
        if header != b'CTENFDAM':
            raise ValueError(f"File {self.file_path} is not a valid NCM format")
        self.offset += 2
        key_len = struct.unpack('<I', self.read_bytes(4))[0]
        encrypted_key = self.read_bytes(key_len)
        encrypted_key = bytes(byte ^ 0x64 for byte in encrypted_key)
        cipher = AES.new(CORE_KEY, AES.MODE_ECB)
        decrypted_key = unpad(cipher.decrypt(encrypted_key), AES.block_size)
        rc4_key = decrypted_key[17:]
        self.key_box = self._generate_rc4_key_box(rc4_key)
        meta_len = struct.unpack('<I', self.read_bytes(4))[0]
        if meta_len > 0:
            encrypted_meta = self.read_bytes(meta_len)
            encrypted_meta = bytes(byte ^ 0x63 for byte in encrypted_meta)
            encrypted_meta = encrypted_meta[22:]
            import base64
            encrypted_meta = base64.b64decode(encrypted_meta)
            cipher = AES.new(META_KEY, AES.MODE_ECB)
            decrypted_meta = unpad(cipher.decrypt(encrypted_meta), AES.block_size)
            decrypted_meta = decrypted_meta[6:]
            self.metadata = json.loads(decrypted_meta.decode('utf-8'))
            self.audio_format = self.metadata.get('format', 'mp3')
        else:
            self.metadata = {}
            self.audio_format = 'mp3'
        self.offset += 4 + 5
        cover_len = struct.unpack('<I', self.read_bytes(4))[0]
        if cover_len > 0:
            self.cover_data = self.read_bytes(cover_len)
        self.audio_data = self._decrypt_audio_data(progress_cb, is_cancelled)
        return self.audio_data, self.metadata, self.cover_data, self.audio_format

    def _generate_rc4_key_box(self, key: bytes) -> List[int]:
        key_len = len(key)
        box = list(range(256))
        j = 0
        for i in range(256):
            j = (j + box[i] + key[i % key_len]) % 256
            box[i], box[j] = box[j], box[i]
        return box

    def _decrypt_audio_data(self, progress_cb=None, is_cancelled=None) -> bytes:
        audio_data = bytearray()
        chunk_size = 0x8000
        total_audio = self.file_size - self.offset
        with open(self.file_path, 'rb') as f:
            f.seek(self.offset)
            processed = 0
            while True:
                if is_cancelled and is_cancelled():
                    raise InterruptedError("Cancelled by user")
                chunk = bytearray(f.read(chunk_size))
                if not chunk:
                    break
                for i in range(len(chunk)):
                    j = (i + 1) % 256
                    k = (self.key_box[j] + self.key_box[(self.key_box[j] + j) % 256]) % 256
                    chunk[i] ^= self.key_box[k]
                audio_data.extend(chunk)
                processed += len(chunk)
                if progress_cb:
                    progress_cb(processed, total_audio)
        return bytes(audio_data)

class ConversionWorker(QObject):
    progress_updated = pyqtSignal(int, int)
    file_progress = pyqtSignal(str, int, int)
    file_started = pyqtSignal(str)
    file_finished = pyqtSignal(str, bool, str)
    log_message = pyqtSignal(str)
    finished = pyqtSignal(int, int)

    def __init__(self, input_files: List[Path], output_dir: Path, max_concurrent: int,
                 output_format: str, mp3_quality: str):
        super().__init__()
        self.input_files = input_files
        self.output_dir = output_dir
        self.max_concurrent = max_concurrent
        self.output_format = output_format
        self.mp3_quality = mp3_quality
        self.is_running = True

    def get_output_extension(self) -> str:
        return OUTPUT_FORMATS[self.output_format]['extension']

    def get_ffmpeg_args(self) -> list:
        if self.output_format == 'FLAC':
            return OUTPUT_FORMATS['FLAC']['ffmpeg_args']
        else:
            return MP3_QUALITY[self.mp3_quality]

    async def convert_audio(self, input_data: bytes, input_format: str, output_path: Path) -> bool:
        try:
            # 如果输出格式是FLAC且输入已经是FLAC，直接写入
            if self.output_format == 'FLAC' and input_format == 'flac':
                with open(output_path, 'wb') as f:
                    f.write(input_data)
                return True

            # 创建临时文件
            temp_file = output_path.with_suffix(f'.{input_format}')
            with open(temp_file, 'wb') as f:
                f.write(input_data)

            ffmpeg_path = find_ffmpeg()
            if not ffmpeg_path:
                self.log_message.emit("ERROR: ffmpeg not found! Please place ffmpeg.exe in the same folder.")
                return False

            cmd = [ffmpeg_path, '-y', '-i', str(temp_file)]
            cmd.extend(self.get_ffmpeg_args())
            cmd.extend(['-v', 'error', str(output_path)])

            process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = await process.communicate()
            temp_file.unlink()
            if process.returncode != 0:
                self.log_message.emit(f"FFmpeg error: {stderr.decode('utf-8', errors='ignore')}")
                return False
            return True
        except Exception as e:
            self.log_message.emit(f"Conversion error: {str(e)}")
            return False

    def write_metadata(self, output_path: Path, metadata: dict, cover_data: bytes):
        """写入元数据到输出文件"""
        ext = self.get_output_extension()
        if ext == '.flac':
            audio = FLAC(output_path)
            audio['title'] = metadata.get('musicName', '')
            artists = metadata.get('artist', [])
            if isinstance(artists, list) and len(artists) > 0:
                if isinstance(artists[0], list):
                    audio['artist'] = '/'.join(a[0] for a in artists)
                elif isinstance(artists[0], dict):
                    audio['artist'] = '/'.join(a.get('name', str(a)) for a in artists)
                else:
                    audio['artist'] = str(artists[0])
            else:
                audio['artist'] = str(artists)
            audio['album'] = metadata.get('album', '')
            audio['tracknumber'] = str(metadata.get('track', 0))
            audio['date'] = str(metadata.get('year', ''))
            if cover_data:
                pic = Picture()
                pic.data = cover_data
                pic.type = 3
                pic.mime = 'image/jpeg'
                audio.add_picture(pic)
            audio.save()
        elif ext == '.mp3':
            audio = MP3File(output_path)
            audio.add_tags()
            audio.tags.add(TIT2(encoding=3, text=metadata.get('musicName', '')))
            artists = metadata.get('artist', [])
            artist_str = ''
            if isinstance(artists, list) and len(artists) > 0:
                if isinstance(artists[0], list):
                    artist_str = '/'.join(a[0] for a in artists)
                elif isinstance(artists[0], dict):
                    artist_str = '/'.join(a.get('name', str(a)) for a in artists)
                else:
                    artist_str = str(artists[0])
            else:
                artist_str = str(artists)
            audio.tags.add(TPE1(encoding=3, text=artist_str))
            audio.tags.add(TALB(encoding=3, text=metadata.get('album', '')))
            audio.tags.add(TRCK(encoding=3, text=str(metadata.get('track', 0))))
            audio.tags.add(TDRC(encoding=3, text=str(metadata.get('year', ''))))
            if cover_data:
                audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data))
            audio.save()

    async def process_file(self, file_path: Path) -> Tuple[bool, str, Optional[str]]:
        try:
            self.file_started.emit(file_path.name)
            self.log_message.emit(f"Decrypting: {file_path.name}")
            def on_progress(processed, total):
                self.file_progress.emit(file_path.name, processed, total)
            def is_cancelled():
                return not self.is_running
            decrypter = NCMDecrypter(str(file_path))
            audio_data, metadata, cover_data, audio_format = decrypter.decrypt(on_progress, is_cancelled)
            self.log_message.emit(f"Converting to {self.output_format}: {file_path.name}")
            ext = self.get_output_extension()
            if metadata and 'musicName' in metadata and 'artist' in metadata:
                artists = metadata['artist']
                if isinstance(artists, list) and len(artists) > 0:
                    if isinstance(artists[0], list):
                        artist_str = '/'.join(a[0] for a in artists)
                    elif isinstance(artists[0], dict):
                        artist_str = '/'.join(a.get('name', str(a)) for a in artists)
                    else:
                        artist_str = str(artists[0])
                else:
                    artist_str = str(artists)
                output_name = f"{artist_str} - {metadata['musicName']}{ext}"
                output_name = "".join(c for c in output_name if c not in '<>:"/\\|?*')
            else:
                output_name = file_path.stem + ext
            output_path = self.output_dir / output_name
            success = await self.convert_audio(audio_data, audio_format, output_path)
            if success and metadata:
                self.write_metadata(output_path, metadata, cover_data)
                self.log_message.emit(f"Success: {file_path.name} -> {output_name}")
                return True, file_path.name, str(output_path)
            else:
                self.log_message.emit(f"Failed: {file_path.name}")
                return False, file_path.name, None
        except InterruptedError:
            self.log_message.emit(f"Cancelled: {file_path.name}")
            return False, file_path.name, None
        except Exception as e:
            self.log_message.emit(f"Error processing {file_path.name}: {str(e)}")
            return False, file_path.name, None

    async def process_files(self):
        semaphore = asyncio.Semaphore(self.max_concurrent)
        completed = 0
        success_count = 0
        fail_count = 0
        async def process_with_semaphore(file_path):
            nonlocal completed, success_count, fail_count
            async with semaphore:
                if not self.is_running:
                    return
                success, name, output = await self.process_file(file_path)
                completed += 1
                if success:
                    success_count += 1
                else:
                    fail_count += 1
                self.progress_updated.emit(completed, len(self.input_files))
                self.file_finished.emit(name, success, output)
        tasks = [process_with_semaphore(file) for file in self.input_files]
        await asyncio.gather(*tasks)
        self.finished.emit(success_count, fail_count)

    @pyqtSlot()
    def run(self):
        asyncio.run(self.process_files())

    def stop(self):
        self.is_running = False

class DropListWidget(QListWidget):
    files_dropped = pyqtSignal(list)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)
    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.CopyAction)
            event.accept()
            files = []
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    if path.is_file() and path.suffix.lower() == '.ncm':
                        files.append(path)
                    elif path.is_dir():
                        files.extend(path.rglob('*.ncm'))
            self.files_dropped.emit(files)
        else:
            super().dropEvent(event)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("NCM2FLAC", "Converter")
        self.worker = None
        self.thread = None
        self.input_files = []
        self.init_ui()
        self.load_settings()

    def init_ui(self):
        self.setWindowTitle("NCM to FLAC/MP3 Converter")
        self.setGeometry(100, 100, 800, 650)
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # 目录选择区域
        dir_group = QGroupBox("Directories")
        dir_layout = QVBoxLayout(dir_group)
        input_layout = QHBoxLayout()
        input_layout.addWidget(QLabel("Input Directory:"))
        self.input_path_edit = QLineEdit()
        input_layout.addWidget(self.input_path_edit)
        self.browse_input_btn = QPushButton("Browse")
        self.browse_input_btn.clicked.connect(self.browse_input)
        input_layout.addWidget(self.browse_input_btn)
        dir_layout.addLayout(input_layout)
        output_layout = QHBoxLayout()
        output_layout.addWidget(QLabel("Output Directory:"))
        self.output_path_edit = QLineEdit()
        output_layout.addWidget(self.output_path_edit)
        self.browse_output_btn = QPushButton("Browse")
        self.browse_output_btn.clicked.connect(self.browse_output)
        output_layout.addWidget(self.browse_output_btn)
        dir_layout.addLayout(output_layout)
        main_layout.addWidget(dir_group)

        # 设置区域
        settings_group = QGroupBox("Settings")
        settings_layout = QHBoxLayout(settings_group)

        settings_layout.addWidget(QLabel("Output Format:"))
        self.format_combo = QComboBox()
        self.format_combo.addItem("FLAC - 无损格式")
        self.format_combo.addItem("MP3 - 有损压缩")
        self.format_combo.currentIndexChanged.connect(self.on_format_changed)
        settings_layout.addWidget(self.format_combo)

        settings_layout.addWidget(QLabel("MP3 Quality:"))
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("高 (320kbps)")
        self.quality_combo.addItem("中 (192kbps)")
        self.quality_combo.addItem("低 (128kbps)")
        settings_layout.addWidget(self.quality_combo)

        settings_layout.addWidget(QLabel("Concurrent:"))
        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 16)
        self.concurrent_spin.setValue(3)
        settings_layout.addWidget(self.concurrent_spin)

        settings_layout.addStretch()
        self.add_files_btn = QPushButton("Add NCM Files")
        self.add_files_btn.clicked.connect(self.add_files)
        settings_layout.addWidget(self.add_files_btn)
        self.clear_files_btn = QPushButton("Clear List")
        self.clear_files_btn.clicked.connect(self.clear_files)
        settings_layout.addWidget(self.clear_files_btn)
        main_layout.addWidget(settings_group)

        # 文件列表区域
        list_group = QGroupBox("Files to Convert (0 files)")
        list_layout = QVBoxLayout(list_group)
        self.file_list = DropListWidget()
        self.file_list.files_dropped.connect(self.add_dropped_files)
        list_layout.addWidget(self.file_list)
        main_layout.addWidget(list_group)

        # 进度区域
        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout(progress_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setAlignment(Qt.AlignCenter)
        progress_layout.addWidget(self.progress_bar)
        self.status_label = QLabel("Ready")
        progress_layout.addWidget(self.status_label)
        main_layout.addWidget(progress_group)

        # 日志区域
        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        log_layout.addWidget(self.log_text)
        main_layout.addWidget(log_group)

        # 按钮区域
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.start_btn = QPushButton("Start Conversion")
        self.start_btn.clicked.connect(self.start_conversion)
        btn_layout.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_conversion)
        self.stop_btn.setEnabled(False)
        btn_layout.addWidget(self.stop_btn)
        main_layout.addLayout(btn_layout)

        # 初始状态：FLAC 时禁用质量选择
        self.quality_combo.setEnabled(False)

    def on_format_changed(self, index):
        """格式切换时：MP3 启用质量选择，FLAC 禁用"""
        self.quality_combo.setEnabled(index == 1)  # MP3 = index 1

    def get_selected_format(self) -> str:
        if self.format_combo.currentIndex() == 0:
            return 'FLAC'
        return 'MP3'

    def get_selected_quality(self) -> str:
        return self.quality_combo.currentText()

    def load_settings(self):
        input_dir = self.settings.value("input_dir", "")
        output_dir = self.settings.value("output_dir", "")
        max_concurrent = self.settings.value("max_concurrent", 3, type=int)
        output_format = self.settings.value("output_format", "FLAC")
        mp3_quality = self.settings.value("mp3_quality", "高 (320kbps)")
        if input_dir:
            self.input_path_edit.setText(input_dir)
        if output_dir:
            self.output_path_edit.setText(output_dir)
        self.concurrent_spin.setValue(max_concurrent)
        if output_format == 'MP3':
            self.format_combo.setCurrentIndex(1)
        idx = self.quality_combo.findText(mp3_quality)
        if idx >= 0:
            self.quality_combo.setCurrentIndex(idx)

    def save_settings(self):
        self.settings.setValue("input_dir", self.input_path_edit.text())
        self.settings.setValue("output_dir", self.output_path_edit.text())
        self.settings.setValue("max_concurrent", self.concurrent_spin.value())
        self.settings.setValue("output_format", self.get_selected_format())
        self.settings.setValue("mp3_quality", self.get_selected_quality())

    def browse_input(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Input Directory")
        if dir_path:
            self.input_path_edit.setText(dir_path)
            self.scan_input_dir()

    def browse_output(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if dir_path:
            self.output_path_edit.setText(dir_path)

    def scan_input_dir(self):
        input_dir = Path(self.input_path_edit.text())
        if input_dir.exists():
            ncm_files = list(input_dir.glob('*.ncm'))
            self.add_files_to_list(ncm_files)

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select NCM Files", "", "NCM Files (*.ncm)")
        if files:
            self.add_files_to_list([Path(f) for f in files])

    def add_files_to_list(self, files):
        for f in files:
            if f.suffix.lower() == '.ncm' and f not in self.input_files:
                self.input_files.append(f)
                item = QListWidgetItem(f.name)
                item.setToolTip(str(f))
                self.file_list.addItem(item)
        self.update_file_count()

    def add_dropped_files(self, files):
        self.add_files_to_list(files)

    def clear_files(self):
        self.input_files.clear()
        self.file_list.clear()
        self.update_file_count()

    def update_file_count(self):
        for g in self.findChildren(QGroupBox):
            if "Files to Convert" in g.title():
                g.setTitle(f"Files to Convert ({len(self.input_files)} files)")
                break

    def start_conversion(self):
        if not self.input_files:
            QMessageBox.warning(self, "Warning", "No NCM files selected!")
            return
        output_dir = Path(self.output_path_edit.text())
        if not output_dir.exists():
            try:
                output_dir.mkdir(parents=True)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Cannot create output directory: {e}")
                return
        self.save_settings()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.add_files_btn.setEnabled(False)
        self.clear_files_btn.setEnabled(False)
        self.log_text.clear()
        self.progress_bar.setValue(0)
        fmt = self.get_selected_format()
        quality = self.get_selected_quality()
        self.status_label.setText(f"Converting to {fmt}...")
        self.thread = QThread()
        self.worker = ConversionWorker(
            self.input_files.copy(),
            output_dir,
            self.concurrent_spin.value(),
            fmt,
            quality,
        )
        self.worker.moveToThread(self.thread)
        self.worker.progress_updated.connect(self.on_progress_updated)
        self.worker.file_progress.connect(self.on_file_progress)
        self.worker.file_started.connect(self.on_file_started)
        self.worker.file_finished.connect(self.on_file_finished)
        self.worker.log_message.connect(self.on_log_message)
        self.worker.finished.connect(self.on_finished)
        self.thread.started.connect(self.worker.run)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def stop_conversion(self):
        if self.worker:
            self.worker.stop()
            self.status_label.setText("Stopping...")

    def on_progress_updated(self, completed, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(completed)

    def on_file_progress(self, name, processed, total):
        pct = int(processed / total * 100) if total > 0 else 0
        self.status_label.setText(f"Decrypting: {name} ({pct}%)")

    def on_file_started(self, name):
        self.status_label.setText(f"Processing: {name}")

    def on_file_finished(self, name, success, output):
        pass

    def on_log_message(self, message):
        self.log_text.append(message)

    def on_finished(self, success_count, fail_count):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.add_files_btn.setEnabled(True)
        self.clear_files_btn.setEnabled(True)
        total = success_count + fail_count
        fmt = self.get_selected_format()
        self.status_label.setText(f"Completed: {success_count}/{total} succeeded, {fail_count} failed ({fmt})")
        QMessageBox.information(self, "Conversion Complete",
            f"Conversion finished!\n\nFormat: {fmt}\nSuccess: {success_count}\nFailed: {fail_count}")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
