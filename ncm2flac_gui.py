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
    QListWidgetItem, QAbstractItemView, QCheckBox, QComboBox
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, pyqtSlot, QObject, QSettings
)
from PyQt5.QtGui import QDragEnterEvent, QDropEvent

# 支持的输入格式
AUDIO_EXTENSIONS = {'.ncm', '.wav', '.flac', '.mp3', '.m4a', '.ogg', '.wma', '.aac', '.opus', '.ape', '.wv', '.aiff', '.aif'}
CORE_KEY = bytes.fromhex('687A4852416D736F356B496E62617857')
META_KEY = bytes.fromhex('2331346C6A6B5F215C5D2630553C2728')

OUTPUT_FORMATS = {
    'FLAC': {'extension': '.flac', 'ffmpeg_args': ['-c:a', 'flac', '-compression_level', '8'], 'description': '无损格式'},
    'MP3':  {'extension': '.mp3',  'ffmpeg_args': None, 'description': '有损压缩'},
}

MP3_QUALITY = {
    '高 (320kbps)': ['-c:a', 'libmp3lame', '-b:a', '320k'],
    '中 (192kbps)': ['-c:a', 'libmp3lame', '-b:a', '192k'],
    '低 (128kbps)': ['-c:a', 'libmp3lame', '-b:a', '128k'],
}

SEPARATION_MODES = {
    '不分离': None,
    '分离伴奏（去人声）': 'no_vocals',
    '分离人声（去伴奏）': 'vocals',
    '分离人声+伴奏（两轨都保留）': 'both',
}

# ── 工具函数 ──────────────────────────────────────
def find_ffmpeg() -> str:
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

def run_demucs(input_path: str, output_dir: str, log_cb=None) -> Optional[dict]:
    """运行 Demucs 人声分离，返回 {vocals: wav_path, no_vocals: wav_path} 字典"""
    try:
        import numpy as np
        import soundfile as sf
        import torch
        from demucs.pretrained import get_model
        from demucs.apply import apply_model

        if log_cb:
            log_cb("Loading Demucs model (htdemucs)...")

        model = get_model(name='htdemucs')
        model.cpu()
        model.eval()

        if log_cb:
            log_cb(f"Loading audio: {Path(input_path).name}")

        data, sr = sf.read(input_path)
        # 转立体声
        if data.ndim == 1:
            data = np.column_stack([data, data])
        wav = torch.from_numpy(data.T).float().unsqueeze(0)  # (1, 2, T)

        if log_cb:
            log_cb("Running AI separation (this may take a while)...")

        with torch.no_grad():
            sources = apply_model(model, wav, device='cpu')[0]

        # sources shape: (4, 2, T) -> drums, bass, other, vocals
        # 合成伴奏 = drums + bass + other
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        result = {}

        # 保存人声
        vocals_np = sources[3].numpy().T
        max_v = np.abs(vocals_np).max()
        if max_v > 0:
            vocals_np = vocals_np / max_v * 0.95
        vocals_path = out_dir / 'vocals.wav'
        sf.write(str(vocals_path), (vocals_np * 32767).astype(np.int16), sr)
        result['vocals'] = str(vocals_path)

        # 合成伴奏 = drums(0) + bass(1) + other(2)
        no_vocals_np = (sources[0] + sources[1] + sources[2]).numpy().T
        max_nv = np.abs(no_vocals_np).max()
        if max_nv > 0:
            no_vocals_np = no_vocals_np / max_nv * 0.95
        no_vocals_path = out_dir / 'no_vocals.wav'
        sf.write(str(no_vocals_path), (no_vocals_np * 32767).astype(np.int16), sr)
        result['no_vocals'] = str(no_vocals_path)

        if log_cb:
            log_cb("Separation complete!")
        return result
    except Exception as e:
        if log_cb:
            log_cb(f"Demucs failed: {str(e)}")
        return None

def convert_audio_file(input_path: str, output_path: str, fmt: str, mp3_quality: str, log_cb=None) -> bool:
    """用 ffmpeg 把音频文件转成目标格式"""
    try:
        ffmpeg_path = find_ffmpeg()
        if not ffmpeg_path:
            if log_cb:
                log_cb("ERROR: ffmpeg not found!")
            return False
        if fmt == 'FLAC':
            args = ['-c:a', 'flac', '-compression_level', '8']
        else:
            args = MP3_QUALITY[mp3_quality]
        cmd = [ffmpeg_path, '-y', '-i', input_path] + args + ['-v', 'error', output_path]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            if log_cb:
                log_cb(f"FFmpeg error: {proc.stderr[:200]}")
            return False
        return True
    except Exception as e:
        if log_cb:
            log_cb(f"Conversion error: {str(e)}")
        return False

# ── NCM 解密器 ────────────────────────────────────
class NCMDecrypter:
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

# ── 转换工作线程 ──────────────────────────────────
class ConversionWorker(QObject):
    progress_updated = pyqtSignal(int, int)
    file_progress = pyqtSignal(str, int, int)
    file_started = pyqtSignal(str)
    file_finished = pyqtSignal(str, bool, str)
    log_message = pyqtSignal(str)
    finished = pyqtSignal(int, int)

    def __init__(self, input_files: List[Path], output_dir: Path, max_concurrent: int,
                 output_format: str, mp3_quality: str, separation_mode: str):
        super().__init__()
        self.input_files = input_files
        self.output_dir = output_dir
        self.max_concurrent = max_concurrent
        self.output_format = output_format
        self.mp3_quality = mp3_quality
        self.separation_mode = separation_mode
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
            if self.output_format == 'FLAC' and input_format == 'flac':
                with open(output_path, 'wb') as f:
                    f.write(input_data)
                return True
            temp_file = output_path.with_suffix(f'.{input_format}')
            with open(temp_file, 'wb') as f:
                f.write(input_data)
            ffmpeg_path = find_ffmpeg()
            if not ffmpeg_path:
                self.log_message.emit("ERROR: ffmpeg not found!")
                return False
            cmd = [ffmpeg_path, '-y', '-i', str(temp_file)]
            cmd.extend(self.get_ffmpeg_args())
            cmd.extend(['-v', 'error', str(output_path)])
            process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = await process.communicate()
            temp_file.unlink()
            if process.returncode != 0:
                self.log_message.emit(f"FFmpeg error: {stderr.decode('utf-8', errors='ignore')[:200]}")
                return False
            return True
        except Exception as e:
            self.log_message.emit(f"Conversion error: {str(e)}")
            return False

    def write_metadata(self, output_path: Path, metadata: dict, cover_data: bytes):
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

    def do_separation(self, converted_path: Path, output_stem: str) -> List[str]:
        """对转换后的文件做人声分离，返回生成的文件路径列表"""
        self.log_message.emit(f"Separating vocals: {converted_path.name}...")
        sep_mode = SEPARATION_MODES.get(self.separation_mode)
        if not sep_mode:
            return []

        # 用临时目录放 Demucs 输出
        import tempfile
        tmp_sep = tempfile.mkdtemp(prefix='ncm_sep_')
        try:
            result = run_demucs(str(converted_path), tmp_sep, self.log_message.emit)
            if not result:
                self.log_message.emit("Vocal separation failed, skipping.")
                return []

            ext = self.get_output_extension()
            produced = []

            # 根据选择的模式决定输出哪些轨
            stems_to_convert = []
            if sep_mode == 'no_vocals':
                stems_to_convert = [('no_vocals', f'{output_stem} - 伴奏')]
            elif sep_mode == 'vocals':
                stems_to_convert = [('vocals', f'{output_stem} - 人声')]
            elif sep_mode == 'both':
                stems_to_convert = [
                    ('no_vocals', f'{output_stem} - 伴奏'),
                    ('vocals', f'{output_stem} - 人声'),
                ]

            for stem_key, stem_label in stems_to_convert:
                wav_path = result.get(stem_key)
                if not wav_path:
                    continue
                out_path = self.output_dir / f'{stem_label}{ext}'
                if convert_audio_file(wav_path, str(out_path), self.output_format, self.mp3_quality, self.log_message.emit):
                    produced.append(str(out_path))
                    self.log_message.emit(f"  -> {out_path.name}")

            return produced
        finally:
            # 清理临时 Demucs 输出
            import shutil
            try:
                shutil.rmtree(tmp_sep, ignore_errors=True)
            except:
                pass

    async def process_file(self, file_path: Path) -> Tuple[bool, str, Optional[str]]:
        try:
            self.file_started.emit(file_path.name)
            is_ncm = file_path.suffix.lower() == '.ncm'
            ext = self.get_output_extension()

            if is_ncm:
                self.log_message.emit(f"Decrypting: {file_path.name}")
                def on_progress(processed, total):
                    self.file_progress.emit(file_path.name, processed, total)
                def is_cancelled():
                    return not self.is_running
                decrypter = NCMDecrypter(str(file_path))
                audio_data, metadata, cover_data, audio_format = decrypter.decrypt(on_progress, is_cancelled)
                self.log_message.emit(f"Converting to {self.output_format}: {file_path.name}")
                if metadata and 'musicName' in metadata and 'artist' in metadata:
                    artists = metadata['artist']
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
                    output_name = f"{artist_str} - {metadata['musicName']}{ext}"
                    output_name = "".join(c for c in output_name if c not in '<>:"/\|?*')
                else:
                    output_name = file_path.stem + ext
                output_path = self.output_dir / output_name
                success = await self.convert_audio(audio_data, audio_format, output_path)
                if success and metadata:
                    self.write_metadata(output_path, metadata, cover_data)
            else:
                output_name = file_path.stem + ext
                output_path = self.output_dir / output_name
                self.log_message.emit(f"Converting: {file_path.name} -> {output_name}")
                ffmpeg_path = find_ffmpeg()
                if not ffmpeg_path:
                    self.log_message.emit("ERROR: ffmpeg not found!")
                    return False, file_path.name, None
                args = ['-c:a', 'flac', '-compression_level', '8'] if self.output_format == 'FLAC' else MP3_QUALITY[self.mp3_quality]
                cmd = [ffmpeg_path, '-y', '-i', str(file_path)] + args + ['-v', 'error', str(output_path)]
                process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = await process.communicate()
                success = process.returncode == 0
                if not success:
                    self.log_message.emit(f"FFmpeg error: {stderr.decode('utf-8', errors='ignore')[:200]}")
                metadata = {}
                cover_data = None

            if success:
                self.log_message.emit(f"Success: {file_path.name} -> {output_name}")
                if self.separation_mode != '不分离':
                    output_stem = output_name.rsplit('.', 1)[0]
                    self.do_separation(output_path, output_stem)
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

# ── 拖拽列表控件 ──────────────────────────────────
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
                    if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
                        files.append(path)
                    elif path.is_dir():
                        for ext in AUDIO_EXTENSIONS:
                            files.extend(path.rglob(f'*{ext}'))
            self.files_dropped.emit(files)
        else:
            super().dropEvent(event)

# ── 主窗口 ────────────────────────────────────────
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
        self.setWindowTitle("Universal Audio Converter (NCM/FLAC/MP3/WAV/M4A/OGG)")
        self.setGeometry(100, 100, 800, 680)
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # ── 目录选择 ──
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

        # ── 设置 ──
        settings_group = QGroupBox("Settings")
        settings_layout = QHBoxLayout(settings_group)

        settings_layout.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        self.format_combo.addItem("FLAC - 无损")
        self.format_combo.addItem("MP3 - 有损")
        self.format_combo.currentIndexChanged.connect(self.on_format_changed)
        settings_layout.addWidget(self.format_combo)

        settings_layout.addWidget(QLabel("MP3:"))
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("高 (320kbps)")
        self.quality_combo.addItem("中 (192kbps)")
        self.quality_combo.addItem("低 (128kbps)")
        settings_layout.addWidget(self.quality_combo)

        settings_layout.addWidget(QLabel("Concurrent:"))
        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 8)
        self.concurrent_spin.setValue(2)
        settings_layout.addWidget(self.concurrent_spin)

        settings_layout.addStretch()
        self.add_files_btn = QPushButton("Add Audio Files")
        self.add_files_btn.clicked.connect(self.add_files)
        settings_layout.addWidget(self.add_files_btn)
        self.clear_files_btn = QPushButton("Clear List")
        self.clear_files_btn.clicked.connect(self.clear_files)
        settings_layout.addWidget(self.clear_files_btn)
        main_layout.addWidget(settings_group)

        # ── 人声分离 ──
        sep_group = QGroupBox("Vocal Separation (powered by Demucs AI)")
        sep_layout = QHBoxLayout(sep_group)
        sep_layout.addWidget(QLabel("Mode:"))
        self.sep_combo = QComboBox()
        for mode in SEPARATION_MODES:
            self.sep_combo.addItem(mode)
        sep_layout.addWidget(self.sep_combo)
        sep_layout.addStretch()
        sep_layout.addWidget(QLabel("⚠ CPU mode, 1 min per min of audio"))
        main_layout.addWidget(sep_group)

        # ── 文件列表 ──
        list_group = QGroupBox("Files to Convert (0 files)")
        list_layout = QVBoxLayout(list_group)
        self.file_list = DropListWidget()
        self.file_list.files_dropped.connect(self.add_dropped_files)
        list_layout.addWidget(self.file_list)
        main_layout.addWidget(list_group)

        # ── 进度 ──
        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout(progress_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setAlignment(Qt.AlignCenter)
        progress_layout.addWidget(self.progress_bar)
        self.status_label = QLabel("Ready")
        progress_layout.addWidget(self.status_label)
        main_layout.addWidget(progress_group)

        # ── 日志 ──
        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        log_layout.addWidget(self.log_text)
        main_layout.addWidget(log_group)

        # ── 按钮 ──
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

        self.quality_combo.setEnabled(False)

    def on_format_changed(self, index):
        self.quality_combo.setEnabled(index == 1)

    def get_selected_format(self) -> str:
        return 'MP3' if self.format_combo.currentIndex() == 1 else 'FLAC'

    def get_selected_quality(self) -> str:
        return self.quality_combo.currentText()

    def get_selected_separation(self) -> str:
        return self.sep_combo.currentText()

    def load_settings(self):
        self.input_path_edit.setText(self.settings.value("input_dir", ""))
        self.output_path_edit.setText(self.settings.value("output_dir", ""))
        self.concurrent_spin.setValue(self.settings.value("max_concurrent", 2, type=int))
        if self.settings.value("output_format", "FLAC") == 'MP3':
            self.format_combo.setCurrentIndex(1)
        idx = self.quality_combo.findText(self.settings.value("mp3_quality", "高 (320kbps)"))
        if idx >= 0:
            self.quality_combo.setCurrentIndex(idx)
        sep_idx = self.sep_combo.findText(self.settings.value("separation_mode", "不分离"))
        if sep_idx >= 0:
            self.sep_combo.setCurrentIndex(sep_idx)

    def save_settings(self):
        self.settings.setValue("input_dir", self.input_path_edit.text())
        self.settings.setValue("output_dir", self.output_path_edit.text())
        self.settings.setValue("max_concurrent", self.concurrent_spin.value())
        self.settings.setValue("output_format", self.get_selected_format())
        self.settings.setValue("mp3_quality", self.get_selected_quality())
        self.settings.setValue("separation_mode", self.get_selected_separation())

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
            for ext in AUDIO_EXTENSIONS:
                self.add_files_to_list(list(input_dir.glob(f'*{ext}')))

    def add_files(self):
        ext_list = ' '.join(f'*{e}' for e in sorted(AUDIO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(self, "Select Audio Files", "",
            f"All Audio ({ext_list});;NCM Files (*.ncm);;WAV (*.wav);;FLAC (*.flac);;MP3 (*.mp3);;M4A (*.m4a);;OGG (*.ogg);;All Files (*.*)")
        if files:
            self.add_files_to_list([Path(f) for f in files])

    def add_files_to_list(self, files):
        for f in files:
            if f.suffix.lower() in AUDIO_EXTENSIONS and f not in self.input_files:
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
            QMessageBox.warning(self, "Warning", "No audio files selected!")
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
        sep = self.get_selected_separation()
        self.status_label.setText(f"Converting to {fmt}..." + (" + vocal separation" if sep != '不分离' else ""))
        self.thread = QThread()
        self.worker = ConversionWorker(
            self.input_files.copy(), output_dir,
            self.concurrent_spin.value(), fmt, quality, sep,
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
        sep = self.get_selected_separation()
        msg = f"Completed: {success_count}/{total} succeeded, {fail_count} failed ({fmt})"
        if sep != '不分离':
            msg += " + vocal separation"
        self.status_label.setText(msg)
        QMessageBox.information(self, "Conversion Complete",
            f"Conversion finished!\n\nFormat: {fmt}\nVocal Separation: {sep}\nSuccess: {success_count}\nFailed: {fail_count}")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
