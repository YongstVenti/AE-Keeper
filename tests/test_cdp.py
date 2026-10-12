import queue
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cdp import BrowserConnection, TAB_FILTER, PAGE_FILTER


def target(identifier, kind='tab', context='normal', url=None, **extra):
    return {'targetId': identifier, 'type': kind, 'browserContextId': context,
            'url': url or 'https://example.com/' + identifier, 'title': identifier, **extra}


class InventoryConnection(BrowserConnection):
    def __init__(self, tabs, pages=(), windows=None, include_all=False):
        self.browser = 'Edge'
        self.include_all_profiles = include_all
        self.normal_contexts = {'normal'}
        self.private_contexts = set()
        self.window_cache = {}
        self.page_parents = {}
        self.inventory = {}
        self.events = queue.Queue()
        self.tab_targets, self.page_targets = list(tabs), list(pages)
        self.windows = windows or {}
        self.queries = []

    def call(self, method, params=None, **kwargs):
        if method == 'Target.getTargets':
            self.queries.append(params)
            return {'targetInfos': self.tab_targets if params['filter'] == TAB_FILTER else self.page_targets}
        if method == 'Target.getBrowserContexts':
            return {'defaultBrowserContextId': 'normal', 'browserContextIds': ['private']}
        raise AssertionError(method)

    def begin(self, method, params):
        assert method == 'Browser.getWindowForTarget'
        return params['targetId']

    def finish(self, identifier, **kwargs):
        value = self.windows.get(identifier, 10)
        if isinstance(value, Exception):
            raise value
        return {'windowId': value}

    def event(self, info, method='Target.targetInfoChanged'):
        self.events.put({'method': method, 'params': {'targetInfo': info}})


class CdpInventoryTests(unittest.TestCase):
    def test_unloaded_container_is_present_without_page_and_index_zero_survives(self):
        connection = InventoryConnection([
            target('sleeping', embedderData={'tabStripIndex': 1, 'tabActive': False}),
            target('first', embedderData={'tabStripIndex': 0, 'tabPinned': True}),
        ], pages=[target('live-renderer', kind='page')])
        rows = connection.tabs()
        self.assertEqual([r['target'] for r in rows], ['first', 'sleeping'])
        self.assertEqual(rows[0]['index'], 0)
        self.assertTrue(rows[0]['pinned'])
        self.assertEqual(rows[1]['url'], 'https://example.com/sleeping')
        self.assertEqual(connection.inventory['target_type'], 'tab')
        self.assertEqual(connection.queries, [{'filter': TAB_FILTER}])

    def test_private_profiles_and_background_extensions_do_not_inflate_count(self):
        connection = InventoryConnection([
            target('one'), target('two'), target('private', context='private'),
            target('other-profile', context='other'), target('extension'),
        ], windows={'one': 10, 'two': 11, 'extension': RuntimeError('Browser window not found')})
        rows = connection.tabs()
        self.assertEqual({r['target'] for r in rows}, {'one', 'two'})
        self.assertEqual(connection.inventory, {'target_type': 'tab', 'candidates': 5, 'allowed': 3,
                         'excluded': 2, 'without_window': 1, 'tabs': 2, 'windows': 2, 'with_tab_index': 0, 'unmapped_tabs': 0})

    def test_identical_urls_in_two_tabs_remain_two_tabs(self):
        connection = InventoryConnection([target('one', url='https://example.com/shared'),
                                          target('two', url='https://example.com/shared')])
        self.assertEqual(len(connection.tabs()), 2)

    def test_legacy_page_fallback_does_not_mix_tab_and_page_inventory(self):
        connection = InventoryConnection([], pages=[target('one', kind='page'), target('hidden', kind='page', subtype='prerender')])
        self.assertEqual([r['target'] for r in connection.tabs()], ['one'])
        self.assertEqual(connection.inventory['target_type'], 'page')
        self.assertEqual(connection.queries[-1], {'filter': PAGE_FILTER})

    def test_all_scope_is_explicit_opt_in(self):
        connection = InventoryConnection([target('private', context='private')], include_all=True)
        self.assertEqual(len(connection.tabs()), 1)

    def test_page_events_keep_transient_urls_and_use_known_tab_parent(self):
        connection = InventoryConnection([])
        connection.window_cache['container'] = 10
        connection.event(target('container', url='https://example.com/one'))
        connection.event(target('renderer', kind='page', parentId='container', url='https://example.com/one'))
        connection.event(target('renderer', kind='page', parentId='container', url='https://example.com/two'))
        connection.event(target('private', kind='page', context='private'))
        rows = connection.drain_events()
        self.assertEqual([r['url'] for r in rows], ['https://example.com/one', 'https://example.com/two'])
        self.assertEqual({r['target'] for r in rows}, {'container'})
        self.assertEqual({r['window'] for r in rows}, {'10'})

    def test_old_page_events_without_parent_are_still_recorded(self):
        connection = InventoryConnection([])
        connection.event(target('short-lived', kind='page'))
        self.assertEqual(connection.drain_events()[0]['target'], 'short-lived')


if __name__ == '__main__':
    unittest.main()
