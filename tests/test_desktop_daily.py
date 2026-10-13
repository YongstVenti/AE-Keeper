import copy
import datetime as dt
from pathlib import Path
import tempfile
import unittest
import queue
from storage import Archive, day_key
from engine import Engine
from workspace_model import demo_workspace


class DesktopDailyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = Archive(self.temp.name)
        self.base = dt.datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()

    def tearDown(self):
        self.archive.db.close(); self.temp.cleanup()

    def save(self, title, stamp, manual=False):
        data = demo_workspace(); data['windows'][0]['title'] = title
        self.archive.capture_workspace(data, timestamp=stamp, manual=manual)

    def test_daily_selects_one_latest_per_date_without_removing_saved_history(self):
        self.save('昨天的桌面', self.base - 86400)
        self.save('较早手动快照', self.base - 20, manual=True)
        self.save('当天自动桌面', self.base)
        self.save('当天最晚桌面', self.base + 20, manual=True)
        rows = self.archive.listing('workspace_daily', thumbnails_only=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['payload']['windows'][0]['title'], '当天最晚桌面')
        self.assertEqual(rows[1]['payload']['windows'][0]['title'], '昨天的桌面')
        self.assertEqual(len(self.archive.listing('workspaces')), 4)
        self.assertEqual(self.archive.stats()['workspaces']['days'], 2)
        self.assertEqual(self.archive.listing('workspace_daily', query='较早手动快照'), [])
        self.assertEqual(len(self.archive.listing('workspace_daily', day=day_key(self.base))), 1)

    def test_auto_updates_latest_and_rejects_older_capture(self):
        self.save('第一份', self.base)
        self.save('第二份', self.base + 40)
        self.save('过时的一份', self.base - 20)
        row = self.archive.listing('workspace_daily')[0]
        self.assertEqual(row['payload']['windows'][0]['title'], '第二份')
        self.assertEqual(len(self.archive.listing('workspaces')), 1)
        self.assertEqual(len(self.archive.listing('workspace_daily', limit=1)), 1)

    def test_equal_timestamp_uses_latest_saved_id(self):
        self.save('同一秒第一份', self.base, manual=True)
        self.save('同一秒第二份', self.base, manual=True)
        self.assertEqual(self.archive.listing('workspace_daily')[0]['payload']['windows'][0]['title'], '同一秒第二份')

    def test_daily_save_updates_auto_row_instead_of_creating_manual_copies(self):
        engine = Engine(Path(self.temp.name) / 'engine', queue.Queue(), demo=True)
        try:
            engine.handle('workspace_save', {'daily': True})
            engine.handle('workspace_save', {'daily': True})
            rows = engine.archive.listing('workspaces')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['origin'], 'auto')
            engine.handle('workspace_save', {'daily': False})
            self.assertEqual(len(engine.archive.listing('workspaces')), 2)
            self.assertEqual(len(engine.archive.listing('workspace_daily')), 1)
        finally:
            engine.archive.db.close()
