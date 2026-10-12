import json
import tempfile
import time
import unittest
from pathlib import Path
from storage import Archive, safe_url


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.a = Archive(self.temp.name)
        self.rows = [{'browser': 'Chrome', 'window': '1', 'target': 'a', 'title': '家庭照片', 'url': 'https://example.com/photos', 'source': '实时连接'},
                     {'browser': 'Edge', 'window': '2', 'target': 'b', 'title': '旧居资料', 'url': 'https://example.com/home', 'source': '实时连接'}]

    def tearDown(self):
        self.a.db.close()
        self.temp.cleanup()

    def test_two_browsers_snapshot_dedup_and_missing_connection(self):
        self.a.capture(self.rows, ['Chrome', 'Edge'])
        self.a.capture(list(reversed(self.rows)), ['Edge', 'Chrome'])
        self.assertEqual(self.a.stats()['snapshots']['count'], 1)
        old = self.a.meta('last_saved')
        self.assertFalse(self.a.capture([], []))
        self.assertEqual(self.a.meta('last_saved'), old)
        self.a.capture(self.rows[:1], ['Chrome'])
        self.assertEqual(self.a.stats()['snapshots']['count'], 2)

    def test_record_persists_after_tab_closed(self):
        self.a.record(self.rows, {('Chrome', 'a'), ('Edge', 'b')})
        self.a.capture([], [])
        self.assertEqual(len(self.a.listing('visits', '照片')), 1)
        self.a.record(self.rows, set())
        self.assertEqual(self.a.listing('visits', '照片')[0]['count'], 1)

    def test_bookmark_merge_and_atomic_rejection(self):
        self.a.merge_bookmarks([{'url': 'https://example.com/', 'title': '首页', 'paths': ['Chrome / 学习'], 'sources': ['Chrome']}])
        self.a.merge_bookmarks([{'url': 'https://example.com/', 'title': '首页', 'paths': ['Edge / 工作'], 'sources': ['Edge']}])
        self.assertEqual(self.a.stats()['bookmarks']['count'], 1)
        self.assertEqual(len(self.a.listing('bookmarks')[0]['paths']), 2)
        self.a.save_settings({'limit_mb': 10})
        with self.assertRaises(ValueError):
            self.a.merge_bookmarks([{'url': 'https://example.com/overflow', 'title': 'overflow', 'paths': ['x' * 2000000], 'sources': ['Edge']}])
        self.assertEqual(self.a.stats()['bookmarks']['count'], 1)

    def test_auto_fallback_preserves_snapshots(self):
        self.a.save_settings({'limit_mb': 10})
        self.a.db.execute('INSERT INTO visits VALUES(?,?,?,?,?,?,?,?,?)', ('2026-10-10', 'Chrome', 'https://fill.test/', '容量', time.time(), time.time(), 1, '', 3670010))
        self.a.db.commit()
        self.a.record(self.rows, {('Chrome', 'a')})
        self.assertTrue(self.a.meta('fallback'))
        self.a.capture(self.rows, ['Chrome', 'Edge'])
        self.assertEqual(self.a.stats()['snapshots']['count'], 1)

    def test_backup_roundtrip_and_safe_urls(self):
        self.a.record(self.rows, {('Chrome', 'a')})
        self.a.capture(self.rows, ['Chrome', 'Edge'])
        file = Path(self.temp.name) / 'backup.json'
        self.a.export(file)
        self.a.import_backup(file)
        self.assertEqual(self.a.stats()['snapshots']['count'], 1)
        self.assertEqual(self.a.stats()['visits']['count'], 2)
        self.assertFalse(safe_url('javascript:alert(1)'))
        self.a.db.close()
        self.a = Archive(self.temp.name)
        self.assertEqual(self.a.stats()['snapshots']['count'], 1)


if __name__ == '__main__':
    unittest.main()
