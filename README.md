# Hand Gesture Mouse Controller

Control your desktop with hand movements captured by a webcam. The app tracks one hand with MediaPipe and translates gestures into pointer movement, clicks, scrolling, and navigation actions. A desktop interface provides a live camera preview and adjustable settings; a separate CLI supports diagnostics and session replay.

## Gestures

- Hold an open palm still for 0.5 seconds to arm or disarm control.
- Raise one finger to enter pointer mode. Move your hand to move the cursor; tap your index finger down and back up to left-click.
- Raise two fingers to enter page mode. Move vertically or sideways to scroll; tilt your hand left or right for back or forward; tap both fingers down and back up to refresh.
- Press Esc in the desktop app or CLI to quit.

## Setup (Windows)

Turn the camera on for desktop apps first:
Settings > Privacy & security > Camera > Camera access **on**, and
"Let desktop apps access your camera" **on**.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
curl -L -o models\hand_landmarker.task https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
```

## Run the desktop app

```powershell
.venv\Scripts\python.exe app.py
```

Settings changed in the app are saved to `%LOCALAPPDATA%\HandGesture\settings.json`. Delete that file to go back to the `config.py` values.

## Run the diagnostic CLI

```powershell
.venv\Scripts\python.exe main.py
```

In a dim room, `--low-light` contrast-boosts the copy of each frame used for detection. It can also make detection worse, so compare with it off. The desktop app has the same switch under Settings > Camera.

## Without moving the real mouse

```powershell
.venv\Scripts\python.exe main.py --dry-run --verbose
```

## Record, replay, measure

```powershell
.venv\Scripts\python.exe main.py --record session.jsonl
.venv\Scripts\python.exe main.py --replay session.jsonl --log
.venv\Scripts\python.exe main.py --log
```

All thresholds are in `config.py`. The CLI always uses `config.py`, never the app's saved settings.

## Build the exe

```powershell
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\pyinstaller.exe --noconfirm --clean HandGesture.spec
```

Output is `dist\HandGesture\`. Ship the whole folder, then run `HandGesture.exe` inside it. The exe writes its log to `%LOCALAPPDATA%\HandGesture\app.log`.

## macOS (not yet tested on a Mac)

The code has a macOS output backend, but it has never been run on a Mac. Apple Silicon only: mediapipe 1.0.1 has no Intel Mac build. Needs macOS 13 or newer, because of the OpenCV build.

Setup, from Terminal in the project folder. pip installs the macOS-only packages in `requirements.txt` (pyobjc) only on a Mac:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
curl -L -o models/hand_landmarker.task https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
```

Run the desktop app or the CLI:

```bash
.venv/bin/python app.py
.venv/bin/python main.py
```

### Two permissions you must grant by hand

macOS asks for or blocks these per app. They cannot be bundled or granted by the app itself. Grant them after the first launch. After a reinstall or rebuild, check that they are still on: macOS can drop a permission when the app changes.

1. **Camera.** On first launch macOS asks whether the app may use the camera: allow it. If the camera then shows an error, quit and start the app again. If you clicked Don't Allow, turn it on in System Settings > Privacy & Security > Camera.
2. **Accessibility.** Moving the cursor, clicking, scrolling and the back/forward/refresh shortcuts all need it. Without it, the gestures are seen but nothing happens on screen, and there is no error message. Open System Settings > Privacy & Security > Accessibility, add the app and switch it on, then quit and restart the app.

Both permissions go to the app that launches Python. Running from Terminal, that is **Terminal** (or iTerm, or VS Code), not Python itself. Running the built `.app`, it is the `.app`.

On macOS, back and forward are Cmd+[ and Cmd+]. They work in Safari, Chrome and Finder, but not in every app. Refresh is Cmd+R. These are the key positions on a US keyboard layout, see `MAC_*` in `config.py`.

Settings are saved in `~/Library/Application Support/HandGesture/`, and so is the built app's log, `app.log`. Run from Terminal, the log prints in the Terminal window.
