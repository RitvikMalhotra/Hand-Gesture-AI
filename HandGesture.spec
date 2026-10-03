# PyInstaller one-folder build of the desktop UI, see README for the command.

import os

from PIL import Image
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# Remade from the PNG on every build so the exe icon can never go stale.
Image.open(os.path.join(SPECPATH, "app_icon_1024.png")).save(
    os.path.join(SPECPATH, "app_icon.ico"),
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

# The hand model is not shipped by pip, it lives in models/.
datas = [("models/hand_landmarker.task", "models")]

# Window icon, app.py loads it from the bundle folder next to models/.
datas += [("app_icon_1024.png", ".")]

# mediapipe has no PyInstaller hook. Its data files, minus the DLL which goes in as a binary.
datas += collect_data_files("mediapipe", excludes=["**/*.dll"])

# libmediapipe.dll must keep its path, mediapipe finds it with importlib.resources.
binaries = collect_dynamic_libs("mediapipe")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    # Nothing imports this package, it is only looked up by name to find the DLL.
    hiddenimports=["mediapipe.tasks.c"],
    hookspath=[],
    # Must load mediapipe before PySide6's runtime hook imports Qt, see the file.
    runtime_hooks=["rthook_mediapipe_first.py"],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HandGesture",
    # Icon built into HandGesture.exe itself, what Explorer and shortcuts show.
    icon="app_icon.ico",
    console=False,
    # UPX-packed DLLs trip antivirus more often and can break Qt and mediapipe DLLs.
    upx=False,
    debug=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="HandGesture",
)
