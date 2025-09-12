# IPO Tracker — Notifications (Cross‑Platform Behavior)

This document describes how in‑app notifications behave on macOS and Windows, and what to expect at runtime.

## Summary
- Popups: In‑app centered window with details and an OK button (works even if the main window is minimized).
- Sound: Optional chime when the popup appears (toggle with the “Sound” checkbox in the header).
- Badge/Attention cue: Unread counter and OS‑specific attention indicators to ensure alerts aren’t missed.

## macOS
- Popup
  - A Tk window appears centered on the screen with the alert details. 
  - The app is de‑minimized and brought to the foreground so the popup is visible.
- Sound
  - Plays a short chime (system “Glass” sound) if “Sound” is enabled.
- Badge Count on Dock (when available)
  - Shows the number of unread popups on the Dock icon.
  - Requires PyObjC at runtime (used internally via AppKit). If PyObjC is not present, the app title will instead show the count as: “IPO Tracker - IPO Scoop [N]”.

## Windows
- Popup
  - A Tk window appears centered with the alert details.
  - The app is de��minimized and brought to the foreground.
- Sound
  - Plays a system beep via winsound if “Sound” is enabled.
- Taskbar Flash
  - The taskbar icon flashes (via FlashWindowEx) to draw attention until the popup is dismissed.
- Unread Count
  - The window title displays “IPO Tracker - IPO Scoop [N]” to show unread popups.

## Common Behavior (Both macOS and Windows)
- Scheduled Times
  - Popups are triggered at scheduled times: 
    - 1 day before at 08:00 (local time)
    - Same day at 08:00 (local time; only if the IPO date is strictly in the future)
  - The app must be running for popups to appear (the scheduler checks every minute and will fire overdue items as soon as the app is awake/running).
- Minimization
  - If minimized, the app de‑minimizes and brings the popup to the front.
- Unread Badge
  - The unread counter increments when a popup appears and decrements when the popup is closed.
- “Test Notify Now”
  - Use the header button to trigger the same in‑app popup immediately for verification.

## Notes & Requirements
- Time Zone
  - All scheduling is computed using local time on the host machine.
- Saved Reminders
  - Persisted in ipo_notifications.json under the app’s data directory; auto‑loaded on startup.
- Sound Toggle
  - The “Sound” checkbox in the header controls whether the chime plays on popup.
- Dock Badge (macOS) 
  - To show the number on the Dock icon, PyObjC should be available in the runtime environment; otherwise, the app falls back to a title suffix.

## Troubleshooting
- No Popup Appears
  - Ensure the app is open and not blocked by OS policy; confirm scheduled times are in the future.
  - If using a packaged app, keep it running across the scheduled time.
- Sound Not Playing
  - Verify “Sound” is enabled.
  - Some systems may block system sounds or not have the expected sound file; the app also attempts a Tk bell as a minimal fallback.
- Badge Count Not Showing on Dock (macOS)
  - Install PyObjC in the environment used by the app, or rely on the title suffix (“[N]”).

## Testing
1. Start the app.
2. Click “Test Notify Now” in the header. The in‑app popup should appear centered.
3. Toggle the “Sound” checkbox on/off and repeat to verify sound behavior.
4. Confirm the unread count increments/decrements on each popup open/close.

## Packaging Notes
- macOS
  - Dock badge requires PyObjC. If not included, the feature gracefully falls back to the title suffix.
- Windows
  - winsound is built‑in; taskbar flash uses the Win32 API (via ctypes), no extra install needed.
