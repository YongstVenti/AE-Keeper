import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Engine


class RestoreTests(unittest.TestCase):
    def test_context_restore_only_selected_browser_window_in_daily_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(folder, queue.Queue())
            try:
                tabs = [dict(browser=b, window=w, target=str(i), url=url, title='示例') for i, (b,w,url) in enumerate([
                    ('Edge','same','https://example.com/edge-1'),
                    ('Edge','same','https://example.com/edge-2'),
                    ('Edge','other','https://example.com/edge-other'),
                    ('Chrome','same','https://example.com/chrome'),
                    ('Edge','same','edge://settings/'),
                    ('Edge','same','file:///C:/Demo/index.html')])]
                engine.archive.capture(tabs,['Chrome','Edge'])
                row = engine.archive.listing('daily')[0]
                with patch('engine.browser_exe',side_effect=lambda browser: browser+'.exe'), patch('engine.subprocess.Popen') as launch:
                    engine.handle('restore',dict(view='daily',id=row['id'],entry_id=row['entry_id'],browser='Edge',window='same'))
                    self.assertEqual(launch.call_count,1)
                    self.assertEqual(launch.call_args.args[0], ['Edge.exe','--new-window','https://example.com/edge-1','https://example.com/edge-2','file:///C:/Demo/index.html'])
                self.assertEqual(len(engine.archive.listing('daily')),1)
            finally:
                engine.archive.db.close()

    def test_invalid_window_scope_never_falls_back_to_restoring_every_window(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(folder,queue.Queue())
            try:
                engine.archive.capture([dict(browser='Edge',window='one',target='1',url='https://example.com/',title='示例')],['Edge'])
                row = engine.archive.listing('snapshots')[0]
                with patch('engine.subprocess.Popen') as launch:
                    with self.assertRaises(ValueError):
                        engine.handle('restore',dict(id=row['id'],browser='Edge'))
                    engine.handle('restore',dict(id=row['id'],browser='Edge',window='missing'))
                    launch.assert_not_called()
            finally:
                engine.archive.db.close()

    def test_snapshot_reopens_urls_in_original_browsers_and_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(folder, queue.Queue())
            try:
                tabs = [
                    {'browser':'Chrome','window':'one','target':'1','url':'https://example.com/one?x=1&y=2','title':'一'},
                    {'browser':'Chrome','window':'one','target':'2','url':'https://example.com/two','title':'二'},
                    {'browser':'Chrome','window':'two','target':'3','url':'https://example.com/other','title':'三'},
                    {'browser':'Edge','window':'three','target':'4','url':'https://example.com/edge','title':'四'},
                    {'browser':'Edge','window':'three','target':'5','url':'edge://settings/','title':'设置'},
                ]
                engine.archive.capture(tabs, ['Chrome','Edge'])
                rid = engine.archive.listing('snapshots')[0]['id']
                existing = Mock()
                engine.clients['Chrome'] = existing
                with patch('engine.browser_exe', side_effect=lambda browser: browser+'.exe'), patch('engine.subprocess.Popen') as launch:
                    engine.handle('restore', {'id':rid})
                    commands = [call.args[0] for call in launch.call_args_list]
                self.assertEqual(len(commands),3)
                self.assertEqual(set(tuple(c) for c in commands), {
                    ('Chrome.exe','--new-window','https://example.com/one?x=1&y=2','https://example.com/two'),
                    ('Chrome.exe','--new-window','https://example.com/other'),
                    ('Edge.exe','--new-window','https://example.com/edge'),
                })
                existing.close.assert_not_called()
                self.assertTrue(engine.archive.listing('snapshots'))
            finally:
                engine.archive.db.close()


if __name__ == '__main__':
    unittest.main()
