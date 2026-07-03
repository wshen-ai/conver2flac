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

## AI 人声分离

基于 Meta 的 Demucs 模型（htdemucs），在 CPU 上运行（约 1 分钟处理 1 分钟音频）。

| 模式 | 效果 |
|------|------|
| 不分离 | 只做格式转换 |
| 分离伴奏（去人声） | 输出伴奏音轨 |
| 分离人声（去伴奏） | 输出清唱音轨 |
| 分离人声+伴奏（两轨都保留） | 同时输出人声和伴奏 |

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
