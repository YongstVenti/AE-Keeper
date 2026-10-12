"""Shared identity and palette for AE Keeper; artwork comes from the owner's SVG."""
from pathlib import Path
import ctypes
import os
import tkinter as tk
from ctypes import wintypes
from PIL import Image, ImageTk

NAME = 'AE Keeper'
TITLE = 'AE Keeper · 网页留痕'
BG, WHITE, INK, MUTED = '#F4F8F6', '#FFFFFF', '#203D36', '#647E75'
ACCENT, HOVER, PRESSED = '#0D766D', '#128578', '#095F57'
TINT, TINT_HOVER, TINT_PRESSED = '#E7F2EE', '#DDEEE7', '#D1E7DD'
SELECTED_INK, BORDER, FOCUS = '#095F57', '#DCE8E2', '#65AB95'
ASSETS = Path(__file__).parent / 'assets'


def logo_image():
    with Image.open(ASSETS / 'ae-keeper-icon.png') as image:
        return image.convert('RGBA')


def logo_photo(master, size=64):
    image = logo_image().resize((size, size), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(image, master=master)


def apply_icon(window):
    paths = sorted((ASSETS / 'ae-keeper-icons').glob('*.png'),
                   key=lambda path: int(path.stem), reverse=True)
    photos = []
    for path in paths:
        with Image.open(path) as image:
            photos.append(ImageTk.PhotoImage(image.convert('RGBA'), master=window))
    if not photos:
        photos = [logo_photo(window, 64)]
    # Tk packs these as individual native icon frames. Do not overwrite them
    # with iconbitmap: its file extraction path selected one 40px image for
    # both caption and taskbar on the tested 125% display.
    window.iconphoto(True, *photos)
    window.ae_icon_photos = photos
    window.ae_icon_photo = photos[0]
    root = window._root()
    if not getattr(root, 'ae_icon_map_hook', False):
        root.bind_all('<Map>', lambda event: refresh_native_icon(event.widget)
                      if isinstance(event.widget, (tk.Tk, tk.Toplevel)) else None, add='+')
        root.ae_icon_map_hook = True
    window.after_idle(lambda: refresh_native_icon(window))


def refresh_native_icon(window):
    """Give Windows independent small/large frames at the current window DPI."""
    if os.name != 'nt' or not window.winfo_exists():
        return
    user = ctypes.windll.user32
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetDpiForWindow.argtypes = [wintypes.HWND]
    user.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                               wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.LoadImageW.restype = wintypes.HANDLE
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.SendMessageW.restype = wintypes.LPARAM
    user.DestroyIcon.argtypes = [wintypes.HANDLE]
    handle = user.GetAncestor(window.winfo_id(), 2)
    dpi = user.GetDpiForWindow(handle) or 96
    key = (int(handle), dpi)
    old = getattr(window, 'ae_native_icons', None)
    if old and old['key'] == key:
        return
    sizes = [user.GetSystemMetricsForDpi(metric, dpi) for metric in (49, 11)]
    icons = [user.LoadImageW(None, str(ASSETS / 'ae-keeper.ico'), 1,
                            size, size, 0x10) for size in sizes]
    if not all(icons):
        for icon in icons:
            if icon:user.DestroyIcon(icon)
        return
    for kind, icon in enumerate(icons):
        user.SendMessageW(handle, 0x80, kind, int(icon))  # WM_SETICON
    window.ae_native_icons = {'key': key, 'handles': icons}
    if old:
        for icon in old['handles']:user.DestroyIcon(icon)
    if not getattr(window, 'ae_native_icon_hooks', False):
        def configured(event):
            if event.widget == window:refresh_native_icon(window)
        def destroyed(event):
            if event.widget == window:
                state = getattr(window, 'ae_native_icons', None)
                if state:
                    for icon in state['handles']:user.DestroyIcon(icon)
                    window.ae_native_icons = None
        window.bind('<Configure>', configured, add='+')
        window.bind('<Destroy>', destroyed, add='+')
        window.ae_native_icon_hooks = True
