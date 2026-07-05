# Universal Audio Converter

通用音频格式转换工具，支持 NCM 解密 + 主流格式互转 + AI 人声分离，带 PyQt5 图形界面，开箱即用。

## 功能

- ✅ 完整 NCM 解密（AES-ECB + RC4）
- ✅ **支持 13 种输入格式**：NCM / WAV / FLAC / MP3 / M4A / OGG / WMA / AAC / Opus / APE / WV / AIFF / AIF
- ✅ **输出 FLAC（无损）或 MP3（320/192/128kbps 可选）**
- ✅ **AI 人声分离** — 基于 Demucs（Meta），可分离人声/伴奏
- ✅ 异步批量转换，并发数可调
- ✅ 实时进度显示（单文件解密进度 + 总体进度）
- ✅ 支持拖拽文件/文件夹
- ✅ 支持中途停止
- ✅ 设置自动保存
- ✅ 独立 EXE 打包，无需 Python 环境

## 支持的格式

| 输入 | 输出 |
|------|------|
| NCM（加密） | FLAC / MP3 |
| WAV / FLAC / MP3 | FLAC / MP3 |
| M4A / OGG / WMA / AAC | FLAC / MP3 |
| Opus / APE / WV / AIFF | FLAC / MP3 |

## 输出格式

| 格式 | 质量选项 | 说明 |
|------|----------|------|
| FLAC | 无损 | 压缩级别 8，文件最小 |
| MP3 | 高 (320kbps) | 接近 CD 音质 |
| MP3 | 中 (192kbps) | 平衡质量与大小 |
| MP3 | 低 (128kbps) | 节省空间 |

## AI 人声分离（v2.0 主打）

本工具搭载 **Meta 的 Demucs v4 (htdemucs)** 混合神经网络模型，实现高质量音源分离。

### 技术原理

Demucs（v4 "hybrid" 版本）融合了两大架构：

| 架构 | 作用 |
|------|------|
| **卷积编码器（CNN）** | 分析波形局部特征（打击乐节奏、人声齿音） |
| **Transformer 注意力层** | 捕捉全局依赖（旋律走向、和声结构） |
| **频域分支** | 在频谱图上做掩码（mask），分离重叠频率的人声和乐器 |

模型将音频分解为 4 个独立音轨：**人声、鼓、贝斯、其他**。选择"分离伴奏"时，程序将鼓+贝斯+其他三轨合成为完整伴奏，丢弃人声轨。

### 硬件与性能

- **完全本地运行**，不上传任何音频数据
- **支持 CPU**（本机 AMD Ryzen 9 6900HX 实测约 1.5x 实时处理）
- 首次运行时自动下载模型（~80MB）
- 建议 8GB+ 内存

### 分离模式

| 模式 | 效果 |
|------|------|
| 不分离 | 只做格式转换 |
| 分离伴奏（去人声） | 输出干净的伴奏音轨 |
| 分离人声（去伴奏） | 输出清唱音轨 |
| 分离两轨（同时保留） | 人声 + 伴奏各一轨 |

## 快速开始

### 方式一：直接运行 EXE

1. 从 [Releases](https://github.com/wshen-ai/conver2flac/releases) 下载最新版
2. 解压后双击 `NCM2FLAC.exe`（确保 `ffmpeg.exe` 在同一目录）
3. 拖入任意音频文件，选择输出格式和码率

### 方式二：源码运行

```bash
pip install -r requirements.txt
python ncm2flac_gui.py
```

### 方式三：源码打包 EXE

```bash
# 1. 准备 ffmpeg（必需）
#    从 https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip 下载
#    解压后将 bin/ffmpeg.exe 放到项目根目录

# 2. 安装打包工具
pip install pyinstaller

# 3. 打包
python -m PyInstaller \
    --clean --onefile --windowed \
    --name "NCM2FLAC" \
    --runtime-hook hook-torch-preload.py \
    --add-binary "ffmpeg.exe;." \
    --collect-data demucs \
    --collect-all numpy \
    --hidden-import PyQt5.QtCore \
    --hidden-import PyQt5.QtGui \
    --hidden-import PyQt5.QtWidgets \
    --hidden-import Crypto.Cipher.AES \
    --hidden-import Crypto.Util.Padding \
    --hidden-import mutagen.flac \
    --hidden-import mutagen.mp3 \
    --hidden-import mutagen.id3 \
    ncm2flac_gui.py
```

> **各参数说明：**
> - `--add-binary "ffmpeg.exe;."` — 将 ffmpeg 嵌入 EXE，用户无需单独安装
> - `--collect-data demucs` — 打包 Meta Demucs 模型数据文件（否则 AI 分离报错）
> - `--collect-all numpy` — 打包 numpy 所有 C 扩展（否则 `multiarray` 找不到）
> - `--runtime-hook hook-torch-preload.py` — **必须**，修复 Windows 上 c10.dll 崩溃（WinError 1114）

产物在 `dist/NCM2FLAC.exe`。

或直接跑：

```bat
BUILD_EXE.bat
```

脚本会自动检测系统 ffmpeg 并复制到 `dist\` 目录。**EXE 运行时需要同目录有 ffmpeg.exe，或系统 PATH 可找到 ffmpeg。**

## 技术栈

- **GUI**: PyQt5
- **NCM 解密**: pycryptodome (AES-ECB + RC4)
- **转码**: FFmpeg
- **AI 人声分离**: Demucs (Meta) + PyTorch
- **元数据**: mutagen（FLAC 原生标签 / MP3 ID3 标签）
- **打包**: PyInstaller

## 依赖

- Python 3.10+
- FFmpeg（EXE 版本已捆绑，源码运行需安装到 PATH）
- Demucs 模型首次运行自动下载（~80MB）
