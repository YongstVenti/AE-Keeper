from pathlib import Path
import sys
import tempfile
import queue
from unittest.mock import Mock
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tab_recovery import TabRecovery
from storage import DEFAULTS
from engine import Engine
from cdp import BrowserConnection, BrowserReadTimeout


def rows(count=8):
    return [dict(browser='Edge',window='one',target=str(i),index=i,url='',title='未读取') for i in range(count)]


class Client:
    closed = False
    def __init__(self): self.call = Mock(); self.url = ''; self.reads = []
    def recovery_info(self,target):
        self.reads.append(target)
        return dict(window='one',url=self.url,title='已读')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.r = TabRecovery(); self.c = Client(); self.saved = []
        self.r.start('Edge',rows(),dict(DEFAULTS))

    def step(self,now,resources=(6000,5)):
        self.r.step(self.c,self.saved.append,resources,now=now)

    def test_exactly_one_activation_then_wait_without_touching_next_tab(self):
        self.step(0);self.step(2);self.step(4)
        self.c.call.assert_called_once_with('Target.activateTarget',{'targetId':'0'},timeout=2)
        self.assertEqual(self.c.reads,['0','0','0'])
        self.c.url='https://example.com/'
        self.step(6)
        self.assertEqual(self.saved[0]['target'],'0')
        self.step(7)
        self.assertEqual(self.r.attempts,1)
        self.assertTrue(self.r.running)

    def test_batch_limit_does_not_silently_wake_remaining_pages(self):
        self.c.url='https://example.com/'
        for stamp in range(0,40,8):self.step(stamp)
        self.assertEqual(len(self.saved),5)
        self.assertFalse(self.r.running)
        self.assertEqual(len(self.r.queue),3)
        self.c.call.assert_not_called()

    def test_low_memory_or_high_cpu_pauses_before_activation(self):
        for resources in ((2000,0),(6000,90)):
            self.r.pause()
            self.r.start('Edge',rows(),dict(DEFAULTS));self.step(0,resources)
            self.assertFalse(self.r.running)
        self.c.call.assert_not_called()

    def test_two_unresolved_pages_pause_without_opening_a_third(self):
        for stamp in (0,20,28,48):self.step(stamp)
        self.assertFalse(self.r.running)
        self.assertEqual(self.r.failed,2)
        self.assertEqual(self.c.call.call_count,2)
        self.r.start('Edge', rows(), dict(DEFAULTS))
        self.assertEqual(self.r.queue[0]['target'], '2')

    def test_connection_timeout_pauses_without_reconnect_or_retry(self):
        self.c.recovery_info=Mock(side_effect=BrowserReadTimeout('timeout'))
        self.step(0);self.step(50)
        self.assertFalse(self.r.running)
        self.c.recovery_info.assert_called_once()
        self.c.call.assert_not_called()

    def test_manual_pause_stops_all_further_commands(self):
        self.step(0);self.r.pause();self.step(5)
        self.assertEqual(self.c.call.call_count,1)
        self.assertEqual(self.r.done,0)

    def test_pause_during_metadata_read_prevents_subsequent_activation(self):
        def slow_read(target):
            self.r.request_pause()
            return dict(window='one',url='')
        self.c.recovery_info=slow_read
        self.step(0)
        self.assertFalse(self.r.running)
        self.c.call.assert_not_called()

    def test_moved_window_is_not_activated_or_saved_with_stale_membership(self):
        self.c.recovery_info=Mock(return_value=dict(window='other',url='https://example.com/'))
        self.step(0)
        self.assertEqual(self.saved,[]);self.c.call.assert_not_called()

    def test_known_urls_and_other_browser_are_never_queued(self):
        data=rows(2)+[dict(browser='Chrome',window='two',target='c',url='')]
        data[0]['url']='https://example.com/'
        self.r.pause()
        self.r.start('Edge',data,dict(DEFAULTS))
        self.assertEqual([r['target'] for r in self.r.queue],['1'])

    def test_private_target_is_rejected_before_window_lookup(self):
        c=object.__new__(BrowserConnection);c.normal_contexts={'normal'};c.include_all_profiles=False
        c.call=Mock(return_value=dict(targetInfo=dict(targetId='x',type='tab',browserContextId='private',url='')))
        with self.assertRaises(RuntimeError):c.recovery_info('x')
        c.call.assert_called_once()

    def test_busy_scan_keeps_connection_and_never_promotes_stale_data_to_daily(self):
        with tempfile.TemporaryDirectory() as folder:
            e=Engine(folder,queue.Queue())
            client=Mock();client.tabs.side_effect=BrowserReadTimeout('busy')
            e.clients={'Edge':client};e.current=[dict(rows(1)[0],url='https://example.com/')]
            try:
                e.save()
                client.close.assert_not_called()
                self.assertIs(e.clients['Edge'],client)
                self.assertFalse(e.scan_complete)
                self.assertFalse(e.archive.listing('snapshots')[0]['complete'])
                self.assertEqual(e.archive.listing('daily'),[])
            finally:e.archive.db.close()
