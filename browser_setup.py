"""Open the opt-in page in a newly created browser window.

Chromium ignores inspect URLs passed on the command line. Use the accessible
address control of the newly created blank window instead. Existing
windows and the opt-in checkbox are never edited.
"""
import ctypes
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


SETTINGS_URLS = {
    'Chrome': 'chrome://inspect/#remote-debugging',
    'Edge': 'edge://inspect/#remote-debugging',
}


@dataclass(frozen=True)
class SetupResult:
    opened: bool
    url: str
    reason: str = ''


def _browser_handles(exe):
    """Get visible Chromium HWNDs from this exact executable, without titles."""
    import psutil
    user32 = ctypes.windll.user32
    expected = os.path.normcase(os.path.abspath(exe))
    handles = set()
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(hwnd, unused):
        if not user32.IsWindowVisible(hwnd):
            return True
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, 256)
        if name.value != 'Chrome_WidgetWin_1':
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            actual = os.path.normcase(os.path.abspath(psutil.Process(pid.value).exe()))
            if actual == expected:
                handles.add(int(hwnd))
        except (psutil.Error, OSError):
            pass
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return handles


def _address(window):
    matches = []
    for control in window.descendants(control_type='Edit'):
        name = (control.element_info.name or '').lower()
        automation_id = (control.element_info.automation_id or '').lower()
        # UIA window_text() can be the current value (e.g. about:blank), rather
        # than the accessible label. Chromium's native omnibox ID is view_1012.
        known_id = automation_id in ('view_1012', 'omnibox', 'address and search bar')
        known_name = ('地址' in name or 'address' in name or '输入网址' in name
                      or '輸入網址' in name or '输入 url' in name or 'search or enter' in name)
        if known_id or known_name:
            matches.append(control)
    return matches[0] if len(matches) == 1 else None


def _address_value(control):
    try:
        return control.get_value().strip()
    except Exception:
        return None


def _has_opt_in(window):
    # Inspect existence only: never toggle a browser permission automatically.
    for control in window.descendants(control_type='CheckBox'):
        name = control.window_text().lower()
        if 'remote debugging' in name or '远程调试' in name:
            return True
    return False


def open_settings(browser, exe, user_data_root, timeout=18):
    url = SETTINGS_URLS[browser]
    if not Path(exe).is_file():
        return SetupResult(False, url, f'没有找到{browser}。请安装浏览器或检查安装位置。')
    if os.name != 'nt':
        return SetupResult(False, url, '自动打开连接设置目前只支持 Windows。')
    initialized = False
    try:
        import pythoncom
        pythoncom.CoInitialize()
        initialized = True
        from pywinauto import Desktop
        before = _browser_handles(exe)
        # Chromium's command-line allowlist accepts exactly about:blank; even
        # adding a fragment can make it open a new-tab page instead.
        blank = 'about:blank'
        subprocess.Popen([exe, '--user-data-dir=' + str(Path(user_data_root)),
                          '--new-window', blank], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        deadline, selected = time.monotonic() + timeout, None
        desktop = Desktop(backend='uia')
        while time.monotonic() < deadline:
            new_handles = _browser_handles(exe) - before
            candidates = []
            for handle in new_handles:
                try:
                    window = desktop.window(handle=handle).wrapper_object()
                    address = _address(window)
                    if address is not None and _address_value(address) in ('', blank):
                        candidates.append((window, address))
                except Exception:
                    continue
            if len(candidates) > 1:
                return SetupResult(False, url, '同时出现多个新空白浏览器窗口，无法安全确认。请复制下面的网址手动打开。')
            if candidates:
                selected = candidates[0]
            if selected:
                break
            time.sleep(.2)
        if selected is None:
            return SetupResult(False, url, '未能确认软件新建的空白窗口。现有窗口没有被修改；请复制下面的网址到浏览器地址栏。')
        window, address = selected
        # Recheck immediately before changing the address; the user may have
        # navigated this new window while it was being identified.
        if _address_value(address) not in ('', blank):
            return SetupResult(False, url, '新窗口已被操作，已停止自动输入。请手动打开下面的网址。')
        window.set_focus()
        address.click_input()
        address.set_edit_text(url)
        if _address_value(address) != url:
            return SetupResult(False, url, '浏览器地址栏未接受设置网址。请手动打开下面的网址。')
        time.sleep(.2)
        address.type_keys('{ENTER}', set_foreground=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _has_opt_in(window):
                return SetupResult(True, url)
            time.sleep(.25)
        return SetupResult(False, url, '设置页已尝试打开，但没有找到远程调试开关。浏览器版本、组织策略或页面加载可能影响此功能；请手动核对下面的网址。')
    except Exception as error:
        return SetupResult(False, url, '自动打开设置未完成：' + str(error))
    finally:
        if initialized:
            pythoncom.CoUninitialize()
