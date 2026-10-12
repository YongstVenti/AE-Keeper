"""Windows 11 compositor effects, retaining native caption controls."""
import ctypes
import os
from ctypes import wintypes


def apply_window_effects(window):
    if os.name != 'nt':
        return {'rounded': False, 'acrylic': False}
    try:
        window.update_idletasks()
        user = ctypes.windll.user32
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        handle = user.GetAncestor(window.winfo_id(), 2)
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        def set_attr(number, value):
            v = ctypes.c_int(value)
            ok = dwm.DwmSetWindowAttribute(handle, number, ctypes.byref(v), ctypes.sizeof(v)) == 0
            actual = ctypes.c_int()
            read = dwm.DwmGetWindowAttribute(handle, number, ctypes.byref(actual), ctypes.sizeof(actual)) == 0
            return ok and read and actual.value == value
        # DWMWCP_ROUND and DWMSBT_TRANSIENTWINDOW (Desktop Acrylic).
        rounded = set_attr(33, 2)
        set_attr(20, 0)  # The interface uses the light material variant.
        # Keep default caption/background colors so the compositor can draw its
        # material rather than a solid custom caption fill.
        set_attr(35, -1)
        set_attr(36, -1)
        acrylic = set_attr(38, 3)
        return {'rounded': rounded, 'acrylic': acrylic, 'handle': int(handle)}
    except (OSError, AttributeError, ctypes.ArgumentError):
        return {'rounded': False, 'acrylic': False}
