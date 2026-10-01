"""
idle.py - How long it's been since you last touched the keyboard or mouse,
so the pomodoro timer can pause when you've walked away (see PomodoroTab).
No new dependency: one backend per platform, picked on first use.

- Windows: GetLastInputInfo.
- Wayland compositors with ext-idle-notify-v1 (KDE Plasma 6, Sway,
  Hyprland, ...): talks to the compositor through libwayland-client with
  ctypes. Version 2's "input idle" ignores idle inhibitors, so a video or
  music playing in the background doesn't hide that nobody's there.
- GNOME (X11 or Wayland): org.gnome.Mutter.IdleMonitor over D-Bus.
- Other X11 desktops: org.freedesktop.ScreenSaver.GetSessionIdleTime.
- Otherwise, a locked screen counts as away, from when it locked.

idle_seconds() returns None when none of these work.
"""
import ctypes
import ctypes.util
import os
import select
import sys
import time

_backend = None
_chosen = False


def idle_seconds():
    """Seconds since the last keyboard/mouse input, or None if this desktop
    can't tell."""
    global _backend
    backend = _get_backend()
    if backend is None:
        return None
    try:
        return backend.idle_seconds()
    except Exception as e:  # a compositor/D-Bus hiccup must never break the timer
        print(f"[idle] {type(backend).__name__} stopped working: {e}")
        _backend = None
        return None


def available():
    return _get_backend() is not None


def _get_backend():
    global _backend, _chosen
    if not _chosen:
        _chosen = True
        _backend = _pick_backend()
        print(f"[idle] using {type(_backend).__name__ if _backend else 'nothing (unsupported desktop)'}")
    return _backend


def _pick_backend():
    if sys.platform == "win32":
        candidates = [_WindowsIdle]
    else:
        candidates = []
        if os.environ.get("WAYLAND_DISPLAY"):
            candidates.append(_WaylandIdle)
        candidates += [_MutterIdle, _ScreenSaverIdle, _ScreenLockIdle]
    for candidate in candidates:
        try:
            backend = candidate()
            backend.idle_seconds()  # must actually answer
            return backend
        except Exception:
            continue
    return None


# ---------- Windows ----------

class _WindowsIdle:
    class _LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint32)]

    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.kernel32.GetTickCount.restype = ctypes.c_uint32

    def idle_seconds(self):
        info = self._LastInputInfo(cbSize=ctypes.sizeof(self._LastInputInfo))
        if not self.user32.GetLastInputInfo(ctypes.byref(info)):
            raise OSError("GetLastInputInfo failed")
        # Both are 32-bit millisecond tick counts, which wrap every ~49 days.
        return ((self.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


# ---------- D-Bus (GNOME, X11 desktops, screen lock) ----------

def _dbus_call(service, path, interface, method):
    """The first value of a session-bus method's reply (half a second at
    most, so a stuck service can't freeze the UI)."""
    from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise OSError("no D-Bus session bus")
    reply = bus.call(QDBusMessage.createMethodCall(service, path, interface, method), QDBus.CallMode.Block, 500)
    if reply.type() != QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
        raise OSError(reply.errorMessage() or f"{method} failed")
    return reply.arguments()[0]


class _MutterIdle:
    def idle_seconds(self):
        return int(_dbus_call("org.gnome.Mutter.IdleMonitor", "/org/gnome/Mutter/IdleMonitor/Core",
                              "org.gnome.Mutter.IdleMonitor", "GetIdletime")) / 1000


class _ScreenSaverIdle:
    # Answers "not supported" on KDE's Wayland session, hence the order above.
    def idle_seconds(self):
        return int(_dbus_call("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
                              "org.freedesktop.ScreenSaver", "GetSessionIdleTime")) / 1000


class _ScreenLockIdle:
    """Only knows about a locked screen: away from the moment it locked."""

    def idle_seconds(self):
        args = ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver")
        if not _dbus_call(*args, "GetActive"):
            return 0.0
        return float(_dbus_call(*args, "GetActiveTime"))


# ---------- Wayland: ext-idle-notify-v1 ----------

class _WlMessage(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("signature", ctypes.c_char_p),
                ("types", ctypes.POINTER(ctypes.c_void_p))]


class _WlInterface(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("version", ctypes.c_int),
                ("method_count", ctypes.c_int), ("methods", ctypes.POINTER(_WlMessage)),
                ("event_count", ctypes.c_int), ("events", ctypes.POINTER(_WlMessage))]


class _WlArgument(ctypes.Union):
    _fields_ = [("i", ctypes.c_int32), ("u", ctypes.c_uint32), ("s", ctypes.c_char_p), ("o", ctypes.c_void_p)]


_GLOBAL = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32)
_GLOBAL_REMOVE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)
_NO_ARGS_EVENT = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)


class _WaylandIdle:
    """Asks the compositor for an "idled" event after GRANULARITY_S without
    input (and "resumed" on the next input), so idle time is known to within
    that much -- plenty for a pause after minutes away. Uses its own
    connection to the compositor, read without blocking each time it's asked."""

    GRANULARITY_S = 30

    def __init__(self):
        path = ctypes.util.find_library("wayland-client") or "libwayland-client.so.0"
        lib = self.lib = ctypes.CDLL(path)
        vp, u32, i = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int
        for name, restype, argtypes in (
            ("wl_display_connect", vp, [ctypes.c_char_p]),
            ("wl_display_disconnect", None, [vp]),
            ("wl_display_roundtrip", i, [vp]),
            ("wl_display_get_fd", i, [vp]),
            ("wl_display_flush", i, [vp]),
            ("wl_display_prepare_read", i, [vp]),
            ("wl_display_read_events", i, [vp]),
            ("wl_display_cancel_read", None, [vp]),
            ("wl_display_dispatch_pending", i, [vp]),
            ("wl_proxy_get_version", u32, [vp]),
            ("wl_proxy_add_listener", i, [vp, vp, vp]),
            ("wl_proxy_marshal_array_constructor_versioned", vp, [vp, u32, ctypes.POINTER(_WlArgument), vp, u32]),
        ):
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = restype, argtypes

        self._idle_since = None  # monotonic time input stopped, while idle
        self._interfaces()
        self.display = lib.wl_display_connect(None)
        if not self.display:
            raise OSError("can't connect to the Wayland compositor")
        try:
            self._setup()
        except Exception:
            lib.wl_display_disconnect(self.display)
            raise

    def _interfaces(self):
        """ext_idle_notifier_v1 / ext_idle_notification_v1, as wayland-scanner
        would generate them (libwayland-client doesn't ship these two)."""
        lib = self.lib
        self.registry_iface = _WlInterface.in_dll(lib, "wl_registry_interface")
        self.seat_iface = _WlInterface.in_dll(lib, "wl_seat_interface")
        self.notification_iface = _WlInterface()
        self.notifier_iface = _WlInterface()
        none = (ctypes.c_void_p * 3)()
        types = (ctypes.c_void_p * 3)(ctypes.addressof(self.notification_iface), None, ctypes.addressof(self.seat_iface))
        notifier_methods = (_WlMessage * 3)(
            _WlMessage(b"destroy", b"", none), _WlMessage(b"get_idle_notification", b"nuo", types),
            _WlMessage(b"get_input_idle_notification", b"2nuo", types))
        notification_methods = (_WlMessage * 1)(_WlMessage(b"destroy", b"", none))
        notification_events = (_WlMessage * 2)(_WlMessage(b"idled", b"", none), _WlMessage(b"resumed", b"", none))
        self.notifier_iface.name, self.notifier_iface.version = b"ext_idle_notifier_v1", 2
        self.notifier_iface.method_count, self.notifier_iface.methods = 3, notifier_methods
        self.notification_iface.name, self.notification_iface.version = b"ext_idle_notification_v1", 2
        self.notification_iface.method_count, self.notification_iface.methods = 1, notification_methods
        self.notification_iface.event_count, self.notification_iface.events = 2, notification_events
        self._keep = [none, types, notifier_methods, notification_methods, notification_events]  # owned by C now

    def _marshal_new(self, proxy, opcode, args, interface, version):
        array = (_WlArgument * len(args))(*args)
        new = self.lib.wl_proxy_marshal_array_constructor_versioned(
            proxy, opcode, array, ctypes.addressof(interface), version)
        if not new:
            raise OSError("Wayland request failed")
        return new

    def _setup(self):
        lib = self.lib
        globals_ = {}

        def on_global(_data, _registry, name, interface, version):
            globals_.setdefault(interface, (name, version))

        # (the CFUNCTYPE objects must outlive the connection)
        self._callbacks = [_GLOBAL(on_global), _GLOBAL_REMOVE(lambda *_: None)]
        self._registry_listener = (ctypes.c_void_p * 2)(
            *(ctypes.cast(cb, ctypes.c_void_p).value for cb in self._callbacks))

        display_version = lib.wl_proxy_get_version(self.display)
        registry = self._marshal_new(self.display, 1, [_WlArgument(o=None)], self.registry_iface, display_version)
        lib.wl_proxy_add_listener(registry, self._registry_listener, None)
        if lib.wl_display_roundtrip(self.display) < 0:
            raise OSError("Wayland roundtrip failed")
        if b"ext_idle_notifier_v1" not in globals_ or b"wl_seat" not in globals_:
            raise OSError("the compositor doesn't offer ext-idle-notify")

        def bind(interface_name, iface, version):
            name, _ = globals_[interface_name]
            args = [_WlArgument(u=name), _WlArgument(s=interface_name), _WlArgument(u=version), _WlArgument(o=None)]
            return self._marshal_new(registry, 0, args, iface, version)

        seat = bind(b"wl_seat", self.seat_iface, 1)
        notifier_version = min(globals_[b"ext_idle_notifier_v1"][1], 2)
        notifier = bind(b"ext_idle_notifier_v1", self.notifier_iface, notifier_version)
        # v2's get_input_idle_notification ignores idle inhibitors (video players).
        opcode = 2 if notifier_version >= 2 else 1
        notification = self._marshal_new(
            notifier, opcode, [_WlArgument(o=None), _WlArgument(u=self.GRANULARITY_S * 1000), _WlArgument(o=seat)],
            self.notification_iface, notifier_version)

        def on_idled(_data, _proxy):
            self._idle_since = time.monotonic() - self.GRANULARITY_S

        def on_resumed(_data, _proxy):
            self._idle_since = None

        self._callbacks += [_NO_ARGS_EVENT(on_idled), _NO_ARGS_EVENT(on_resumed)]
        self._notification_listener = (ctypes.c_void_p * 2)(
            *(ctypes.cast(cb, ctypes.c_void_p).value for cb in self._callbacks[2:]))
        lib.wl_proxy_add_listener(notification, self._notification_listener, None)
        if lib.wl_display_roundtrip(self.display) < 0:
            raise OSError("Wayland roundtrip failed")
        self.fd = lib.wl_display_get_fd(self.display)

    def _read_events(self):
        """Handles whatever the compositor has sent, without waiting."""
        lib, display = self.lib, self.display
        while lib.wl_display_prepare_read(display) != 0:
            if lib.wl_display_dispatch_pending(display) < 0:
                raise OSError("Wayland connection lost")
        lib.wl_display_flush(display)
        readable, _, _ = select.select([self.fd], [], [], 0)
        if readable:
            if lib.wl_display_read_events(display) < 0:
                raise OSError("Wayland connection lost")
        else:
            lib.wl_display_cancel_read(display)
        if lib.wl_display_dispatch_pending(display) < 0:
            raise OSError("Wayland connection lost")

    def idle_seconds(self):
        self._read_events()
        return 0.0 if self._idle_since is None else time.monotonic() - self._idle_since
