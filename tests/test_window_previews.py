import base64
import copy
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_windows import capture_desktop, window_included
from storage import Archive
from window_previews import attach_previews, encode_preview, image_readable
from workspace_model import clean_workspace, demo_workspace


class FakeProcess:
    def __init__(self, target, args, daemon):
        self.args, self.alive, self.terminated, self.closed = args, False, False, False

    def start(self):
        pass

    def join(self, timeout):
        pass

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.alive, self.terminated = False, True

    def close(self):
        self.closed = True


class WindowPreviewTests(unittest.TestCase):
    def setUp(self):
        self.payload = clean_workspace(demo_workspace())
        for index, row in enumerate(self.payload['windows']):
            row.update(hwnd=index + 11, pid=22, created=33)

    def test_minimized_taskbar_window_is_included_by_selected_scope(self):
        info = dict(visible=True, minimized=True, cloaked=False, tool=False, owned=False,
                    title='Folder', pid=1, app='explorer.exe', **{'class': 'CabinetWClass'})
        self.assertFalse(window_included(info, 999))
        self.assertTrue(window_included(info, 999, include_minimized=True))
        self.assertFalse(window_included({**info, 'visible': False}, 999, True))
        self.assertFalse(window_included({**info, 'tool': True}, 999, True))

    def test_owned_explicit_appwindow_is_not_dropped(self):
        info = dict(visible=True, minimized=False, cloaked=False, tool=False, owned=True,
                    appwindow=True, title='Work', pid=1, app='app.exe', **{'class': 'app'})
        self.assertTrue(window_included(info, 999, True))
        self.assertFalse(window_included({**info, 'appwindow': False}, 999, True))

    def test_default_capture_keeps_minimized_explorer_and_its_tabs(self):
        info = {**self.payload['windows'][0], 'class': 'CabinetWClass', 'minimized': True}
        with patch('desktop_windows.native_windows', return_value=[info]), patch('win32com.client.Dispatch'), \
             patch('desktop_windows.explorer_tabs', return_value=info['tabs']):
            value = capture_desktop()
            self.assertEqual(len(value['windows']), 1)
            self.assertEqual(len(value['windows'][0]['tabs']), 3)
            self.assertTrue(value['windows'][0]['minimized'])
            self.assertEqual(capture_desktop(include_minimized=False)['windows'], [])

    def test_unreadable_minimized_frame_uses_only_matching_window_cache(self):
        previous = copy.deepcopy(self.payload)
        previous['windows'][0]['preview']['time'] = 77
        self.payload['windows'][1]['hwnd'] = 999
        context = Mock(); context.Process.side_effect = FakeProcess
        with patch('window_previews.mp.get_context', return_value=context):
            attach_previews(self.payload, previous)
        self.assertEqual(self.payload['windows'][0]['preview']['status'], 'cached')
        self.assertEqual(self.payload['windows'][0]['preview']['time'], 77)
        self.assertEqual(self.payload['windows'][1]['preview']['status'], 'unavailable')
        self.assertFalse(self.payload['windows'][1]['preview']['image'])

    def test_disabling_images_never_starts_capture_and_does_not_reuse_images(self):
        with patch('window_previews.mp.get_context') as context:
            attach_previews(self.payload, self.payload, enabled=False)
            context.assert_not_called()
        self.assertTrue(all(w['preview']['status'] == 'disabled' and not w['preview']['image'] for w in self.payload['windows']))

    def test_hung_renderer_is_terminated_and_window_metadata_is_retained(self):
        process = FakeProcess(None, (), True); process.alive = True
        context = Mock(); context.Process.return_value = process
        with patch('window_previews.mp.get_context', return_value=context):
            attach_previews(self.payload, timeout=.01)
        self.assertTrue(process.terminated and process.closed)
        self.assertEqual(len(self.payload['windows']), 3)

    def test_caption_only_black_client_is_rejected_as_an_image(self):
        picture = Image.new('RGB', (600, 350), 'black')
        picture.paste('white', (0, 0, 600, 35))
        self.assertFalse(image_readable(picture))
        self.assertTrue(image_readable(Image.new('RGB', (600, 350), 'white')))
        encoded = encode_preview(Image.new('RGB', (1600, 1000), '#0d766d'))
        with Image.open(BytesIO(encoded)) as result:
            self.assertEqual(result.size, (640, 400))

    def test_failed_renderer_launch_does_not_discard_window_records(self):
        context = Mock(); process = FakeProcess(None, (), True)
        process.start = Mock(side_effect=OSError('capture unavailable'))
        context.Process.return_value = process
        with patch('window_previews.mp.get_context', return_value=context):
            attach_previews(self.payload)
        self.assertEqual(len(self.payload['windows']), 3)
        self.assertTrue(all(w['preview']['status'] == 'unavailable' for w in self.payload['windows']))

    def test_images_and_minimized_flags_round_trip_through_local_backup(self):
        with tempfile.TemporaryDirectory() as root:
            first, second = Archive(Path(root) / 'first'), Archive(Path(root) / 'second')
            try:
                first.capture_workspace(self.payload, manual=True)
                backup = Path(root) / 'backup.json'; first.export(backup)
                second.import_backup(backup)
                window = second.listing('workspaces')[0]['payload']['windows'][0]
                self.assertTrue(window['minimized'])
                self.assertEqual(window['preview']['image'], self.payload['windows'][0]['preview']['image'])
                self.assertEqual(window['hwnd'], 0)
            finally:
                first.db.close(); second.db.close()

    def test_invalid_image_rejects_import_without_changing_existing_archives(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Archive(root)
            try:
                archive.capture_workspace(self.payload, manual=True)
                backup = Path(root) / 'backup.json'; archive.export(backup)
                value = json.loads(backup.read_text(encoding='utf-8'))
                payload = json.loads(value['workspaces'][0]['payload'])
                payload['windows'][0]['preview']['image'] = 'not-an-image'
                value['workspaces'][0]['payload'] = json.dumps(payload)
                backup.write_text(json.dumps(value), encoding='utf-8')
                before = archive.listing('workspaces')
                with self.assertRaises(ValueError):
                    archive.import_backup(backup)
                self.assertEqual(archive.listing('workspaces'), before)
            finally:
                archive.db.close()

    def test_old_archives_remain_readable_without_photos(self):
        for row in self.payload['windows']:
            row.pop('preview'); row.pop('minimized')
        clean = clean_workspace(self.payload)
        self.assertFalse(clean['windows'][0]['minimized'])
        self.assertNotIn('preview', clean['windows'][0])

    def test_oversized_image_is_rejected_before_ui_decoding(self):
        output = BytesIO(); Image.new('RGB', (1025, 20), 'white').save(output, format='PNG')
        self.payload['windows'][0]['preview']['image'] = base64.b64encode(output.getvalue()).decode()
        with self.assertRaises(ValueError):
            clean_workspace(self.payload)


if __name__ == '__main__':
    unittest.main()
