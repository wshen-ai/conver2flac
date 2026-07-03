# NCM to FLAC/MP3 Converter

NCM 加密音频格式转 FLAC/MP3 工具，带 PyQt5 图形界面，开箱即用。

## 功能

- ✅ 完整 NCM 解密（AES-ECB + RC4）
- ✅ 自动提取元数据（歌名、艺术家、专辑、封面）
- ✅ **支持输出 FLAC（无损）和 MP3（320/192/128kbps 可选）**
- ✅ 异步批量转换，并发数可调
- ✅ 实时进度显示（单文件解密进度 + 总体进度）
- ✅ 支持拖拽文件/文件夹
- ✅ 支持中途停止
- ✅ 设置自动保存
- ✅ 独立 EXE 打包，无需 Python 环境

## 输出格式

| 格式 | 质量选项 | 说明 |
|------|----------|------|
| FLAC | 无损 | 压缩级别 8，文件最小 |
| MP3 | 高 (320kbps) | 接近 CD 音质 |
| MP3 | 中 (192kbps) | 平衡质量与大小 |
| MP3 | 低 (128kbps) | 节省空间 |

## 快速开始

### 方式一：直接运行 EXE

1. 从 [Releases](https://github.com/wshen-ai/conver2flac/releases) 下载最新版
2. 解压后双击 `NCM2FLAC.exe`（确保 `ffmpeg.exe` 在同一目录）
3. 选择输入/输出目录，选择输出格式（FLAC/MP3）和 MP3 码率
4. 点击 Start Conversion

### 方式二：源码运行

```bash
pip install -r requirements.txt
python ncm2flac_gui.py
```

### 方式三：自行打包 EXE

```bash
# 双击 BUILD_EXE.bat 即可
# 打包后 dist/ 目录下会生成 NCM2FLAC.exe + ffmpeg.exe
```

## 项目结构

```
conver2flac/
├── ncm2flac_gui.py      # 主程序（PyQt5 图形界面）
├── requirements.txt     # Python 依赖
├── BUILD_EXE.bat        # 一键打包脚本
├── .gitignore
└── dist/                # 打包输出目录
    ├── NCM2FLAC.exe     # 独立可执行文件
    └── ffmpeg.exe       # 转码引擎
```

## 技术栈

- **GUI**: PyQt5
- **解密**: pycryptodome (AES-ECB + RC4)
- **转码**: FFmpeg
- **元数据**: mutagen（FLAC 原生标签 / MP3 ID3 标签）
- **打包**: PyInstaller

## 依赖

- Python 3.10+
- FFmpeg（EXE 版本已捆绑，源码运行需安装到 PATH）
