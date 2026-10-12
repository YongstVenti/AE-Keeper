import datetime as dt
import json
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from storage import Archive
from engine import Engine


class DailyArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.a=Archive(Path(self.temp.name)/'a')
        self.rows=[dict(browser=b,window=b+'-one',target=b+'-tab',title='日常网页',url='https://example.com/'+b,source='实时连接') for b in ['Edge','Chrome']]
        self.t=dt.datetime(2026,10,11,10,0).timestamp()

    def tearDown(self):
        self.a.db.close();self.temp.cleanup()

    def capture(self,stamp,rows=None,connected=None,**kwargs):
        with patch('storage.time.time',return_value=stamp):
            self.a.capture(self.rows if rows is None else rows,['Edge','Chrome'] if connected is None else connected,**kwargs)

    def test_latest_only_includes_all_tabs_and_separate_windows(self):
        self.capture(self.t)
        more=self.rows+[dict(self.rows[0],target='Edge-second',window='second',url='https://other.test/')]
        self.capture(self.t+3600,more)
        out=self.a.listing('daily')
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]['time'],self.t+3600)
        self.assertEqual(len(out[0]['tabs']),3)
        self.assertEqual(len({(r['browser'],r['window']) for r in out[0]['tabs']}),3)

    def test_partial_connection_unread_urls_and_legacy_inventory_cannot_replace_complete_day(self):
        self.capture(self.t)
        missing=[dict(r) for r in self.rows];missing[0]['url']=''
        self.capture(self.t+1,missing)
        self.capture(self.t+2,self.rows[:1],['Edge'])
        self.capture(self.t+3,complete=False)
        self.capture(self.t+4,self.rows[:1],['Edge','Chrome'])
        self.assertEqual(self.a.listing('daily')[0]['time'],self.t)
        self.assertEqual(len(self.a.listing('daily')[0]['tabs']),2)

    def test_midnight_and_unchanged_tabs_update_each_day_last_time(self):
        before=dt.datetime(2026,10,10,23,59,59).timestamp()
        self.capture(before)
        self.capture(before+2)
        self.capture(before+5)
        days=self.a.listing('daily')
        self.assertEqual([r['day'] for r in days],['2026-10-11','2026-10-10'])
        self.assertEqual(days[0]['time'],before+5)
        self.assertEqual(self.a.stats()['daily_archives']['count'],2)

    def test_annotations_and_daily_copy_survive_snapshot_updates_deletion_and_restart(self):
        self.capture(self.t)
        rid=self.a.listing('daily')[0]['id']
        self.a.note('daily',rid,'这一天的回忆')
        self.capture(self.t+1)
        self.assertEqual(self.a.listing('daily')[0]['note'],'这一天的回忆')
        self.a.delete('snapshots',self.a.listing('snapshots')[0]['id'])
        self.a.db.close();self.a=Archive(Path(self.temp.name)/'a')
        self.assertEqual(len(self.a.listing('daily')),1)
        self.a.delete('daily',rid)
        self.a.db.close();self.a=Archive(Path(self.temp.name)/'a')
        self.assertFalse(self.a.listing('daily'))

    def test_backup_restores_daily_notes_and_older_import_cannot_replace_later_day(self):
        self.capture(self.t)
        self.a.note('daily',self.a.listing('daily')[0]['id'],'每日备注')
        file=Path(self.temp.name)/'backup.json';self.a.export(file)
        with ArchiveForTest(Path(self.temp.name)/'b') as b:
            b.import_backup(file)
            b.import_backup(file)
            self.assertEqual(len(b.listing('daily')),1)
            self.assertEqual(b.listing('daily')[0]['note'],'每日备注')
        self.capture(self.t+500)
        self.a.import_backup(file)
        self.assertEqual(self.a.listing('daily')[0]['time'],self.t+500)

    def test_legacy_backup_without_proof_remains_readable_without_false_daily_completeness(self):
        self.capture(self.t)
        file=Path(self.temp.name)/'old.json';self.a.export(file)
        data=json.loads(file.read_text(encoding='utf-8'));data.pop('daily_archives')
        data.pop('daily_folders')
        for s in data['snapshots']:
            p=json.loads(s['payload']);p.pop('complete',None);s['payload']=json.dumps(p)
        file.write_text(json.dumps(data),encoding='utf-8')
        with ArchiveForTest(Path(self.temp.name)/'b') as b:
            b.import_backup(file)
            self.assertEqual(len(b.listing('snapshots')),1)
            self.assertFalse(b.listing('daily'))

    def test_quick_snapshot_capacity_cleanup_preserves_daily_copy_first(self):
        self.a.save_settings({'limit_mb':10})
        self.capture(self.t)
        self.a.db.execute('UPDATE snapshots SET bytes=6000000')
        self.a.db.commit()
        self.capture(self.t+2,[dict(r,title='新记录') for r in self.rows])
        self.assertEqual(len(self.a.listing('daily')),1)
        self.assertEqual(len(self.a.listing('snapshots')),1)

    def test_daily_restore_uses_daily_row_and_original_browser_groups(self):
        self.capture(self.t)
        engine=Engine(Path(self.temp.name)/'a',queue.Queue())
        try:
            rid=engine.archive.listing('daily')[0]['id']
            with patch('engine.browser_exe',side_effect=lambda b:b+'.exe'),patch('engine.subprocess.Popen') as launch:
                engine.handle('restore',{'id':rid,'view':'daily'})
            commands=[c.args[0] for c in launch.call_args_list]
            self.assertEqual(len(commands),2)
            self.assertEqual({c[0] for c in commands},{'Edge.exe','Chrome.exe'})
        finally:engine.archive.db.close()

    def test_engine_requires_complete_tab_container_inventory_in_both_browsers(self):
        from unittest.mock import Mock
        engine=Engine(Path(self.temp.name)/'engine',queue.Queue())
        try:
            for b in ['Edge','Chrome']:
                client=Mock()
                client.tabs.return_value=[r for r in self.rows if r['browser']==b]
                client.inventory={'target_type':'tab','tabs':1,'with_tab_index':1,'unmapped_tabs':0}
                engine.clients[b]=client
            for field,value in [('target_type','page'),('unmapped_tabs',1),('with_tab_index',0),('tabs',2)]:
                original=dict(engine.clients['Edge'].inventory)
                engine.clients['Edge'].inventory[field]=value
                engine.save()
                self.assertFalse(engine.archive.listing('daily'))
                engine.clients['Edge'].inventory=original
            engine.save()
            self.assertEqual(len(engine.archive.listing('daily')),1)
        finally:engine.archive.db.close()


class ArchiveForTest(Archive):
    def __enter__(self):return self
    def __exit__(self,*args):self.db.close()


if __name__=='__main__':unittest.main()
