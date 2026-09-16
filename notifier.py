"""
notifier.py - Cross-platform desktop notifications, routed through the
app's own QSystemTrayIcon (QSystemTrayIcon.showMessage), which works on
Windows, Linux, and macOS with no extra dependencies or platform-specific
tools (this replaces an earlier Linux-only `notify-send` subprocess call).

MainWindow registers its tray icon once at startup; anything else in the
app just calls notifier.send(...).
"""
_tray_icon = None


def register_tray(tray_icon):
    global _tray_icon
    _tray_icon = tray_icon


def send(title, message):
    if _tray_icon is not None:
        from PyQt6.QtWidgets import QSystemTrayIcon
        _tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)
    else:
        print(f"[notify] {title}: {message}")
