# Hand Gesture Mouse Controller

A hands-free desktop interaction system that turns webcam-based hand movement into mouse and keyboard-like control. This project blends computer vision, real-time interaction design, and desktop automation to create a more natural and accessible way to interact with a computer.

## Overview

This project was built to explore how gesture recognition can replace or complement traditional input devices. Instead of using a physical mouse, the user can control the cursor, scroll, navigate pages, and trigger common actions using hand gestures detected by a webcam.

The result is a functional prototype that sits at the intersection of accessibility, HCI, and applied computer vision. It demonstrates a practical use case for gesture-based interfaces in everyday computing.

## What I built

- Real-time hand tracking with MediaPipe and OpenCV
- Gesture-driven mouse movement and click interactions
- Scroll, navigation, and system control modes
- Desktop UI with adjustable camera and control settings
- Diagnostic tooling for session recording, replay, and debugging
- Packaging for distribution as a Windows desktop app

## Interaction model

- Palm hold: arm/disarm gesture control
- One finger: pointer mode for moving the cursor
- One-finger tap: left click
- One finger drag: click-and-drag behavior
- Two fingers: page navigation and scrolling mode
- Three fingers: volume control mode on Windows
- Escape key: exit the app

## Tech stack

- Python
- OpenCV
- MediaPipe
- PyAutoGUI / desktop automation hooks
- PyInstaller
- Windows and macOS desktop support

## Why this project stands out

- Demonstrates applied computer vision in a real user-facing application
- Focuses on accessibility and hands-free interaction
- Combines AI/vision processing with desktop behavior and UI tuning
- Includes tooling for testing, recording, and debugging gestures in real time
- Moves beyond a simple demo into a usable desktop experience

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
