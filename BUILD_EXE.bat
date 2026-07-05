@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ==============================================
echo        NCM2FLAC - Standalone EXE Builder
echo    (torch DLL fix + ffmpeg + Demucs + numpy)
echo ==============================================
echo.

:: 找系统 Python（带 pip 的）
set "PYTHON_EXE="
for %%p in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
) do (
    if exist %%p (
        set "PYTHON_EXE=%%~p"
        goto :found_python
    )
)
echo [ERROR] Cannot find system Python with pip!
echo Install Python from https://www.python.org/downloads/
pause
exit /b 1

:found_python
echo Python found: %PYTHON_EXE%

:: 检查 ffmpeg.exe
echo.
if not exist "ffmpeg.exe" (
    echo [WARNING] ffmpeg.exe not found in project root!
    echo Download from: https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
    echo Extract bin\ffmpeg.exe to this directory, then re-run.
    pause
    exit /b 1
)
echo ffmpeg.exe found.

:: 安装 PyInstaller
echo.
echo Installing PyInstaller...
%PYTHON_EXE% -m pip install pyinstaller --quiet
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install PyInstaller!
    pause
    exit /b 1
)

:: 清理旧构建
echo.
echo Cleaning old builds...
rmdir /s /q build 2>nul
del /q NCM2FLAC*.spec 2>nul

:: 打包 EXE
echo.
echo Building NCM2FLAC.exe...
%PYTHON_EXE% -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "NCM2FLAC" ^
    --runtime-hook hook-torch-preload.py ^
    --add-binary "ffmpeg.exe;." ^
    --collect-data demucs ^
    --collect-all numpy ^
    --hidden-import PyQt5 ^
    --hidden-import PyQt5.QtCore ^
    --hidden-import PyQt5.QtGui ^
    --hidden-import PyQt5.QtWidgets ^
    --hidden-import Crypto.Cipher.AES ^
    --hidden-import Crypto.Util.Padding ^
    --hidden-import mutagen.flac ^
    --hidden-import mutagen.mp3 ^
    --hidden-import mutagen.id3 ^
    ncm2flac_gui.py

if %errorlevel% neq 0 (
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)

echo.
echo ==============================================
echo   Build complete!
echo   EXE location: dist\NCM2FLAC.exe
echo ==============================================
echo.
echo To release:
echo   1. Call gh release create vX.X dist\NCM2FLAC.exe
echo   2. Or drag into GitHub Release page in browser
echo.
pause
