"""
notifier.py - Cross-platform desktop notifications, routed through the
app's own QSystemTrayIcon (QSystemTrayIcon.showMessage), which works on
Windows, Linux, and macOS with no extra dependencies or platform-specific
tools (this replaces an earlier Linux-only `notify-send` subprocess call).

MainWindow registers its tray icon once at startup; anything else in the
app just calls notifier.send(...).

Clicking a notification runs the `on_click` given with it. The tray only
reports "a message was clicked", not which one, so that's the callback of
the most recent notification (and none, if it had none).
"""
_tray_icon = None
_on_click = None


def register_tray(tray_icon):
    global _tray_icon
    _tray_icon = tray_icon
    tray_icon.messageClicked.connect(_clicked)


def _clicked():
    if _on_click is not None:
        _on_click()


def send(title, message, on_click=None):
    global _on_click
    _on_click = on_click
    if _tray_icon is not None:
        from PyQt6.QtWidgets import QSystemTrayIcon
        _tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)
    else:
        print(f"[notify] {title}: {message}")
