import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import Archive, day_key
from workspace_model import demo_workspace


class ArchiveDateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.a = Archive(self.temp.name)
        today = dt.date.today()
        self.today = today.isoformat()
        self.yesterday = (today - dt.timedelta(days=1)).isoformat()
        self.stamp = dt.datetime.combine(today, dt.time(0, 15)).timestamp()
        self.tabs = [dict(browser=b, window=str(i), target=str(i), title='示例网页', url=f'https://example.com/{i}')
                     for i, b in enumerate(('Edge', 'Chrome'))]

    def tearDown(self):
        self.a.db.close(); self.temp.cleanup()

    def browser(self, stamp):
        with patch('storage.time.time', return_value=stamp):
            self.a.capture(self.tabs, ['Chrome', 'Edge'])

    def test_browser_date_move_preserves_times_ids_notes_and_automatic_save(self):
        self.browser(self.stamp)
        entry = self.a.listing('daily')[0]
        self.a.note('daily_entry', entry['entry_id'], '凌晨补存')
        self.a.move_daily('daily', entry['entry_id'], self.yesterday)
        moved = self.a.listing('daily')[0]
        self.assertEqual((moved['day'], moved['time'], moved['entry_id'], moved['note']),
                         (self.yesterday, self.stamp, entry['entry_id'], '凌晨补存'))
        self.browser(self.stamp + 60)
        self.assertEqual(len(self.a.listing('daily')), 2)
        self.assertEqual(self.a.listing('daily', day=self.yesterday)[0]['time'], self.stamp)
        self.assertEqual(self.a.listing('snapshots')[0]['day'], self.today)
        path = Path(self.temp.name) / 'browser-backup.json'; self.a.export(path)
        other = Archive(Path(self.temp.name) / 'browser-import')
        try:
            other.import_backup(path)
            restored = other.listing('daily', day=self.yesterday)[0]
            self.assertEqual((restored['time'], restored['note']), (self.stamp, '凌晨补存'))
        finally:
            other.db.close()

    def test_move_browser_primary_to_existing_day_keeps_both_dates_other_entries(self):
        self.browser(self.stamp - 86400)
        target = self.a.listing('daily')[0]['entry_id']
        self.browser(self.stamp)
        snap = self.a.listing('snapshots')[0]
        self.a.add_daily(snap['id'], self.today, mode='append')
        # Add a different manual candidate to the source folder.
        self.tabs[0]['title'] = '另一个网页'
        self.browser(self.stamp + 30)
        new_snap = self.a.listing('snapshots')[0]
        candidate = self.a.add_daily(new_snap['id'], self.today, mode='append')
        source = self.a.listing('daily', day=self.today)[0]
        self.a.move_daily('daily', source['entry_id'], self.yesterday)
        moved = self.a.listing('daily', day=self.yesterday)[0]
        self.assertEqual(moved['entry_id'], source['entry_id'])
        self.assertIn(target, [r['entry_id'] for r in moved['children']])
        self.assertEqual(self.a.listing('daily', day=self.today)[0]['entry_id'], candidate)
        self.browser(self.stamp - 86400 + 120)
        self.assertEqual(self.a.daily_entry(target)['time'], self.stamp - 86400)
        self.assertEqual(self.a.daily_entry(target)['origin'], 'manual')

    def test_desktop_date_can_differ_from_capture_day_and_roundtrip(self):
        self.a.capture_workspace(demo_workspace(), timestamp=self.stamp)
        row = self.a.listing('workspace_daily')[0]
        self.a.move_daily('workspace_daily', row['id'], self.yesterday)
        moved = self.a.listing('workspace_daily')[0]
        self.assertEqual((moved['day'], moved['time'], moved['payload']), (self.yesterday, self.stamp, row['payload']))
        self.assertEqual(moved['origin'], 'manual')
        self.a.capture_workspace(demo_workspace(), timestamp=self.stamp + 60)
        self.assertEqual(len(self.a.listing('workspace_daily')), 2)
        path = Path(self.temp.name) / 'backup.json'; self.a.export(path)
        other = Archive(Path(self.temp.name) / 'imported')
        try:
            other.import_backup(path)
            self.assertEqual(other.listing('workspace_daily', day=self.yesterday)[0]['time'], self.stamp)
        finally:
            other.db.close()

    def test_invalid_move_does_not_change_existing_records(self):
        self.browser(self.stamp)
        before = self.a.listing('daily')
        for day in ('2026-02-30', '20261014', '2026-1-1'):
            with self.assertRaises(ValueError):
                self.a.move_daily('daily', before[0]['entry_id'], day)
        with self.assertRaises(ValueError):
            self.a.move_daily('daily', 999999, self.yesterday)
        self.assertEqual(self.a.listing('daily'), before)

    def test_date_dialog_validates_and_can_cancel(self):
        from archive_date import ask_archive_date
        with patch('archive_date.simpledialog.askstring', side_effect=['2026-02-30', self.yesterday]), patch('archive_date.messagebox.showerror') as error:
            self.assertEqual(ask_archive_date(None, self.today), self.yesterday)
            error.assert_called_once()
        with patch('archive_date.simpledialog.askstring', return_value=None):
            self.assertIsNone(ask_archive_date(None, self.today, desktop=True))

    def test_desktop_daily_orders_by_assigned_day_after_date_move(self):
        self.a.capture_workspace(demo_workspace(), timestamp=self.stamp - 86400)
        old = self.a.listing('workspace_daily')[0]
        self.a.capture_workspace(demo_workspace(), timestamp=self.stamp)
        future = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        self.a.move_daily('workspace_daily', old['id'], future)
        self.assertEqual(self.a.listing('workspace_daily', limit=1)[0]['day'], future)
