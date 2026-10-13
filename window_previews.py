"""Bounded local window previews with a timeout-isolated Windows renderer.

Automatic capture never raises other windows. Explicit full capture projects
DWM thumbnails onto our opaque temporary board, including minimized windows.
"""
import base64
from io import BytesIO
import json
import multiprocessing as mp
import os
from pathlib import Path
import tempfile
import time
from image_codec import encode_full, MAX_FULL_BYTES

MAX_IMAGE_BYTES = 180000


def window_key(row):
    return (row.get('hwnd'), row.get('pid'), row.get('created')) if row.get('hwnd') and row.get('pid') else None


def image_readable(picture):
    from PIL import ImageStat
    width, height = picture.size
    inner = picture.crop((int(width * .06), int(height * .15), int(width * .94), int(height * .94))).resize((60, 40))
    stat = ImageStat.Stat(inner.convert('RGB'))
    # Minimized PrintWindow often returns only a caption and an all-black client.
    return not (max(stat.mean) < 5 and max(stat.stddev) < 2)


def encode_preview(picture, edge=640):
    from PIL import Image
    picture = picture.convert('RGB')
    picture.thumbnail((edge, int(edge * .625)), Image.Resampling.LANCZOS)
    output = BytesIO(); picture.save(output, format='WEBP', quality=70, method=3)
    raw = output.getvalue()
    output = BytesIO(); picture.save(output, format='WEBP', lossless=True, quality=80, method=3)
    raw = min(raw, output.getvalue(), key=len)
    if len(raw) > MAX_IMAGE_BYTES:
        output = BytesIO(); picture.save(output, format='WEBP', quality=40, method=3)
        raw = output.getvalue()
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('窗口预览超过大小限制。')
    return raw


def _save_images(picture, folder, index, method, options):
    (folder / f'{index}.webp').write_bytes(encode_preview(picture, options.get('workspace_thumb_edge', 640)))
    info = {'time': time.time(), 'method': method}
    if options.get('workspace_hd'):
        try:
            full = encode_full(picture, options.get('workspace_hd_edge', 1920), options.get('workspace_image_mode', 'lossless'))
            (folder / f'{index}.full').write_bytes(full)
        except ValueError as error:
            info['full_reason'] = str(error)
    (folder / f'{index}.json').write_text(json.dumps(info))


def _still_same_window(row):
    import ctypes
    from ctypes import wintypes
    import psutil
    from desktop_windows import user32
    api = user32(); pid = wintypes.DWORD()
    api.GetWindowThreadProcessId(row['hwnd'], ctypes.byref(pid))
    try:
        return bool(api.IsWindow(row['hwnd'])) and pid.value == row['pid'] and psutil.Process(pid.value).create_time() == row['created']
    except psutil.Error:
        return False


def _print_window(row):
    import ctypes
    from PIL import Image
    import win32gui
    import win32ui
    if not _still_same_window(row) or ctypes.windll.user32.IsHungAppWindow(row['hwnd']):
        return None
    hwnd = row['hwnd']; rect = win32gui.GetWindowRect(hwnd)
    width, height = (row['rect'][2:] if row.get('minimized') else (rect[2] - rect[0], rect[3] - rect[1]))
    if width <= 0 or height <= 0 or width > 5000 or height > 3000:
        return None
    dc_handle = win32gui.GetWindowDC(hwnd)
    dc = win32ui.CreateDCFromHandle(dc_handle); memory = dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap(); bitmap.CreateCompatibleBitmap(dc, width, height)
    memory.SelectObject(bitmap)
    try:
        if not ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2):
            return None
        picture = Image.frombuffer('RGB', (width, height), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1)
        return picture if image_readable(picture) else None
    finally:
        win32gui.DeleteObject(bitmap.GetHandle()); memory.DeleteDC(); dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, dc_handle)


def _dwm_board(rows, folder, options=None):
    """User-initiated only; never show/minimize/activate a source window."""
    import ctypes
    from ctypes import wintypes
    import tkinter as tk
    from PIL import ImageGrab, Image
    import win32gui
    import win32process
    from desktop_windows import user32
    api, dwm = user32(), ctypes.windll.dwmapi
    class Props(ctypes.Structure):
        _fields_ = [('flags', wintypes.DWORD), ('dest', wintypes.RECT), ('source', wintypes.RECT),
                    ('opacity', ctypes.c_ubyte), ('visible', wintypes.BOOL), ('client', wintypes.BOOL)]
    dwm.DwmRegisterThumbnail.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.POINTER(wintypes.HANDLE)]
    dwm.DwmUpdateThumbnailProperties.argtypes = [wintypes.HANDLE, ctypes.POINTER(Props)]
    dwm.DwmQueryThumbnailSourceSize.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.SIZE)]
    dwm.DwmUnregisterThumbnail.argtypes = [wintypes.HANDLE]
    board = tk.Tk(); board.withdraw(); board.overrideredirect(True)
    board.configure(bg='#173a35')
    options = options or {}
    hd = options.get('workspace_hd', False)
    width = min(1500 if hd else 720, api.GetSystemMetrics(0) - 60)
    height = min(1050 if hd else 640, api.GetSystemMetrics(1) - 100)
    x, y = (api.GetSystemMetrics(0) - width) // 2, (api.GetSystemMetrics(1) - height) // 2
    board.geometry(f'{width}x{height}+{x}+{y}')
    tk.Label(board, text='AE Keeper · 正在保存窗口预览\n原窗口不会展开或切换',
             bg='#173a35', fg='white', font=('Microsoft YaHei UI', 12), pady=10).pack(fill='x')
    board.update_idletasks()
    hwnd = int(board.wm_frame(), 0)
    api.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    api.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    api.SetWindowLongPtrW(hwnd, -20, api.GetWindowLongPtrW(hwnd, -20) | 0x08000000 | 0x80)
    board.deiconify(); board.update()
    hwnd = int(board.wm_frame(), 0)
    api.SetWindowPos(hwnd, ctypes.c_void_p(-1), x, y, width, height, 0x0010)
    registered = []
    try:
        cell_w, cell_h = (width - 36) // 2, (height - 96) // 3
        for page in range(0, len(rows), 6):
            cells = []
            for local, (index, row) in enumerate(rows[page:page + 6]):
                if not _still_same_window(row):
                    continue
                handle = wintypes.HANDLE()
                if dwm.DwmRegisterThumbnail(hwnd, row['hwnd'], ctypes.byref(handle)) != 0:
                    continue
                registered.append(handle)
                size = wintypes.SIZE()
                if dwm.DwmQueryThumbnailSourceSize(handle, ctypes.byref(size)) != 0 or min(size.cx, size.cy) <= 0:
                    continue
                if hd:
                    # Project source regions at the requested scale onto our
                    # opaque board. Stitch tiles, never upscale a small preview.
                    if size.cx > 16384 or size.cy > 16384:
                        continue
                    scale = min(1, options.get('workspace_hd_edge', 1920) / max(size.cx, size.cy))
                    fw, fh = max(1, round(size.cx * scale)), max(1, round(size.cy * scale))
                    picture = Image.new('RGB', (fw, fh))
                    valid = True
                    for ty in range(0, fh, height - 92):
                        for tx in range(0, fw, width - 24):
                            tw, th = min(width - 24, fw - tx), min(height - 92, fh - ty)
                            destination = wintypes.RECT(12, 80, 12 + tw, 80 + th)
                            source = wintypes.RECT(round(tx * size.cx / fw), round(ty * size.cy / fh),
                                                  round((tx + tw) * size.cx / fw), round((ty + th) * size.cy / fh))
                            props = Props(1 | 2 | 4 | 8 | 16, destination, source, 255, True, False)
                            if dwm.DwmUpdateThumbnailProperties(handle, ctypes.byref(props)) != 0:
                                valid = False; break
                            for _ in range(3):
                                board.update(); time.sleep(.035)
                            dwm.DwmFlush()
                            origin = win32gui.ClientToScreen(hwnd, (0, 0))
                            box = (origin[0] + 12, origin[1] + 80, origin[0] + 12 + tw, origin[1] + 80 + th)
                            points = [(box[0] + int((tw - 1) * a), box[1] + int((th - 1) * b))
                                      for a in (0, .5, 1) for b in (0, .5, 1)]
                            if any(win32process.GetWindowThreadProcessId(win32gui.WindowFromPoint(point))[1] != os.getpid() for point in points):
                                valid = False; break
                            picture.paste(ImageGrab.grab(bbox=box), (tx, ty))
                        if not valid:
                            break
                    dwm.DwmUnregisterThumbnail(handle); registered.remove(handle)
                    if valid and image_readable(picture):
                        _save_images(picture, folder, index, 'dwm-hd', options)
                    continue
                left, top = 12 + (local % 2) * (cell_w + 12), 80 + (local // 2) * cell_h
                scale = min(cell_w / size.cx, (cell_h - 12) / size.cy)
                tw, th = max(1, int(size.cx * scale)), max(1, int(size.cy * scale))
                left += (cell_w - tw) // 2
                destination = wintypes.RECT(left, top, left + tw, top + th)
                props = Props(1 | 4 | 8 | 16, destination, wintypes.RECT(), 255, True, False)
                if dwm.DwmUpdateThumbnailProperties(handle, ctypes.byref(props)) == 0:
                    cells.append((index, destination))
            for _ in range(4):
                board.update(); time.sleep(.045)
            dwm.DwmFlush()
            origin = win32gui.ClientToScreen(hwnd, (0, 0))
            for index, rect in cells:
                box = (origin[0] + rect.left, origin[1] + rect.top, origin[0] + rect.right, origin[1] + rect.bottom)
                # Reject samples if any other window covers this capture cell.
                # Read only the opaque destination board, never a full desktop.
                points = [(box[0] + int((box[2] - box[0] - 1) * a), box[1] + int((box[3] - box[1] - 1) * b))
                          for a in (0, .5, 1) for b in (0, .5, 1)]
                if any(win32process.GetWindowThreadProcessId(win32gui.WindowFromPoint(point))[1] != os.getpid() for point in points):
                    continue
                picture = ImageGrab.grab(bbox=box)
                if image_readable(picture):
                    _save_images(picture, folder, index, 'dwm', options)
            for handle in registered:
                dwm.DwmUnregisterThumbnail(handle)
            registered.clear()
            board.update()
    finally:
        for handle in registered:
            dwm.DwmUnregisterThumbnail(handle)
        board.destroy()


def _render_worker(rows, folder, interactive, options=None):
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except OSError:
        pass
    folder = Path(folder)
    options = options or {}
    if interactive:
        try:
            _dwm_board(list(enumerate(rows)), folder, options)
        except Exception:
            pass  # Still retain metadata and try individual window rendering.
    for index, row in enumerate(rows):
        if (folder / f'{index}.webp').exists():
            continue
        try:
            picture = _print_window(row)
            if picture is not None:
                _save_images(picture, folder, index, 'window', options)
        except Exception:
            continue


def attach_previews(payload, previous=None, enabled=True, interactive=False, timeout=12, options=None):
    rows = payload['windows']
    options = options or {}
    if options.get('workspace_hd'):
        timeout = max(timeout, 90 if interactive else 25)
    if not rows:
        return payload
    if not enabled:
        for row in rows:
            row['preview'] = {'status': 'disabled', 'image': '', 'time': 0, 'method': '', 'reason': '预览图保存已关闭'}
        return payload
    cache = {window_key(row): row['preview'] for row in (previous or {}).get('windows', [])
             if window_key(row) and row.get('preview', {}).get('image')}
    # Own generated folder only; helper input is metadata, output is bounded images.
    with tempfile.TemporaryDirectory(prefix='ae-keeper-previews-') as temp:
        folder = Path(temp).resolve()
        assert folder.parent == Path(tempfile.gettempdir()).resolve()
        process = mp.get_context('spawn').Process(target=_render_worker, args=(rows, str(folder), interactive, options), daemon=True)
        try:
            process.start(); process.join(timeout)
        except Exception:
            pass  # Images are optional; failing to launch a renderer must not lose metadata.
        if process.is_alive():
            process.terminate(); process.join(2)
        try:
            for index, row in enumerate(rows):
                path = folder / f'{index}.webp'
                if path.is_file() and path.stat().st_size <= MAX_IMAGE_BYTES:
                    info = json.loads((folder / f'{index}.json').read_text())
                    row['preview'] = {'status': 'captured', 'image': base64.b64encode(path.read_bytes()).decode('ascii'),
                                      'time': info['time'], 'method': info['method'], 'reason': ''}
                    full = folder / f'{index}.full'
                    if full.is_file() and full.stat().st_size <= MAX_FULL_BYTES:
                        row['preview']['full_image'] = base64.b64encode(full.read_bytes()).decode('ascii')
                    elif info.get('full_reason'):
                        row['preview']['reason'] = info['full_reason']
                elif window_key(row) in cache:
                    row['preview'] = {**cache[window_key(row)], 'status': 'cached', 'reason': '保留此窗口上次可读取的预览'}
                    if not options.get('workspace_hd'):
                        row['preview'].pop('full_image', None)
                else:
                    row['preview'] = {'status': 'unavailable', 'image': '', 'time': 0, 'method': '',
                                      'reason': '暂无法读取画面，窗口信息已保存；可点击拍摄全部窗口'}
        finally:
            if not process.is_alive():
                process.close()
    return payload
