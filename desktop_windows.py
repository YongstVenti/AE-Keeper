"""Local desktop inventory. Never capture buffers, credentials or command lines.

Explorer tabs use Shell COM; tab creation is restricted to a newly opened window.
Terminal sessions use process directories, which may differ from shell logical pwd.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

import psutil

SHELLS = {'cmd.exe', 'powershell.exe', 'pwsh.exe'}
TERMINALS = {'windowsterminal.exe', 'windowsterminalpreview.exe'}
BROWSERS = {'chrome.exe', 'msedge.exe'}
IGNORED_CLASSES = {'Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd', 'tooltips_class32'}
IGNORED_APPS = {'shellexperiencehost.exe', 'startmenuexperiencehost.exe',
                'searchhost.exe', 'textinputhost.exe'}


def user32():
    api = ctypes.windll.user32
    api.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    api.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    api.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    api.GetWindow.restype = wintypes.HWND
    api.IsWindow.argtypes = [wintypes.HWND]
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.IsIconic.argtypes = [wintypes.HWND]
    api.IsZoomed.argtypes = [wintypes.HWND]
    api.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    ctypes.windll.dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    api.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.c_void_p]
    api.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    return api


def window_included(info, own_pid):
    return (info['visible'] and not info['minimized'] and not info['cloaked']
            and not info['tool'] and not info['owned'] and bool(info['title'])
            and info['pid'] != own_pid and info['class'] not in IGNORED_CLASSES
            and info.get('app', '').lower() not in IGNORED_APPS)


def native_windows(include_minimized=False):
    if os.name != 'nt':
        return []
    api, rows = user32(), []
    class Placement(ctypes.Structure):
        _fields_ = [('length', wintypes.UINT), ('flags', wintypes.UINT),
                    ('showCmd', wintypes.UINT), ('min', wintypes.POINT),
                    ('max', wintypes.POINT), ('normal', wintypes.RECT)]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(hwnd, unused):
        try:
            if not api.IsWindowVisible(hwnd):
                return True
            length = api.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(min(length + 1, 4096))
            api.GetWindowTextW(hwnd, title, len(title))
            cls = ctypes.create_unicode_buffer(256)
            api.GetClassNameW(hwnd, cls, 256)
            pid = wintypes.DWORD()
            api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            cloaked = wintypes.DWORD()
            ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
            p = psutil.Process(pid.value)
            info = dict(hwnd=int(hwnd), pid=pid.value, created=p.create_time(),
                        title=title.value, **{'class': cls.value}, app=p.name(), exe=p.exe(),
                        visible=True, minimized=bool(api.IsIconic(hwnd)),
                        cloaked=bool(cloaked.value), tool=bool(api.GetWindowLongPtrW(hwnd, -20) & 0x80),
                        owned=bool(api.GetWindow(hwnd, 4)))
            check = {**info, 'minimized': False} if include_minimized else info
            if not window_included(check, os.getpid()):
                return True
            placement = Placement(); placement.length = ctypes.sizeof(placement)
            if api.GetWindowPlacement(hwnd, ctypes.byref(placement)):
                rect = placement.normal
            else:
                rect = wintypes.RECT(); api.GetWindowRect(hwnd, ctypes.byref(rect))
            info.update(rect=[rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top],
                        maximized=bool(api.IsZoomed(hwnd)))
            if info['rect'][2] <= 0 or info['rect'][3] <= 0:
                return True
            rows.append(info)
        except (psutil.Error, OSError, AttributeError):
            pass
        return True
    api.EnumWindows(visit, 0)
    return rows


def explorer_tabs(shell, hwnd):
    result = []
    for window in shell.Windows():
        try:
            if int(window.HWND) == hwnd and Path(window.FullName).name.lower() == 'explorer.exe':
                result.append({'path': str(window.Document.Folder.Self.Path),
                               'title': str(window.LocationName)})
        except Exception:
            continue
    return result


def terminal_sessions(pid):
    """Only highest shell descendants of this exact host; no cmdline/buffer reads."""
    result = []
    try:
        children = psutil.Process(pid).children(recursive=True)
        shells = {p.pid: p for p in children if p.name().lower() in SHELLS}
        for process in sorted(shells.values(), key=lambda p: p.create_time()):
            if any(parent.pid in shells for parent in process.parents()):
                continue
            try:
                result.append({'exe': process.exe(), 'cwd': process.cwd(), 'title': process.name()})
            except psutil.Error:
                continue
    except psutil.Error:
        pass
    return result


def capture_desktop():
    if os.name != 'nt':
        raise RuntimeError('工作现场目前只支持 Windows。')
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    warnings, result = [], []
    try:
        all_windows = native_windows(include_minimized=True)
        try:
            shell = win32com.client.Dispatch('Shell.Application')
        except Exception:
            shell = None
            warnings.append('资源管理器标签页读取失败，未推测缺失路径。')
        for info in all_windows:
            if info['minimized']:
                continue
            row = {k: info[k] for k in ('hwnd', 'pid', 'created', 'title', 'app', 'exe', 'rect', 'maximized')}
            row.update(kind='app', tabs=[], sessions=[], detail='只能重新打开软件，文档和未保存内容不在此存档内')
            name = info['app'].lower()
            if info['class'] == 'CabinetWClass':
                row.update(kind='explorer', tabs=explorer_tabs(shell, info['hwnd']) if shell else [],
                           detail='恢复文件夹标签页和窗口位置；标签顺序及选中项可能不同')
                if not row['tabs']:
                    row['detail'] = '未读到文件夹路径，暂不能恢复'
            elif name in TERMINALS:
                same_host = sum(w['pid'] == info['pid'] and w['app'].lower() in TERMINALS for w in all_windows)
                sessions = terminal_sessions(info['pid']) if same_host == 1 else []
                row.update(kind='terminal', sessions=sessions,
                           detail='恢复进程目录为新终端标签；不接续命令，分屏会展开为标签，目录可能与 pwd 不同')
                if not sessions:
                    row['detail'] = '无法对应终端会话，只能重新打开终端；未推测工作目录'
            elif name in SHELLS or info['class'] == 'ConsoleWindowClass':
                candidates = [psutil.Process(info['pid'])]
                if name not in SHELLS:
                    try:
                        candidates = [psutil.Process(info['pid']).parent()]
                    except psutil.Error:
                        candidates = []
                sessions = []
                for process in candidates:
                    try:
                        if process and process.name().lower() in SHELLS:
                            sessions.append({'exe': process.exe(), 'cwd': process.cwd(), 'title': process.name()})
                    except psutil.Error:
                        pass
                row.update(kind='console', sessions=sessions,
                           detail='重新打开可读取的进程目录，不接续运行中的命令')
                if not sessions:
                    row['detail'] = '控制台所属 shell 未确定，暂不能恢复'
            elif name in BROWSERS:
                row.update(kind='browser', detail='浏览器标签页请从“每日存档”恢复')
            elif name == 'applicationframehost.exe':
                row.update(exe='', detail='已记录窗口；此系统宿主不是软件入口，请在原应用中恢复')
            result.append(row)
        return {'version': 1, 'windows': result, 'warnings': warnings}
    finally:
        pythoncom.CoUninitialize()


def safe_folder(path):
    return isinstance(path, str) and bool(path) and '\x00' not in path and Path(path).is_absolute() and Path(path).is_dir()


def launch_exe(path):
    return isinstance(path, str) and Path(path).is_absolute() and Path(path).suffix.lower() == '.exe' and Path(path).is_file()


def restorable(row):
    if row['kind'] == 'explorer':
        return any(safe_folder(t['path']) or re.fullmatch(r'::\{[\da-fA-F-]{36}\}', t['path']) for t in row.get('tabs', []))
    if row['kind'] in ('terminal', 'console'):
        return bool(row.get('sessions')) or (row['kind'] == 'terminal' and launch_exe(row.get('exe')))
    return row['kind'] == 'app' and launch_exe(row.get('exe'))


def place_window(hwnd, row):
    api = user32()
    x, y, width, height = row.get('rect', [80, 80, 1000, 700])
    # Clamp to the current desktop after monitor changes, keeping a visible caption.
    left, top, sw, sh = [api.GetSystemMetrics(n) for n in (76, 77, 78, 79)]
    width, height = max(320, min(width, sw)), max(240, min(height, sh))
    x, y = max(left, min(x, left + sw - width)), max(top, min(y, top + sh - height))
    api.SetWindowPos(hwnd, None, x, y, width, height, 0x0014)  # no activation/z-order changes
    if row.get('maximized'):
        api.ShowWindow(hwnd, 3)


def _new_explorer(shell, path):
    before = {int(w.HWND) for w in shell.Windows()}
    shell.Explore(path)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        for window in shell.Windows():
            try:
                if int(window.HWND) not in before and str(window.Document.Folder.Self.Path).casefold() == path.casefold():
                    return window
            except Exception:
                continue
        time.sleep(.1)
    raise RuntimeError('未能确认新建的资源管理器窗口，已停止修改标签页。')


def restore_explorer(row):
    import pythoncom
    import win32com.client
    from pywinauto import Desktop
    pythoncom.CoInitialize()
    restored, messages = 0, []
    try:
        shell = win32com.client.Dispatch('Shell.Application')
        paths = [t['path'] for t in row['tabs'] if safe_folder(t['path']) or re.fullmatch(r'::\{[\da-fA-F-]{36}\}', t['path'])]
        if not paths:
            return 0, ['文件夹已不存在或路径不受支持。']
        window = _new_explorer(shell, paths[0]); hwnd = int(window.HWND)
        restored = 1
        for path in paths[1:]:
            previous = [w for w in shell.Windows() if int(w.HWND) == hwnd]
            # Only invoke the AddButton of the newly created, verified window.
            wrapper = Desktop(backend='uia').window(handle=hwnd).wrapper_object()
            buttons = [b for b in wrapper.descendants(control_type='Button') if b.element_info.automation_id == 'AddButton']
            if len(buttons) != 1:
                messages.append('当前系统不支持创建资源管理器标签，剩余路径改为单独窗口。')
                for remaining in paths[restored:]:
                    _new_explorer(shell, remaining); restored += 1
                break
            buttons[0].invoke()
            deadline, new = time.monotonic() + 4, []
            while time.monotonic() < deadline:
                now = [w for w in shell.Windows() if int(w.HWND) == hwnd]
                new = [w for w in now if not any(w._oleobj_ == old._oleobj_ for old in previous)]
                if new:
                    break
                time.sleep(.1)
            if len(new) != 1:
                raise RuntimeError('无法确认新标签页，已停止继续恢复。')
            new[0].Navigate2(path)
            restored += 1
        place_window(hwnd, row)
        if restored < len(row['tabs']):
            messages.append('部分文件夹已不存在，已跳过。')
        return restored, messages
    finally:
        pythoncom.CoUninitialize()


def terminal_commands(row, host):
    """Separate argument lists prevent saved paths/titles from becoming shell code."""
    window_name = 'ae-keeper-' + uuid.uuid4().hex
    commands = []
    for session in row.get('sessions', []):
        exe, cwd = session.get('exe', ''), session.get('cwd', '')
        if Path(exe).name.lower() not in SHELLS or not launch_exe(exe) or not safe_folder(cwd):
            continue
        if row['kind'] == 'terminal':
            # WT interprets semicolons as command separators even inside arguments.
            # Never submit those values to its command parser.
            if ';' in cwd or ';' in exe:
                continue
            commands.append(([host, '-w', window_name, 'new-tab', '-d', cwd, exe], None))
        else:
            commands.append(([exe], cwd))
    return commands


def restore_desktop(payload, selected, progress=None):
    """Explicit user action only. Existing windows are never closed or navigated."""
    if os.name != 'nt':
        raise RuntimeError('工作现场恢复目前只支持 Windows。')
    rows, messages, count = payload['windows'], [], 0
    current = capture_desktop()['windows']
    for index in sorted(set(selected)):
        if index < 0 or index >= len(rows):
            continue
        row = rows[index]
        if progress:
            progress(f'正在恢复：{row["app"]}')
        try:
            if any(r['hwnd'] == row.get('hwnd') and r['pid'] == row.get('pid') and r['created'] == row.get('created') for r in current):
                messages.append(row['app'] + '：原窗口仍打开，已保留。')
                continue
            if row['kind'] == 'explorer':
                opened, notes = restore_explorer(row)
                count += int(opened > 0); messages.extend(notes)
            elif row['kind'] in ('terminal', 'console'):
                alias = Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft/WindowsApps/wt.exe'
                host = str(alias) if alias.is_file() else row['exe']
                commands = terminal_commands(row, host)
                if not commands and row['kind'] == 'terminal' and launch_exe(row['exe']):
                    commands = [([host, '-w', '-1'], None)]
                    messages.append('终端目录未能恢复，已打开默认会话。')
                if not commands:
                    messages.append('终端程序或目录不可用，已跳过。')
                    continue
                before = {w['hwnd'] for w in native_windows()}
                for args, cwd in commands:
                    subprocess.Popen(args, cwd=cwd)
                count += 1
                # Best effort positioning only when a single new host is identified.
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    new = [w for w in native_windows() if w['hwnd'] not in before and (
                        w['app'].lower() in TERMINALS if row['kind'] == 'terminal' else w['class'] == 'ConsoleWindowClass')]
                    if len(new) == 1:
                        place_window(new[0]['hwnd'], row); break
                    time.sleep(.15)
            elif row['kind'] == 'app' and launch_exe(row['exe']):
                if any(w['exe'].casefold() == row['exe'].casefold() for w in current):
                    messages.append(row['app'] + '：软件已打开，未重复启动。'); continue
                before = {w['hwnd'] for w in native_windows()}
                subprocess.Popen([row['exe']]); count += 1
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    new = [w for w in native_windows() if w['hwnd'] not in before and w['exe'].casefold() == row['exe'].casefold()]
                    if len(new) == 1:
                        place_window(new[0]['hwnd'], row); break
                    time.sleep(.15)
                current.append(row)
            else:
                messages.append(row['app'] + '：该类型请在原软件中恢复。')
        except Exception as error:
            messages.append(row['app'] + '：' + str(error))
    return {'opened': count, 'messages': messages}
