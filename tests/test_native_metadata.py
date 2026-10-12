import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_metadata import merge_windows, signature
from cdp import BrowserConnection


def row(w, i, url='', active=False):
    return dict(target=f'{w}-{i}', browser='Edge', window=w, index=i,
                url=url, title='unavailable', active=active, source='实时连接')


class MetadataTests(unittest.TestCase):
    def test_sleeping_urls_and_duplicate_tabs_keep_exact_window_and_order(self):
        rows = [row('A',0,'https://a/',True),row('A',1),row('A',2),
                row('B',0,'https://b/',True),row('B',1)]
        windows = [{'tabs':[dict(url='https://b/',active=True),dict(url='https://sleep-b/')]},
                   {'tabs':[dict(url='https://a/',active=True),dict(url='https://same/',title='Sleep'),dict(url='https://same/')]}]
        out=merge_windows(rows,windows)
        self.assertEqual([r['url'] for r in out],['https://a/','https://same/','https://same/','https://b/','https://sleep-b/'])
        self.assertEqual([r['target'] for r in out],[r['target'] for r in rows])
        self.assertEqual(out[1]['title'],'Sleep')
        self.assertFalse(rows[1]['url'])

    def test_identical_window_sizes_require_unique_anchor_matches(self):
        rows=[row('A',0,'https://a/',True),row('A',1),row('B',0,'https://b/',True),row('B',1)]
        windows=[{'tabs':[dict(url='https://b/',active=True),dict(url='https://sleep-b/')]},
                 {'tabs':[dict(url='https://a/',active=True),dict(url='https://sleep-a/')]}]
        self.assertEqual(merge_windows(rows,windows)[1]['url'],'https://sleep-a/')

    def test_ambiguous_unanchored_and_changed_windows_stay_unresolved(self):
        rows=[row('A',0,'https://same/',True),row('A',1)]
        w={'tabs':[dict(url='https://same/',active=True),dict(url='https://private/') ]}
        self.assertFalse(merge_windows(rows,[w,w])[1]['url'])
        self.assertFalse(merge_windows([row('A',0),row('A',1)],[w])[1]['url'])
        self.assertFalse(merge_windows(rows,[{'tabs':[dict(url='https://changed/',active=True),dict(url='https://x/')] }])[1]['url'])

    def test_missing_indexes_and_active_tab_changes_are_not_guessed(self):
        rows=[row('A',0,'https://a/',True),row('A',2)]
        windows=[{'tabs':[dict(url='https://a/',active=True),dict(url='https://x/')]}]
        self.assertFalse(merge_windows(rows,windows)[1]['url'])
        rows[1]['index']=1
        windows[0]['tabs'][0]['active']=False
        self.assertFalse(merge_windows(rows,windows)[1]['url'])

    def test_inventory_signature_detects_moves_navigation_and_activation(self):
        rows=[row('A',0,'https://a/',True),row('A',1)]
        for field,value in [('window','B'),('index',3),('url','https://new/'),('active',True),('target','replacement')]:
            other=[dict(r) for r in rows];other[1][field]=value
            self.assertNotEqual(signature(rows),signature(other))

    def test_unavailable_ui_does_not_close_browser_connection(self):
        c=object.__new__(BrowserConnection)
        c.browser='Edge';c.inventory={};c.closed=False
        c._read_inventory=lambda:[row('A',0)]
        class Unavailable:
            def windows(self):raise RuntimeError('not available')
            def close(self):pass
        c.native_metadata=Unavailable()
        self.assertFalse(c.tabs()[0]['url'])
        self.assertFalse(c.closed)
        self.assertIn('metadata_error',c.inventory)


if __name__=='__main__':unittest.main()
