import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Engine
from browser_setup import SetupResult


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = Engine(self.root/'archive', queue.Queue())
        self.engine.archive.save_settings({'chrome_root': str(self.root/'Chrome'), 'edge_root': str(self.root/'Edge')})

    def tearDown(self):
        self.engine.archive.db.close()
        self.temp.cleanup()

    def endpoint(self, browser):
        folder = self.root/browser
        folder.mkdir()
        (folder/'DevToolsActivePort').write_text('9222\n/devtools/browser/test', encoding='utf-8')

    def test_setup_opens_once_and_connects_after_toggle(self):
        with patch('engine.browser_exe', return_value='browser.exe'), patch('engine.open_settings', return_value=SetupResult(True, 'chrome://inspect/#remote-debugging')) as launch, patch('engine.BrowserConnection') as factory:
            client = Mock()
            client.tabs.return_value = [{'browser': 'Chrome', 'window': '1', 'target': 't', 'title': 'Example', 'url': 'https://example.com/', 'source': '实时连接'}]
            factory.return_value = client
            self.engine.handle('connect_both', {})
            self.assertEqual(launch.call_count, 2)
            self.engine.last_connection_check = 0
            self.engine.check_connections()
            self.assertEqual(launch.call_count, 2)
            self.endpoint('Chrome')
            self.engine.last_connection_check = 0
            self.engine.check_connections()
            self.assertIn('Chrome', self.engine.clients)
            self.assertNotIn('Chrome', self.engine.pending_connections)
            self.assertEqual(factory.call_count, 1)

    def test_declined_connection_does_not_repeat_dialogs(self):
        self.endpoint('Chrome')
        with patch('engine.browser_exe', return_value='browser.exe'), patch('engine.open_settings', return_value=SetupResult(True, 'chrome://inspect/#remote-debugging')), patch('engine.BrowserConnection', side_effect=RuntimeError('not allowed')) as factory:
            self.engine.handle('connect_both', {})
            for _ in range(3):
                self.engine.last_connection_check = 0
                self.engine.check_connections()
            self.assertEqual(factory.call_count, 1)
            self.engine.handle('disconnect', {'browser': 'Chrome'})
            self.assertNotIn('Chrome', self.engine.pending_connections)

    def test_failed_open_is_reported_with_copyable_url(self):
        result = SetupResult(False, 'chrome://inspect/#remote-debugging', '开关不可见')
        with patch('engine.browser_exe', return_value='browser.exe'), patch('engine.open_settings', return_value=result):
            self.engine.handle('open_setup', {'browser': 'Chrome'})
        events = []
        while not self.engine.output.empty():
            events.append(self.engine.output.get_nowait())
        failure = next(e for e in events if e['kind'] == 'setup_failed')
        self.assertEqual(failure['text'], '开关不可见')
        self.assertEqual(failure['url'], result.url)
        self.assertNotIn('已找到', self.engine.status['Chrome'])

    def test_connection_error_is_not_replaced_by_generic_instruction(self):
        self.endpoint('Chrome')
        with patch('engine.browser_exe', return_value='browser.exe'), patch('engine.open_settings', return_value=SetupResult(True, 'chrome://inspect/#remote-debugging')), patch('engine.BrowserConnection', side_effect=RuntimeError('browser policy rejected')):
            self.engine.handle('connect_both', {})
        events = []
        while not self.engine.output.empty():
            events.append(self.engine.output.get_nowait())
        self.assertTrue(any('browser policy rejected' in e.get('text', '') for e in events))

    def test_stopping_wait_preserves_existing_snapshots(self):
        self.engine.pending_connections = {'Chrome', 'Edge'}
        self.engine.connection_deadline = 0
        self.engine.check_connections()
        self.assertFalse(self.engine.pending_connections)
        self.assertFalse(self.engine.clients)

    def test_clicking_connected_browser_reuses_connection_without_authorization(self):
        client=Mock(closed=False)
        self.engine.clients['Edge']=client
        with patch('engine.BrowserConnection') as factory, patch.object(self.engine,'scan') as scan:
            self.engine.handle('connect',{'browser':'Edge'})
            factory.assert_not_called()
            client.close.assert_not_called()
            scan.assert_called_once()
        self.assertIs(self.engine.clients['Edge'],client)


if __name__ == '__main__':
    unittest.main()
