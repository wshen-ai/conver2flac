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
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QSpinBox, QProgressBar,
    QTextEdit, QFileDialog, QGroupBox, QMessageBox, QListWidget,
    QListWidgetItem, QAbstractItemView, QSplitter
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, pyqtSlot, QObject, QSettings
)
from PyQt5.QtGui import QDragEnterEvent, QDropEvent

# NCM格式常量
CORE_KEY = bytes.fromhex('687A4852416D736F356B496E62617857')
META_KEY = bytes.fromhex('2331346C6A6B5F215C5D2630553C2728')

def find_ffmpeg() -> str:
    """查找 ffmpeg.exe：优先 EXE 同目录，其次 PATH"""
    # 1. PyInstaller 打包后，EXE 同目录
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
    else:
        exe_dir = Path(__file__).parent
    
    local_ffmpeg = exe_dir / 'ffmpeg.exe'
    if local_ffmpeg.exists():
        return str(local_ffmpeg)
    
    # 2. 系统 PATH
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
        """读取指定字节数"""
        with open(self.file_path, 'rb') as f:
            f.seek(self.offset)
            data = f.read(count)
            self.offset += count
            return data
    
    def decrypt(self, progress_cb=None, is_cancelled=None) -> Tuple[bytes, dict, bytes, str]:
        """解密整个NCM文件"""
        # 验证文件头
        header = self.read_bytes(8)
        if header != b'CTENFDAM':
            raise ValueError(f"File {self.file_path} is not a valid NCM format")
        
        # 跳过2字节
        self.offset += 2
        
        # 读取RC4密钥长度
        key_len = struct.unpack('<I', self.read_bytes(4))[0]
        encrypted_key = self.read_bytes(key_len)
        
        # 解密RC4密钥
        encrypted_key = bytes(byte ^ 0x64 for byte in encrypted_key)
        cipher = AES.new(CORE_KEY, AES.MODE_ECB)
        decrypted_key = unpad(cipher.decrypt(encrypted_key), AES.block_size)
        rc4_key = decrypted_key[17:]
        
        # 生成密钥盒
        self.key_box = self._generate_rc4_key_box(rc4_key)
        
        # 读取metadata长度
        meta_len = struct.unpack('<I', self.read_bytes(4))[0]
        if meta_len > 0:
            encrypted_meta = self.read_bytes(meta_len)
            encrypted_meta = bytes(byte ^ 0x63 for byte in encrypted_meta)
            encrypted_meta = encrypted_meta[22:]  # 跳过"163 key(Don't modify):"
            
            # base64解码
            import base64
            encrypted_meta = base64.b64decode(encrypted_meta)
            
            # 解密metadata
            cipher = AES.new(META_KEY, AES.MODE_ECB)
            decrypted_meta = unpad(cipher.decrypt(encrypted_meta), AES.block_size)
            decrypted_meta = decrypted_meta[6:]  # 跳过"music:"
            self.metadata = json.loads(decrypted_meta.decode('utf-8'))
            self.audio_format = self.metadata.get('format', 'mp3')
        else:
            self.metadata = {}
            self.audio_format = 'mp3'
        
        # 跳过CRC32和5个未知字节
        self.offset += 4 + 5
        
        # 读取封面长度
        cover_len = struct.unpack('<I', self.read_bytes(4))[0]
        if cover_len > 0:
            self.cover_data = self.read_bytes(cover_len)
        
        # 解密音频数据
        self.audio_data = self._decrypt_audio_data(progress_cb, is_cancelled)
        
        return self.audio_data, self.metadata, self.cover_data, self.audio_format
    
    def _generate_rc4_key_box(self, key: bytes) -> List[int]:
        """生成RC4密钥盒"""
        key_len = len(key)
        box = list(range(256))
        j = 0
        for i in range(256):
            j = (j + box[i] + key[i % key_len]) % 256
            box[i], box[j] = box[j], box[i]
        return box
    
    def _decrypt_audio_data(self, progress_cb=None, is_cancelled=None) -> bytes:
        """解密音频数据，支持进度回调和取消检查"""
        audio_data = bytearray()
        chunk_size = 0x8000
        
        # 估算总音频数据大小
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
    """转换工作线程"""
    progress_updated = pyqtSignal(int, int)  # 当前完成, 总数
    file_progress = pyqtSignal(str, int, int)  # 文件名, 当前字节, 总字节
    file_started = pyqtSignal(str)  # 文件名
    file_finished = pyqtSignal(str, bool, str)  # 文件名, 成功, 输出路径
    log_message = pyqtSignal(str)
    finished = pyqtSignal(int, int)  # 成功数, 失败数
    
    def __init__(self, input_files: List[Path], output_dir: Path, max_concurrent: int):
        super().__init__()
        self.input_files = input_files
        self.output_dir = output_dir
        self.max_concurrent = max_concurrent
        self.is_running = True
        
    async def convert_to_flac(self, input_data: bytes, input_format: str, output_path: Path) -> bool:
        """转换为FLAC"""
        try:
            # 如果原始格式已经是FLAC，直接写入
            if input_format == 'flac':
                with open(output_path, 'wb') as f:
                    f.write(input_data)
                return True
            
            # 创建临时文件
            temp_file = output_path.with_suffix(f'.{input_format}')
            with open(temp_file, 'wb') as f:
                f.write(input_data)
            
            # 构建ffmpeg命令
            ffmpeg_path = find_ffmpeg()
            if not ffmpeg_path:
                self.log_message.emit("ERROR: ffmpeg not found! Please place ffmpeg.exe in the same folder as this program.")
                return False
            cmd = [
                ffmpeg_path,
                '-y',
                '-i', str(temp_file),
                '-c:a', 'flac',
                '-compression_level', '8',
                '-v', 'error',
                str(output_path)
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            # 删除临时文件
            temp_file.unlink()
            
            if process.returncode != 0:
                error_msg = stderr.decode('utf-8', errors='ignore')
                self.log_message.emit(f"FFmpeg error: {error_msg}")
                return False
            
            return True
            
        except Exception as e:
            self.log_message.emit(f"Conversion error: {str(e)}")
            return False
    
    async def process_file(self, file_path: Path) -> Tuple[bool, str, Optional[str]]:
        """处理单个文件"""
        try:
            self.file_started.emit(file_path.name)
            self.log_message.emit(f"Decrypting: {file_path.name}")
            
            # 解密（带进度和取消检查）
            def on_progress(processed, total):
                self.file_progress.emit(file_path.name, processed, total)
            def is_cancelled():
                return not self.is_running
            
            decrypter = NCMDecrypter(str(file_path))
            audio_data, metadata, cover_data, audio_format = decrypter.decrypt(on_progress, is_cancelled)
            
            self.log_message.emit(f"Converting: {file_path.name}")
            
            # 生成输出文件名
            if metadata and 'musicName' in metadata and 'artist' in metadata:
                # artist 格式: [['name', id], ['name2', id2], ...]
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
                output_name = f"{artist_str} - {metadata['musicName']}.flac"
                # 处理文件名中的非法字符
                output_name = "".join(c for c in output_name if c not in '<>:"/\\|?*')
            else:
                output_name = file_path.stem + '.flac'
            
            output_path = self.output_dir / output_name
            
            # 转换
            success = await self.convert_to_flac(audio_data, audio_format, output_path)
            
            if success:
                # 写入元数据
                if metadata:
                    audio = FLAC(output_path)
                    audio['title'] = metadata.get('musicName', '')
                    # artist 格式兼容: [['name',id],...] 或 [{'name':'...'},...]
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
                    
                    # 写入封面
                    if cover_data:
                        pic = Picture()
                        pic.data = cover_data
                        pic.type = 3
                        pic.mime = 'image/jpeg'
                        audio.add_picture(pic)
                    
                    audio.save()
                
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
        """处理所有文件"""
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
        """运行转换任务"""
        asyncio.run(self.process_files())
    
    def stop(self):
        """停止转换"""
        self.is_running = False

class DropListWidget(QListWidget):
    """支持拖拽的列表控件"""
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
                        # 递归查找目录下的ncm文件
                        files.extend(path.rglob('*.ncm'))
            
            self.files_dropped.emit(files)
        else:
            super().dropEvent(event)

class MainWindow(QMainWindow):
    """主窗口"""
    def __init__(self):
        super().__init__()
        self.settings = QSettings("NCM2FLAC", "Converter")
        self.worker = None
        self.thread = None
        self.input_files = []
        
        self.init_ui()
        self.load_settings()
        
    def init_ui(self):
        """初始化界面"""
        self.setWindowTitle("NCM to FLAC Converter")
        self.setGeometry(100, 100, 800, 600)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # 目录选择区域
        dir_group = QGroupBox("Directories")
        dir_layout = QVBoxLayout(dir_group)
        
        # 输入目录
        input_layout = QHBoxLayout()
        input_layout.addWidget(QLabel("Input Directory:"))
        self.input_path_edit = QLineEdit()
        input_layout.addWidget(self.input_path_edit)
        self.browse_input_btn = QPushButton("Browse")
        self.browse_input_btn.clicked.connect(self.browse_input)
        input_layout.addWidget(self.browse_input_btn)
        dir_layout.addLayout(input_layout)
        
        # 输出目录
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
        
        settings_layout.addWidget(QLabel("Max Concurrent Tasks:"))
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
        list_group = QGroupBox(f"Files to Convert (0 files)")
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
        self.start_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 16px;")
        btn_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_conversion)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 8px 16px;")
        btn_layout.addWidget(self.stop_btn)
        
        main_layout.addLayout(btn_layout)
        
    def load_settings(self):
        """加载保存的设置"""
        input_dir = self.settings.value("input_dir", str(Path.cwd() / "input"))
        output_dir = self.settings.value("output_dir", str(Path.cwd() / "output"))
        concurrent = self.settings.value("concurrent", 3, type=int)
        
        self.input_path_edit.setText(input_dir)
        self.output_path_edit.setText(output_dir)
        self.concurrent_spin.setValue(concurrent)
        
        # 自动扫描输入目录的ncm文件
        self.scan_input_directory()
    
    def save_settings(self):
        """保存设置"""
        self.settings.setValue("input_dir", self.input_path_edit.text())
        self.settings.setValue("output_dir", self.output_path_edit.text())
        self.settings.setValue("concurrent", self.concurrent_spin.value())
    
    def browse_input(self):
        """选择输入目录"""
        dir_path = QFileDialog.getExistingDirectory(self, "Select Input Directory", self.input_path_edit.text())
        if dir_path:
            self.input_path_edit.setText(dir_path)
            self.scan_input_directory()
            self.save_settings()
    
    def browse_output(self):
        """选择输出目录"""
        dir_path = QFileDialog.getExistingDirectory(self, "Select Output Directory", self.output_path_edit.text())
        if dir_path:
            self.output_path_edit.setText(dir_path)
            self.save_settings()
    
    def scan_input_directory(self):
        """扫描输入目录的ncm文件"""
        input_dir = Path(self.input_path_edit.text())
        if input_dir.exists() and input_dir.is_dir():
            ncm_files = list(input_dir.glob('*.ncm'))
            self.add_files_to_list(ncm_files)
    
    def add_files(self):
        """手动添加文件"""
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select NCM Files", "", "NCM Files (*.ncm)"
        )
        if files:
            self.add_files_to_list([Path(f) for f in files])
    
    def add_dropped_files(self, files):
        """添加拖拽的文件"""
        self.add_files_to_list(files)
    
    def add_files_to_list(self, files: List[Path]):
        """添加文件到列表"""
        existing_paths = [item.data(Qt.UserRole) for item in self.file_list.findItems("*", Qt.MatchWildcard)]
        
        for file in files:
            if str(file) not in existing_paths:
                item = QListWidgetItem(file.name)
                item.setData(Qt.UserRole, str(file))
                self.file_list.addItem(item)
                existing_paths.append(str(file))
        
        self.update_file_count()
    
    def clear_files(self):
        """清空文件列表"""
        self.file_list.clear()
        self.update_file_count()
    
    def update_file_count(self):
        """更新文件计数"""
        count = self.file_list.count()
        self.file_list.parent().setTitle(f"Files to Convert ({count} files)")
        self.input_files = [Path(item.data(Qt.UserRole)) for item in self.file_list.findItems("*", Qt.MatchWildcard)]
    
    def log(self, message: str):
        """添加日志"""
        self.log_text.append(message)
        # 滚动到底部
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    @pyqtSlot(int, int)
    def update_progress(self, completed: int, total: int):
        """更新进度条"""
        self.progress_bar.setValue(int(completed / total * 100))
        self.status_label.setText(f"Processing: {completed}/{total} files")
    
    @pyqtSlot(str, int, int)
    def on_file_progress(self, filename: str, processed: int, total: int):
        """更新单文件解密进度"""
        pct = int(processed / total * 100) if total > 0 else 0
        self.status_label.setText(f"Decrypting {filename}: {pct}% ({processed//1048576}MB / {total//1048576}MB)")
    
    @pyqtSlot(str)
    def on_file_started(self, filename: str):
        """文件开始处理"""
        # 查找对应的列表项并更新状态
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item.text() == filename:
                item.setForeground(Qt.yellow)
                break
    
    @pyqtSlot(str, bool, str)
    def on_file_finished(self, filename: str, success: bool, output_path: str):
        """文件处理完成"""
        # 查找对应的列表项并更新状态
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item.text() == filename:
                if success:
                    item.setForeground(Qt.green)
                else:
                    item.setForeground(Qt.red)
                break
    
    @pyqtSlot(str)
    def on_log_message(self, message: str):
        """处理日志消息"""
        self.log(message)
    
    @pyqtSlot(int, int)
    def on_conversion_finished(self, success_count: int, fail_count: int):
        """转换完成"""
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.browse_input_btn.setEnabled(True)
        self.browse_output_btn.setEnabled(True)
        self.add_files_btn.setEnabled(True)
        self.clear_files_btn.setEnabled(True)
        self.concurrent_spin.setEnabled(True)
        
        self.status_label.setText(f"Completed: {success_count} succeeded, {fail_count} failed")
        self.progress_bar.setValue(100)
        
        QMessageBox.information(
            self,
            "Conversion Complete",
            f"Successfully converted {success_count} files\nFailed: {fail_count} files"
        )
    
    def start_conversion(self):
        """开始转换"""
        if self.file_list.count() == 0:
            QMessageBox.warning(self, "Warning", "No NCM files selected!")
            return
        
        output_dir = Path(self.output_path_edit.text())
        if not output_dir.exists():
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Cannot create output directory: {str(e)}")
                return
        
        # 保存设置
        self.save_settings()
        
        # 获取文件列表
        self.input_files = [Path(item.data(Qt.UserRole)) for item in self.file_list.findItems("*", Qt.MatchWildcard)]
        
        # 重置界面状态
        self.progress_bar.setValue(0)
        self.log_text.clear()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.browse_input_btn.setEnabled(False)
        self.browse_output_btn.setEnabled(False)
        self.add_files_btn.setEnabled(False)
        self.clear_files_btn.setEnabled(False)
        self.concurrent_spin.setEnabled(False)
        
        # 创建工作线程
        self.thread = QThread()
        self.worker = ConversionWorker(
            self.input_files,
            output_dir,
            self.concurrent_spin.value()
        )
        
        # 连接信号
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.file_progress.connect(self.on_file_progress)
        self.worker.file_started.connect(self.on_file_started)
        self.worker.file_finished.connect(self.on_file_finished)
        self.worker.log_message.connect(self.on_log_message)
        self.worker.finished.connect(self.on_conversion_finished)
        self.worker.moveToThread(self.thread)
        
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        
        self.thread.start()
        self.log("Conversion started...")
    
    def stop_conversion(self):
        """停止转换"""
        if self.worker:
            self.worker.stop()
            self.log("Stopping conversion...")
            self.status_label.setText("Stopping...")
            self.stop_btn.setEnabled(False)
    
    def closeEvent(self, event):
        """关闭事件"""
        if self.thread and self.thread.isRunning():
            reply = QMessageBox.question(
                self,
                "Confirm Exit",
                "Conversion is in progress. Are you sure you want to exit?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                self.worker.stop()
                self.thread.wait()
                event.accept()
            else:
                event.ignore()
        else:
            self.save_settings()
            event.accept()

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()