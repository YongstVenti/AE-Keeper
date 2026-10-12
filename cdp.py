"""Local, explicitly enabled Chromium browser connection. No extension required."""
import json
import queue
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
import websocket
from native_metadata import NativeMetadata, merge_windows, signature


TAB_FILTER = [{'type': 'tab', 'exclude': False}, {'exclude': True}]
PAGE_FILTER = [{'type': 'page', 'exclude': False}, {'exclude': True}]
EVENT_FILTER = [{'type': 'tab', 'exclude': False}, {'type': 'page', 'exclude': False}, {'exclude': True}]


class BrowserConnection:
    def __init__(self, root, browser, endpoint=None, include_all_profiles=False):
        self.browser = browser
        self.include_all_profiles = include_all_profiles
        self.events = queue.Queue()
        self.pending = {}
        self.lock = threading.Lock()
        self.serial = 0
        self.closed = False
        self.window_cache = {}
        self.page_parents = {}
        self.inventory = {}
        self.native_metadata = NativeMetadata(self)
        if endpoint is None:
            port_file = Path(root) / 'DevToolsActivePort'
            try:
                lines = port_file.read_text(encoding='utf-8').strip().splitlines()
                port, route = int(lines[0]), lines[1].strip()
                if not 1 <= port <= 65535 or not route.startswith('/devtools/browser/'):
                    raise ValueError()
            except (OSError, ValueError, IndexError):
                raise RuntimeError(f'{browser}还未开放连接。请先在连接指南中打开浏览器设置，允许本机连接。') from None
            endpoint = f'ws://127.0.0.1:{port}{route}'
        parsed = urlparse(endpoint)
        if parsed.scheme != 'ws' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('只允许连接本机浏览器。')
        # Built-in browser authorization can precede the websocket upgrade.
        # Give the user time to answer that one prompt without retrying it.
        self.socket = websocket.create_connection(endpoint, timeout=120, suppress_origin=True,
                                                   http_no_proxy=['127.0.0.1', 'localhost'])
        self.socket.settimeout(1)
        self.reader = threading.Thread(target=self._receive, daemon=True)
        self.reader.start()
        try:
            version = self.call('Browser.getVersion', timeout=120)
            product = version.get('product', '')
            if browser == 'Edge' and 'Edg' not in product:
                raise RuntimeError('这个连接指向的不是Edge，请检查浏览器数据路径。')
            if browser == 'Chrome' and 'Edg' in product:
                raise RuntimeError('这个连接指向的是Edge，请检查Chrome数据路径。')
            # getBrowserContexts only lists DevTools-created contexts, not every
            # user-created private window. Use the explicitly identified normal
            # default profile instead of guessing from the current target list.
            contexts = self.call('Target.getBrowserContexts')
            default_context = contexts.get('defaultBrowserContextId')
            if not default_context and not self.include_all_profiles:
                raise RuntimeError('浏览器没有提供普通个人资料识别，请更新浏览器后重试连接。')
            self.private_contexts = set(contexts.get('browserContextIds', []))
            self.normal_contexts = {default_context}
            # Tab containers survive renderer discard/session lazy loading.
            # Keep page discovery as well for visits that close before polling.
            self.call('Target.setDiscoverTargets', {'discover': True, 'filter': EVENT_FILTER})
        except Exception:
            self.close()
            raise

    def _receive(self):
        try:
            while not self.closed:
                try:
                    raw = self.socket.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    break
                value = json.loads(raw)
                if 'id' in value:
                    with self.lock:
                        destination = self.pending.pop(value['id'], None)
                    if destination:
                        destination.put(value)
                elif value.get('method') in ('Target.targetCreated', 'Target.targetInfoChanged', 'Target.targetDestroyed'):
                    self.events.put(value)
        except Exception:
            pass
        finally:
            self.closed = True
            with self.lock:
                pending, self.pending = self.pending, {}
            for destination in pending.values():
                destination.put({'error': {'message': '浏览器连接已断开，请重新连接。'}})

    def begin(self, method, params=None, session=None):
        if self.closed:
            raise RuntimeError('浏览器连接已断开，请重新连接。')
        with self.lock:
            self.serial += 1
            token = self.serial
            result = queue.Queue(maxsize=1)
            self.pending[token] = result
        try:
            message = {'id': token, 'method': method, 'params': params or {}}
            if session:
                message['sessionId'] = session
            self.socket.send(json.dumps(message))
        except Exception:
            with self.lock:
                self.pending.pop(token, None)
            raise
        return token, result

    def finish(self, handle, timeout=10):
        token, result = handle
        try:
            response = result.get(timeout=timeout)
        except queue.Empty:
            with self.lock:
                self.pending.pop(token, None)
            raise RuntimeError('等待浏览器回应超时。请确认浏览器的连接授权提示。') from None
        if 'error' in response:
            raise RuntimeError(response['error'].get('message', '浏览器未允许这个操作。'))
        return response.get('result', {})

    def call(self, method, params=None, timeout=10):
        return self.finish(self.begin(method, params), timeout)

    def session_call(self, session, method, params=None):
        return self.finish(self.begin(method, params, session=session))

    def allowed(self, target):
        return target.get('type') in ('tab', 'page') and not target.get('subtype') and (self.include_all_profiles or target.get('browserContextId') in self.normal_contexts)

    def tabs(self):
        rows = self._read_inventory()
        if self.browser == 'Edge' and any(not r['url'] for r in rows):
            try:
                windows = self.native_metadata.windows()
                # The browser can move/close/navigate tabs during this read.
                # Verify identities, window membership, order and known URLs.
                after = self._read_inventory()
                if signature(rows) == signature(after):
                    rows = merge_windows(after, windows)
                else:
                    rows = after
                self.inventory['metadata_supplemented'] = sum('浏览器标签列表' in r['source'] for r in rows)
                self.inventory.pop('metadata_error', None)
            except Exception:
                # Missing metadata must not tear down a healthy browser
                # connection and repeatedly request browser authorization.
                self.inventory['metadata_error'] = '浏览器标签列表暂时不可用'
                self.native_metadata.close()
        return rows

    def _read_inventory(self):
        # The default CDP filter explicitly excludes "tab" containers, leaving
        # only live page renderers. Sleeping/discarded tabs must be enumerated
        # at the container level, without activating or loading their pages.
        try:
            candidates = self.call('Target.getTargets', {'filter': TAB_FILTER}).get('targetInfos', [])
        except RuntimeError:
            candidates = []  # Older embedders may not support target filters.
        kind = 'tab' if any(t.get('type') == 'tab' for t in candidates) else 'page'
        if kind == 'page':
            candidates = self.call('Target.getTargets', {'filter': PAGE_FILTER}).get('targetInfos', [])
        self.private_contexts.update(self.call('Target.getBrowserContexts').get('browserContextIds', []))
        self.normal_contexts.difference_update(self.private_contexts)
        targets = list({t['targetId']: t for t in candidates if t.get('type') == kind and self.allowed(t)}.values())
        handles = [(t, self.begin('Browser.getWindowForTarget', {'targetId': t['targetId']})) for t in targets]
        rows = []
        without_window = 0
        unmapped_tabs = 0
        for target, handle in handles:
            index_hint = (target.get('embedderData') or {}).get('tabStripIndex')
            visible_tab = isinstance(index_hint, int) and not isinstance(index_hint, bool) and index_hint >= 0
            try:
                window = self.finish(handle).get('windowId')
            except RuntimeError:
                without_window += 1
                unmapped_tabs += int(visible_tab)
                continue  # Tab may close while the list is being collected.
            if window is None:
                without_window += 1
                unmapped_tabs += int(visible_tab)
                continue  # Extension background containers have no UI window.
            self.window_cache[target['targetId']] = window
            row = {'browser': self.browser, 'window': str(window), 'target': target['targetId'],
                   'title': target.get('title') or target.get('url') or '空白标签页',
                   'url': target.get('url', ''), 'source': '实时连接'}
            metadata = target.get('embedderData') or {}
            index = metadata.get('tabStripIndex')
            if isinstance(index, int) and not isinstance(index, bool) and index >= 0:
                row['index'] = index  # Index zero is a valid first tab.
            for field, key in [('tabActive', 'active'), ('tabPinned', 'pinned'), ('tabGroupId', 'group')]:
                if field in metadata:
                    row[key] = metadata[field]
            rows.append(row)
        rows.sort(key=lambda r: (r['window'], r.get('index', float('inf')), r['target']))
        self.inventory = {'target_type': kind, 'candidates': len(candidates), 'allowed': len(targets),
                          'excluded': len(candidates) - len(targets), 'without_window': without_window,
                          'tabs': len(rows), 'windows': len({r['window'] for r in rows}),
                          'unmapped_tabs': unmapped_tabs,
                          'with_tab_index': sum('index' in r for r in rows)}
        return rows

    def drain_events(self):
        values = []
        latest = {}
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return values
            target = event.get('params', {}).get('targetInfo')
            if target and self.allowed(target):
                target_id = target['targetId']
                if target.get('type') == 'page' and target.get('parentId'):
                    self.page_parents[target_id] = target['parentId']
                # Modern page targets name their tab container as parentId.
                # Reuse the tab id so page/tab discovery and polling do not
                # count the same visit twice; retain old page events otherwise.
                canonical_id = self.page_parents.get(target_id, target_id)
                row = {'browser': self.browser, 'window': str(self.window_cache.get(canonical_id, self.window_cache.get(target_id, ''))),
                       'target': canonical_id, 'title': target.get('title', ''),
                       'url': target.get('url', ''), 'source': '实时连接'}
                previous = latest.get(canonical_id)
                if previous is not None and values[previous]['url'] == row['url']:
                    values[previous] = row
                else:
                    latest[canonical_id] = len(values)
                    values.append(row)

    def close(self):
        if hasattr(self, 'native_metadata'):
            self.native_metadata.close()
        self.closed = True
        try:
            self.socket.close()
        except Exception:
            pass
