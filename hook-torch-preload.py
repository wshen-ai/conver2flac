# PyInstaller runtime hook — force preload torch DLLs via Windows API
# The Python-level os.add_dll_directory() is too late — PyInstaller's
# built-in PyQt5 hooks already loaded conflicting Qt DLLs by then.
# We go straight to Win32 LoadLibraryEx to grab c10.dll first.

import os
import sys
import ctypes

_torch_dir = os.path.join(sys._MEIPASS, "torch", "lib")
if not os.path.isdir(_torch_dir):
    # Not a PyInstaller bundle — nothing to do
    pass
else:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008
    # Tells Windows: when loading this DLL, search ITS directory first for dependencies
    LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008

    # Preload c10.dll — the one that triggers WinError 1114
    c10_path = os.path.join(_torch_dir, "c10.dll")
    try:
        kernel32.LoadLibraryExW(c10_path, None, LOAD_WITH_ALTERED_SEARCH_PATH)
    except OSError:
        pass  # Will be caught by torch's own _load_dll_libraries later with better error

    # Also set the DLL directory path as a fallback
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(_torch_dir)
        except OSError:
            pass
