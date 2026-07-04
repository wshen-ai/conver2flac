# PyInstaller runtime hook — preload torch DLLs before PyQt5
# Fixes: OSError [WinError 1114] c10.dll initialization failure on Windows
import os
import sys

# Prepend torch/lib to PATH so c10.dll and its deps load before PyQt5's Qt5/bin
_torch_dir = os.path.join(sys._MEIPASS, "torch", "lib")
if os.path.isdir(_torch_dir):
    os.add_dll_directory(_torch_dir)
    os.environ["PATH"] = _torch_dir + os.pathsep + os.environ.get("PATH", "")
