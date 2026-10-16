from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App


class Value:
    def __init__(self, value): self.value = value
    def set(self, value): self.value = value
    def get(self): return self.value


class ArchiveMoveFilterTests(unittest.TestCase):
    def test_browser_move_shows_all_dates_and_selects_destination(self):
        app = SimpleNamespace(view='daily', search_var=Value('旧搜索'), day_var=Value('2026-10-15'), refresh=Mock())
        App.archive_moved(app, dict(view='daily', day='2026-10-14'))
        self.assertEqual(app.day_var.get(), '')
        self.assertEqual(app.search_var.get(), '')
        self.assertEqual(app.pending_daily_selection, 'day:2026-10-14')
        app.refresh.assert_called_once()

    def test_desktop_move_shows_all_dates_and_selects_destination(self):
        pane = SimpleNamespace(query=Value('旧搜索'), day=Value('2026-10-15'))
        app = SimpleNamespace(view='workspace_daily', daily_workspace_pane=pane, refresh=Mock())
        App.archive_moved(app, dict(view='workspace_daily', day='2026-10-14'))
        self.assertEqual(pane.day.get(), '')
        self.assertEqual(pane.query.get(), '')
        self.assertEqual(pane.pending_day, '2026-10-14')
        app.refresh.assert_called_once()
