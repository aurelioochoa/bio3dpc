"""Confine an X11/Xwayland pointer without grabbing the keyboard.

Tk's global grab redirects events but does not confine the pointer. Use the
same X connection as Tk so motion and button events still reach its bindings.
Other window systems retain the frontend's recentering fallback.
"""
from __future__ import annotations

import ctypes as C
import _tkinter


class PointerCapture:
    def __init__(self, root, canvas):
        self.canvas = canvas
        self.active = False
        self._lib = None
        self._display = None
        if root.tk.call('tk', 'windowingsystem') != 'x11':
            return
        try:
            # Resolve the libraries already loaded by this Python's Tk rather
            # than loading a potentially different Tk installation.
            lib = C.CDLL(_tkinter.__file__)
            lib.Tk_MainWindow.argtypes = [C.c_void_p]
            lib.Tk_MainWindow.restype = C.c_void_p
            lib.XGrabPointer.argtypes = [
                C.c_void_p, C.c_ulong, C.c_int, C.c_uint, C.c_int,
                C.c_int, C.c_ulong, C.c_ulong, C.c_ulong,
            ]
            lib.XGrabPointer.restype = C.c_int
            lib.XUngrabPointer.argtypes = [C.c_void_p, C.c_ulong]
            lib.XUngrabPointer.restype = C.c_int
            lib.XFlush.argtypes = [C.c_void_p]
            lib.XFlush.restype = C.c_int
            window = lib.Tk_MainWindow(root.tk.interpaddr())
            if not window:
                return
            # Tk_Display is a public tk.h macro: the first field of the
            # public Tk_FakeWin structure is Display*, in Tk 8 and Tk 9.
            self._display = C.cast(window, C.POINTER(C.c_void_p))[0]
            self._lib = lib
        except (AttributeError, OSError):
            pass

    def capture(self) -> bool:
        if self._lib is None or not self.canvas.winfo_ismapped():
            return False
        window = self.canvas.winfo_id()
        # ButtonPress, ButtonRelease, Enter, Leave and PointerMotion masks.
        # Both devices remain asynchronous; desktop keyboard shortcuts work.
        mask = (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5) | (1 << 6)
        result = self._lib.XGrabPointer(
            self._display, window, True, mask, 1, 1, window, 0, 0)
        self.active = result == 0  # GrabSuccess
        self._lib.XFlush(self._display)
        return self.active

    def release(self) -> None:
        if self.active:
            self._lib.XUngrabPointer(self._display, 0)
            self._lib.XFlush(self._display)
            self.active = False
