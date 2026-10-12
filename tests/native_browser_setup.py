"""Manual Windows integration check. Own isolated profiles only; no opt-ins."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
import psutil
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_setup import _address, _address_value, _browser_handles, _has_opt_in, open_settings
from browser_setup import SETTINGS_URLS
from engine import browser_exe
from pywinauto import Desktop


root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=True)
for browser in ('Chrome', 'Edge'):
    folder, exe = root / browser, browser_exe(browser)
    subprocess.Popen([exe, '--user-data-dir=' + str(folder), '--no-first-run',
                      '--no-default-browser-check', '--disable-background-networking',
                      '--new-window', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        own_handles = set()
        ready = False
        while time.monotonic() < deadline:
            for handle in _browser_handles(exe):
                window = Desktop(backend='uia').window(handle=handle).wrapper_object()
                try:
                    process = psutil.Process(window.process_id())
                    if '--user-data-dir=' + str(folder) in process.cmdline():
                        own_handles.add(handle)
                        address = _address(window)
                        ready = ready or (address is not None and _address_value(address) == 'about:blank')
                except psutil.Error:
                    continue
            if ready:
                break
            time.sleep(.2)
        assert ready, 'Isolated fixture window missing'
        original = {h: _address_value(_address(Desktop(backend='uia').window(handle=h).wrapper_object())) for h in own_handles}
        results = []
        worker = threading.Thread(target=lambda: results.append(open_settings(browser, exe, folder)))
        worker.start()
        worker.join(timeout=45)
        assert not worker.is_alive(), 'Settings worker hung'
        assert results, 'Settings worker returned nothing'
        result = results[0]
        if not result.opened:
            diagnosis = []
            for handle in _browser_handles(exe):
                window = Desktop(backend='uia').window(handle=handle).wrapper_object()
                try:
                    if '--user-data-dir=' + str(folder) not in psutil.Process(window.process_id()).cmdline():
                        continue
                    address = _address(window)
                    value_error = ''
                    try:
                        value = address.get_value() if address else None
                    except Exception as error:
                        value, value_error = None, str(error)
                    diagnosis.append({'original': handle in own_handles, 'address_found': address is not None,
                                      'value': value, 'value_error': value_error,
                                      'controls': [(c.element_info.control_type, c.window_text(), c.element_info.automation_id) for c in window.descendants() if c.element_info.control_type in ('Edit', 'ComboBox', 'Document', 'CheckBox', 'Text')]})
                except psutil.Error:
                    continue
            print(json.dumps({'browser': browser, 'reason': result.reason, 'diagnosis': diagnosis}, ensure_ascii=True), flush=True)
        assert result.opened, result.reason
        for handle, old_address in original.items():
            window = Desktop(backend='uia').window(handle=handle).wrapper_object()
            assert _address_value(_address(window)) == old_address, 'Original window was changed'
        new_handles = _browser_handles(exe) - own_handles
        verified = False
        for handle in new_handles:
            window = Desktop(backend='uia').window(handle=handle).wrapper_object()
            process = psutil.Process(window.process_id())
            if '--user-data-dir=' + str(folder) not in process.cmdline():
                continue
            if _address(window) is None or _address_value(_address(window)) != SETTINGS_URLS[browser]:
                continue  # Chromium also owns native popup windows.
            assert _has_opt_in(window), 'No visible checkbox'
            checks = [c for c in window.descendants(control_type='CheckBox')
                      if 'remote debugging' in c.window_text().lower() or '远程调试' in c.window_text()]
            assert checks and all(c.get_toggle_state() == 0 for c in checks), 'The test must never enable permissions'
            verified = True
        assert verified
        print(json.dumps({'browser': browser, 'opened': True, 'checkbox_visible': True,
                          'checkbox_left_off': True, 'original_window_unchanged': True}, ensure_ascii=False), flush=True)
    finally:
        # Identify only the browser parents carrying our unique isolated root.
        # Never kill by executable name or touch the user's browser processes.
        owned = []
        for process in psutil.process_iter(['pid', 'cmdline']):
            try:
                if '--user-data-dir=' + str(folder) in (process.info['cmdline'] or []):
                    owned.extend(process.children(recursive=True))
                    owned.append(process)
            except psutil.Error:
                pass
        for process in reversed(owned):
            try:
                process.terminate()
            except psutil.Error:
                pass
        psutil.wait_procs(owned, timeout=5)
