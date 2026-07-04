@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ==============================================
echo        NCM2FLAC - Standalone EXE Builder
echo      (with torch preload DLL hook)
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
rmdir /s /q build dist 2>nul
del /q NCM2FLAC*.spec 2>nul

:: 打包 EXE（--windowed 无控制台窗口，含 torch DLL 预加载钩子）
echo.
echo Building NCM2FLAC.exe...
%PYTHON_EXE% -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "NCM2FLAC" ^
    --runtime-hook hook-torch-preload.py ^
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
echo To use on another machine:
echo   1. Copy the entire "dist" folder
echo   2. Double-click NCM2FLAC.exe
echo.
pause
