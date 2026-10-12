import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
import unittest
import test_daily
from test_daily import ArchiveForTest


class FolderTests(unittest.TestCase):
    setUp=test_daily.DailyArchiveTests.setUp
    tearDown=test_daily.DailyArchiveTests.tearDown
    capture=test_daily.DailyArchiveTests.capture
    def snap(self, stamp, label, complete=True):
        self.capture(stamp, [dict(r, title=label) for r in self.rows], complete=complete)
        return self.a.listing('snapshots')[0]['id']

    def test_one_auto_entry_rolls_forward_without_extra_children(self):
        self.snap(self.t,'早上');self.snap(self.t+10,'晚上')
        root=self.a.listing('daily')[0]
        self.assertFalse(root['children'])
        self.assertEqual(self.a.db.execute('SELECT COUNT(*) FROM daily_entries').fetchone()[0],1)

    def test_adding_same_snapshot_again_does_not_create_duplicate_child(self):
        sid=self.snap(self.t,'唯一')
        self.a.add_daily(sid,'2026-10-11','replace')
        self.a.add_daily(sid,'2026-10-11','append')
        self.assertFalse(self.a.listing('daily')[0]['children'])
        path=Path(self.temp.name)/'same.json';self.a.export(path)
        with ArchiveForTest(Path(self.temp.name)/'same') as other:
            other.import_backup(path)
            self.assertFalse(other.listing('daily')[0]['children'])

    def test_restore_child_uses_its_urls_instead_of_the_primary_urls(self):
        import queue
        from engine import Engine
        self.snap(self.t,'父级')
        self.capture(self.t+1,[dict(r,url=r['url']+'/child') for r in self.rows],complete=False)
        eid=self.a.add_daily(self.a.listing('snapshots')[0]['id'],'2026-10-11')
        engine=Engine(Path(self.temp.name)/'a',queue.Queue())
        try:
            with patch('engine.browser_exe',side_effect=lambda b:b+'.exe'),patch('engine.subprocess.Popen') as launch:
                engine.handle('restore',{'id':eid,'entry_id':eid,'view':'daily'})
            self.assertEqual(len(launch.call_args_list),2)
            self.assertTrue(all(c.args[0][-1].endswith('/child') for c in launch.call_args_list))
        finally:engine.archive.db.close()

    def test_append_replace_and_promote_keep_previous_primary(self):
        first=self.snap(self.t,'最初')
        second=self.snap(self.t+10,'另一份',complete=False)
        eid=self.a.add_daily(second,'2026-10-11','append')
        root=self.a.listing('daily')[0]
        self.assertEqual(root['tabs'][0]['title'],'最初')
        self.assertEqual(root['children'][0]['entry_id'],eid)
        self.a.primary_daily(eid)
        root=self.a.listing('daily')[0]
        self.assertEqual(root['entry_id'],eid)
        self.assertEqual(root['children'][0]['tabs'][0]['title'],'最初')
        self.a.add_daily(first,'2026-10-11','replace')
        self.assertEqual(self.a.listing('daily')[0]['tabs'][0]['title'],'最初')

    def test_manual_primary_stays_fixed_auto_child_updates_once(self):
        early=self.snap(self.t,'选择保留')
        self.a.add_daily(early,'2026-10-11','replace')
        self.snap(self.t+10,'自动一');self.snap(self.t+20,'自动二')
        root=self.a.listing('daily')[0]
        self.assertEqual(root['tabs'][0]['title'],'选择保留')
        auto=[r for r in root['children'] if r['origin']=='auto']
        self.assertEqual(len(auto),1)
        self.assertEqual(auto[0]['tabs'][0]['title'],'自动二')
        self.a.primary_daily(auto[0]['entry_id'])
        self.snap(self.t+30,'自动三')
        root=self.a.listing('daily')[0]
        self.assertEqual(root['tabs'][0]['title'],'自动二')
        self.assertEqual(sum(r['origin']=='auto' for r in root['children']),1)

    def test_partial_october_tenth_can_be_added_without_inventing_urls(self):
        rows=[dict(r) for r in self.rows];rows[0]['url']=''
        self.capture(self.t-86400,rows,complete=False)
        self.assertFalse(self.a.listing('daily'))
        eid=self.a.add_daily(self.a.listing('snapshots')[0]['id'],'2026-10-10')
        root=self.a.listing('daily')[0]
        self.assertEqual(root['entry_id'],eid)
        self.assertFalse(root['children'])
        self.assertEqual(sum(not t['url'] for t in root['tabs']),1)
        self.assertFalse(root['complete'])

    def test_candidate_note_search_and_deletion(self):
        self.snap(self.t,'主存档')
        other=self.snap(self.t+1,'子快照',complete=False)
        eid=self.a.add_daily(other,'2026-10-11')
        self.a.note('daily_entry',eid,'候选备注')
        self.assertEqual(len(self.a.listing('daily',query='候选备注')),1)
        self.a.delete('daily_entry',eid)
        self.assertFalse(self.a.listing('daily')[0]['children'])
        self.a.delete('daily',self.a.listing('daily')[0]['id'])
        self.assertEqual(self.a.db.execute('SELECT COUNT(*) FROM daily_entries').fetchone()[0],0)

    def test_backup_preserves_folders_primary_choice_and_unread_urls(self):
        self.snap(self.t,'自动')
        rows=[dict(r,url='' if r['browser']=='Edge' else r['url'],title='未读记录') for r in self.rows]
        self.capture(self.t+1,rows,complete=False)
        eid=self.a.add_daily(self.a.listing('snapshots')[0]['id'],'2026-10-10','replace')
        path=Path(self.temp.name)/'folders.json';self.a.export(path)
        with ArchiveForTest(Path(self.temp.name)/'other') as other:
            other.capture(self.rows,['Edge','Chrome']) # Different entry IDs.
            other.import_backup(path);other.import_backup(path)
            folder=next(r for r in other.listing('daily') if r['day']=='2026-10-10')
            self.assertTrue(folder['primary_locked'])
            self.assertEqual(folder['tabs'][0]['title'],'未读记录')
            self.assertEqual(sum(not t['url'] for t in folder['tabs']),1)
            self.assertFalse(folder['children'])
            quick=next(r for r in other.listing('snapshots') if any(t['title']=='未读记录' for t in r['tabs']))
            self.assertEqual(len(quick['tabs']),2)
            self.assertEqual(sum(not t['url'] for t in quick['tabs']),1)

    def test_older_backup_cannot_overwrite_newer_manual_primary(self):
        first=self.snap(self.t,'早');self.a.add_daily(first,'2026-10-11','replace')
        path=Path(self.temp.name)/'old.json';self.a.export(path)
        second=self.snap(self.t+1,'晚',complete=False)
        with patch('storage.time.time',return_value=self.t+50000):
            self.a.add_daily(second,'2026-10-11','replace')
        # Use a guaranteed newer action timestamp than the exported manual choice.
        self.a.db.execute('UPDATE daily_archives SET modified_at=modified_at+10000000');self.a.db.commit()
        self.a.import_backup(path)
        self.assertEqual(self.a.listing('daily')[0]['tabs'][0]['title'],'晚')

    def test_capacity_rejection_rolls_back_folder_without_touching_snapshots(self):
        sid=self.snap(self.t,'完整')
        before=self.a.db.execute('SELECT COUNT(*) FROM daily_entries').fetchone()[0]
        self.a.db.execute('UPDATE snapshots SET bytes=600000000');self.a.db.commit()
        with self.assertRaises(ValueError):self.a.add_daily(sid,'2026-10-10')
        self.assertEqual(self.a.db.execute('SELECT COUNT(*) FROM daily_entries').fetchone()[0],before)
        self.assertEqual(len(self.a.listing('snapshots')),1)

    def test_previous_single_daily_schema_migrates_exact_payload(self):
        folder=Path(self.temp.name)/'legacy';folder.mkdir()
        db=sqlite3.connect(folder/'archive.sqlite3')
        db.execute("CREATE TABLE daily_archives(day TEXT PRIMARY KEY,time REAL,last_seen REAL,fingerprint TEXT,payload TEXT,bytes INTEGER,note TEXT DEFAULT '')")
        payload=json.dumps({'tabs':self.rows,'connected':['Edge','Chrome'],'complete':True})
        db.execute('INSERT INTO daily_archives VALUES(?,?,?,?,?,?,?)',('2026-10-10',self.t,self.t,'sig',payload,len(payload),'旧备注'));db.commit();db.close()
        with ArchiveForTest(folder) as legacy:
            root=legacy.listing('daily')[0]
            self.assertEqual(root['tabs'],self.rows)
            self.assertEqual(root['note'],'旧备注')
            self.assertFalse(root['children'])

    def test_invalid_dates_and_modes_do_not_change_anything(self):
        sid=self.snap(self.t,'完整')
        for date,mode in [('2026-02-30','append'),('2026-1-1','append'),('2026-10-10','invalid')]:
            with self.assertRaises(ValueError):self.a.add_daily(sid,date,mode)
        self.assertEqual(len(self.a.listing('daily')),1)
