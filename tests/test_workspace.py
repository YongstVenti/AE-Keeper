import copy
import json
from pathlib import Path
import queue
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_windows import window_included, terminal_commands, restore_desktop, capture_desktop, restorable
from engine import Engine
from storage import Archive
from workspace_model import clean_workspace, demo_workspace


class WorkspaceStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive = Archive(self.root / 'data')
        self.payload = demo_workspace()
        self.stamp = time.time()

    def tearDown(self):
        self.archive.db.close(); self.temp.cleanup()

    def test_auto_replaces_only_daily_auto_and_keeps_manual(self):
        self.archive.capture_workspace(self.payload, timestamp=self.stamp)
        self.archive.capture_workspace(self.payload, manual=True, timestamp=self.stamp)
        changed = copy.deepcopy(self.payload); changed['windows'][0]['title'] = 'changed'
        self.archive.capture_workspace(changed, timestamp=self.stamp + 1)
        rows = self.archive.listing('workspaces')
        self.assertEqual(len(rows), 2)
        self.assertEqual(next(r for r in rows if r['origin'] == 'auto')['payload']['windows'][0]['title'], 'changed')
        self.assertEqual(next(r for r in rows if r['origin'] == 'manual')['payload']['windows'][0]['title'], '项目文件')

    def test_empty_does_not_erase_and_old_auto_cannot_replace_new(self):
        self.archive.capture_workspace(self.payload, timestamp=self.stamp)
        self.assertFalse(self.archive.capture_workspace({'version': 1, 'windows': []}, timestamp=self.stamp + 1))
        self.assertFalse(self.archive.capture_workspace(self.payload, timestamp=self.stamp - 1))
        self.assertEqual(len(self.archive.listing('workspaces')), 1)

    def test_same_day_restart_retains_last_session_before_auto_update(self):
        self.archive.capture_workspace(self.payload, timestamp=self.stamp)
        self.archive.preserve_workspace_on_start(); self.archive.preserve_workspace_on_start()
        changed = copy.deepcopy(self.payload); changed['windows'] = [changed['windows'][2]]
        self.archive.capture_workspace(changed, timestamp=self.stamp + 1)
        rows = self.archive.listing('workspaces')
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(next(r for r in rows if r['origin'] == 'previous')['payload']['windows']), 3)
        self.assertEqual(len(next(r for r in rows if r['origin'] == 'auto')['payload']['windows']), 1)

    def test_reopen_export_import_notes_and_browser_compatibility(self):
        self.payload['windows'][0].update(hwnd=123, pid=456, created=999)
        self.archive.capture_workspace(self.payload, manual=True)
        rid = self.archive.listing('workspaces')[0]['id']
        self.archive.note('workspaces', rid, '一个工作节点')
        exported = self.root / 'backup.json'; self.archive.export(exported)
        self.archive.import_backup(exported)
        self.assertEqual(len(self.archive.listing('workspaces')), 1)
        self.assertEqual(self.archive.listing('workspaces')[0]['payload']['windows'][0]['hwnd'], 123)
        other = Archive(self.root / 'other')
        try:
            other.import_backup(exported); other.import_backup(exported)
            rows = other.listing('workspaces')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['note'], '一个工作节点')
            self.assertEqual(rows[0]['payload']['windows'][0]['hwnd'], 0)
            self.assertEqual(len(rows[0]['payload']['windows'][0]['tabs']), 3)
            legacy = json.loads(exported.read_text(encoding='utf-8')); legacy.pop('workspaces')
            exported.write_text(json.dumps(legacy), encoding='utf-8')
            other.import_backup(exported)
            self.assertEqual(len(other.listing('workspaces')), 1)
        finally:
            other.db.close()
        self.archive.db.close(); self.archive = Archive(self.root / 'data')
        self.assertEqual(self.archive.workspace(rid)['note'], '一个工作节点')

    def test_invalid_import_rolls_back_whole_workspace_transaction(self):
        self.archive.capture_workspace(self.payload, manual=True)
        exported = self.root / 'backup.json'; self.archive.export(exported)
        value = json.loads(exported.read_text(encoding='utf-8'))
        value['workspaces'][0]['last_seen'] += 1
        invalid = copy.deepcopy(value['workspaces'][0]); invalid['payload'] = '{}'
        value['workspaces'].append(invalid)
        exported.write_text(json.dumps(value), encoding='utf-8')
        before = self.archive.listing('workspaces')
        with self.assertRaises(ValueError):
            self.archive.import_backup(exported)
        self.assertEqual(self.archive.listing('workspaces'), before)

    def test_previous_session_backup_can_be_merged_into_same_archive(self):
        self.archive.capture_workspace(self.payload)
        self.archive.preserve_workspace_on_start()
        exported = self.root / 'previous.json'; self.archive.export(exported)
        self.archive.import_backup(exported); self.archive.import_backup(exported)
        self.assertEqual(len(self.archive.listing('workspaces')), 2)

    def test_search_date_and_delete_leave_browser_snapshots(self):
        rows = [dict(browser=b, window='1', target=b, url='https://example.com/', title='example', source='test') for b in ('Chrome', 'Edge')]
        self.archive.capture(rows, ['Chrome', 'Edge'])
        self.archive.capture_workspace(self.payload)
        self.assertEqual(len(self.archive.listing('workspaces', query='参考资料')), 1)
        self.assertEqual(self.archive.listing('workspaces', day='2000-01-01'), [])
        self.archive.delete('workspaces', self.archive.listing('workspaces')[0]['id'])
        self.assertEqual(self.archive.listing('workspaces'), [])
        self.assertEqual(len(self.archive.listing('snapshots')), 1)

    def test_capacity_rejects_oversized_payload_without_erasing(self):
        self.archive.capture_workspace(self.payload)
        large = copy.deepcopy(self.payload)
        large['windows'] = [copy.deepcopy(large['windows'][0]) for _ in range(200)]
        for w in large['windows']:
            w['tabs'] = [{'path': 'x' * 4096, 'title': 'y' * 4096} for _ in range(128)]
        with self.assertRaises(ValueError):
            self.archive.capture_workspace(large)
        self.assertEqual(len(self.archive.listing('workspaces')), 1)


class DesktopRestoreTests(unittest.TestCase):
    def test_excludes_minimized_hidden_tool_owned_cloaked_and_self(self):
        row = dict(visible=True, minimized=False, cloaked=False, tool=False, owned=False,
                   title='work', pid=123, app='notepad.exe', **{'class': 'normal'})
        self.assertTrue(window_included(row, 999))
        for key, value in [('visible', False), ('minimized', True), ('cloaked', True),
                           ('tool', True), ('owned', True), ('title', ''), ('pid', 999)]:
            self.assertFalse(window_included({**row, key: value}, 999))

    def test_terminal_uses_shell_exe_and_directory_never_old_commands(self):
        row = clean_workspace(demo_workspace())['windows'][1]
        row['sessions'][0].update(cwd=r'C:\Projects\a & b', exe=r'C:\PowerShell\pwsh.exe', command='Remove-Item anything')
        with patch('desktop_windows.launch_exe', return_value=True), patch('desktop_windows.safe_folder', return_value=True):
            commands = terminal_commands(row, 'wt.exe')
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0][0][-3:], ['-d', r'C:\Projects\a & b', r'C:\PowerShell\pwsh.exe'])
            self.assertNotIn('Remove-Item anything', commands[0][0])
            row['sessions'][0]['cwd'] = r'C:\a;new-tab'
            self.assertEqual(terminal_commands(row, 'wt.exe'), [])

    def test_store_app_window_is_recorded_without_launching_system_host(self):
        row = clean_workspace(demo_workspace())['windows'][2]
        row.update(app='ApplicationFrameHost.exe', minimized=False, **{'class': 'ApplicationFrameWindow'})
        with patch('desktop_windows.native_windows', return_value=[row]), patch('win32com.client.Dispatch'):
            captured = capture_desktop()['windows']
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]['exe'], '')
        self.assertFalse(restorable(captured[0]))

    def test_restore_is_selected_only_and_keeps_live_original(self):
        payload = clean_workspace(demo_workspace())
        payload['windows'][0].update(hwnd=12, pid=34, created=56)
        with patch('desktop_windows.capture_desktop', return_value={'windows': [payload['windows'][0]]}), \
             patch('desktop_windows.restore_explorer') as explorer, patch('desktop_windows.subprocess.Popen') as popen:
            result = restore_desktop(payload, [0])
            self.assertEqual(result['opened'], 0)
            explorer.assert_not_called(); popen.assert_not_called()

    def test_capture_format_rejects_commands_and_bounds(self):
        value = demo_workspace(); value['windows'][0]['command'] = 'private command'
        self.assertNotIn('command', clean_workspace(value)['windows'][0])
        value['windows'][0]['rect'] = [0, 0, -10, 20]
        with self.assertRaises(ValueError):
            clean_workspace(value)

    def test_demo_never_captures_or_restores_real_desktop(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(folder, queue.Queue(), demo=True)
            try:
                with patch('engine.capture_desktop') as capture, patch('engine.restore_desktop') as restore:
                    engine.handle('workspace_save', {})
                    engine.handle('workspace_restore', {'id': 1, 'selected': [0]})
                    capture.assert_not_called(); restore.assert_not_called()
                    self.assertEqual(len(engine.archive.listing('workspaces')), 1)
            finally:
                engine.archive.db.close()


if __name__ == '__main__':
    unittest.main()
