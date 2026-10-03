# PyInstaller runtime hook: runs before PySide6's own hook, which imports Qt before app.py starts.
import os

# Same as app.py: keep matplotlib, which mediapipe pulls in, off any GUI backend.
os.environ.setdefault("MPLBACKEND", "Agg")

# PySide6's import hook crashes on six, which mediapipe pulls in, if Qt is already loaded.
import mediapipe  # noqa: E402,F401
