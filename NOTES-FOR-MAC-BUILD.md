# Notes for the later macOS build

Nothing here has been run. It describes the separate step of turning this code into a macOS `.app`, which has to happen on a Mac.

## Target: Apple Silicon only

- mediapipe 1.0.1 publishes a macOS wheel for arm64 only (`macosx_11_0_arm64`). There is no Intel Mac build of it, so an Intel or universal2 app is not possible with the current pins.
- opencv-contrib-python 5.0.0.93 for arm64 needs macOS 13 or newer, so the app needs macOS 13+.
- Build with an arm64 Python 3.12 on an Apple Silicon Mac. PyInstaller does not cross-compile, so this cannot be built from Windows.
- Supporting Intel Macs would mean finding a mediapipe version that ships x86_64 macOS wheels and re-testing the gesture code against it.

## PyInstaller spec changes

`HandGesture.spec` is the Windows spec. A Mac build needs a separate spec, or a `sys.platform == "darwin"` branch:

- Wrap the `COLLECT` in a `BUNDLE(...)` to get `HandGesture.app`.
- Pass `info_plist=` the dict from `macos/Info.plist` (load it with `plistlib`), and set `bundle_identifier` to the same identifier. Replace the `com.example.handgesture` placeholder first.
- The icon must be `.icns`, not `.ico`. Make it from `app_icon_1024.png` (`iconutil` on the Mac).
- Keep `runtime_hooks=["rthook_mediapipe_first.py"]` and `hiddenimports=["mediapipe.tasks.c"]`. Whether the import-order hook is still needed on macOS is unknown.
- `controller.py` imports `controller_mac` with a plain import, so PyInstaller should find it. Check that the build includes `Quartz` and `AppKit` (pyautogui imports AppKit on macOS).
- `console=False` (windowed). The app then writes its log to `~/Library/Application Support/HandGesture/app.log`.

## Signing and notarization

- Without signing, Gatekeeper blocks the first launch. That's fine for your own Mac (Control-click > Open), not for giving it to anyone else.
- To distribute it:
  - You need a paid Apple Developer account and a "Developer ID Application" certificate.
  - Sign with hardened runtime: `codesign --deep --force --options runtime --entitlements <file> --sign "Developer ID Application: ..." HandGesture.app`.
- Entitlements under hardened runtime:
  - `com.apple.security.device.camera` is required for camera access.
  - Python/PyInstaller apps sometimes also need `com.apple.security.cs.disable-library-validation` or `com.apple.security.cs.allow-unsigned-executable-memory`. Whether this app does is unknown until it runs signed.
- Notarize:
  1. `xcrun notarytool submit HandGesture.zip --keychain-profile <profile> --wait`
  2. `xcrun stapler staple HandGesture.app`
- Do not enable App Sandbox, and do not ship through the Mac App Store. Posting mouse and keyboard events to other apps needs Accessibility, which sandboxed apps can't use for this.

## Permissions after the app is built

- Camera: macOS shows its prompt with the `NSCameraUsageDescription` text. A bundled app that reads the camera without that key is terminated by macOS.
- Accessibility: no Info.plist key or entitlement grants it. The user adds the app in System Settings > Privacy & Security > Accessibility by hand.
- Grants are tied to the app's code signature. An unsigned or ad-hoc-signed app that is rebuilt may need its Accessibility entry removed and re-added. Expect this during development.

## First things to test on a real Mac

1. `pip install -r requirements.txt` succeeds, and `python main.py --replay tilt.jsonl` gives the same counts as on Windows.
2. The camera opens with the AVFoundation backend. OpenCV may fail the very first open while the permission prompt is up; a restart should fix it.
3. With Accessibility granted:
   - the cursor follows the hand;
   - the click lands where the cursor is;
   - vertical scroll goes the expected way and at a sensible speed (`MAC_PIXELS_PER_WHEEL_NOTCH`, `MAC_VSCROLL_SIGN`);
   - sideways scroll goes the same way as on Windows (`MAC_HSCROLL_SIGN`).
4. Cmd+[ / Cmd+] / Cmd+R act in Safari and Chrome. Check with a non-US keyboard layout too.
5. Without Accessibility: the log shows the startup warning and nothing moves.
6. Retina: cursor positions match the screen. pyautogui and Quartz should both use points, not pixels.
