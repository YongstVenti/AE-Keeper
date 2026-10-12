"""Read-only metadata from the browser's own tab-search UI.

Never activate, attach to, or load a user's sleeping page. Window matching
requires an exact tab count/order plus an existing URL anchor, and must be
unique. Ambiguous or changing inventories remain explicitly unresolved.
"""
import json
from collections import defaultdict


EDGE_EXPRESSION = """(async () => {
  const shell = document.querySelector('app-shell');
  const app = shell && shell.shadowRoot.querySelector('tab-search-app');
  if (!app || !app.controlMojomService) throw new Error('Tab search unavailable');
  const data = (await app.controlMojomService.handler.getData()).data;
  return JSON.stringify({windows: data.windows.map(w => ({tabs: w.tabs.map(t => ({
    title: t.title, url: t.url, active: t.active
  }))}))});
})()"""


def signature(rows):
    return sorted((r['target'], r['window'], r.get('index'), r.get('url', ''),
                   bool(r.get('active'))) for r in rows)


def merge_windows(rows, windows):
    groups = defaultdict(list)
    for row in rows:
        groups[row['window']].append(row)
    choices = {}
    for wid, group in groups.items():
        group.sort(key=lambda r: r.get('index', -1))
        if [r.get('index') for r in group] != list(range(len(group))):
            continue
        anchors = [r for r in group if r.get('url')]
        if not anchors:
            continue
        valid = []
        for i, window in enumerate(windows):
            tabs = window.get('tabs', [])
            if len(tabs) != len(group):
                continue
            def url(t):
                value = t.get('url', '')
                return value.get('url', '') if isinstance(value, dict) else value
            if any(r['url'] != url(tabs[r['index']]) for r in anchors):
                continue
            if any('active' in r and bool(r['active']) != bool(tabs[r['index']].get('active')) for r in group):
                continue
            if any(not isinstance(url(t), str) or not url(t) for t in tabs):
                continue
            valid.append(i)
        choices[wid] = valid
    # Resolve only uniquely forced one-to-one assignments; do not guess based
    # on list position, identical window sizes, titles or browsing history.
    matched, used = {}, set()
    while True:
        forced = {wid: [i for i in options if i not in used]
                  for wid, options in choices.items() if wid not in matched}
        singles = {wid: options[0] for wid, options in forced.items() if len(options) == 1}
        additions = {wid: i for wid, i in singles.items() if sum(j == i for j in singles.values()) == 1}
        if not additions:
            break
        matched.update(additions)
        used.update(additions.values())
    output = [dict(r) for r in rows]
    for row in output:
        if row.get('url') or row['window'] not in matched:
            continue
        tab = windows[matched[row['window']]]['tabs'][row['index']]
        value = tab['url']
        row['url'] = value.get('url', '') if isinstance(value, dict) else value
        row['title'] = tab.get('title') or row['url']
        row['source'] = '实时连接 · 浏览器标签列表'
    return output


class NativeMetadata:
    def __init__(self, connection):
        self.connection = connection
        self.session = None
        self.target = None

    def windows(self):
        c = self.connection
        # Edge's internal page is profile-scoped. Explicitly filter it by the
        # same normal profile as the CDP tab inventory, including private gates.
        candidates = c.call('Target.getTargets', {'filter': [
            {'type': 'browser_ui', 'exclude': False}, {'exclude': True}
        ]}).get('targetInfos', [])
        expected = 'edge://tab-search.top-chrome/'
        targets = [t for t in candidates if t.get('type') == 'browser_ui'
                   and t.get('url') == expected and not t.get('subtype')
                   and t.get('browserContextId') in c.normal_contexts]
        if c.browser != 'Edge' or len(targets) != 1:
            return []
        target = targets[0]['targetId']
        if self.target != target or self.session is None:
            self.close()
            self.session = c.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
            self.target = target
        result = c.session_call(self.session, 'Runtime.evaluate', {
            'expression': EDGE_EXPRESSION, 'awaitPromise': True, 'returnByValue': True
        })
        if result.get('exceptionDetails'):
            raise RuntimeError('浏览器标签列表暂时不可用。')
        data = json.loads(result.get('result', {}).get('value', '{}'))
        return data.get('windows', [])

    def close(self):
        if self.session:
            try:
                self.connection.call('Target.detachFromTarget', {'sessionId': self.session}, timeout=2)
            except Exception:
                pass
        self.session = self.target = None
