@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ==============================================
echo        NCM2FLAC - Standalone EXE Builder
echo ==============================================
echo.

:: 找系统 Python（带 pip 的，不是 hermes venv）
set "PYTHON_EXE="
for %%p in (
    "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
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
del /q NCM2FLAC.spec 2>nul

:: 打包 EXE（--windowed 无控制台窗口）
echo.
echo Building NCM2FLAC.exe...
%PYTHON_EXE% -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "NCM2FLAC" ^
    --add-data "ncm2flac.py;." ^
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

:: 查找 ffmpeg.exe
set "FFMPEG_SRC="
for %%f in (
    "%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-*\bin\ffmpeg.exe"
    "C:\ffmpeg\bin\ffmpeg.exe"
    "%ProgramFiles%\ffmpeg\bin\ffmpeg.exe"
) do (
    if exist "%%f" (
        set "FFMPEG_SRC=%%~dpf"
        goto :found_ffmpeg
    )
)
echo.
echo [WARNING] ffmpeg.exe not found! EXE will be built without ffmpeg.
echo The converter will still work if ffmpeg is installed on the target machine.
echo.
goto :done

:found_ffmpeg
echo.
echo Copying ffmpeg to dist folder...
copy /y "%FFMPEG_SRC%ffmpeg.exe" dist\ >nul
copy /y "%FFMPEG_SRC%ffprobe.exe" dist\ >nul 2>nul
echo ffmpeg.exe copied to dist\

:done
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
