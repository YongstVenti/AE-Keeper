import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from cdp import BrowserConnection
from browser_setup import open_settings, SETTINGS_URLS
from storage import Archive, safe_url
from desktop_windows import capture_desktop, restore_desktop
from workspace_model import demo_workspace, workspace_counts
from window_previews import attach_previews


def default_roots():
    root = Path(os.environ.get('LOCALAPPDATA', '.'))
    return {'Chrome': str(root / 'Google/Chrome/User Data'), 'Edge': str(root / 'Microsoft/Edge/User Data')}


def browser_exe(browser):
    names = ['Google/Chrome/Application/chrome.exe'] if browser == 'Chrome' else ['Microsoft/Edge/Application/msedge.exe']
    for base in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        for name in names:
            p = Path(os.environ.get(base, '')) / name
            if p.is_file():
                return str(p)
    raise RuntimeError(f'没有找到{browser}，请先安装浏览器。')


def read_bookmarks(root, browser):
    root, rows = Path(root), []
    info = {}
    try:
        info = json.loads((root / 'Local State').read_text(encoding='utf-8')).get('profile', {}).get('info_cache', {})
    except (OSError, ValueError):
        pass
    profiles = sorted(set(['Default'] + list(info) + [p.name for p in root.glob('Profile *') if p.is_dir()]))
    for name in profiles:
        file = root / name / 'Bookmarks'
        if not file.exists():
            continue
        data = json.loads(file.read_text(encoding='utf-8'))
        profile = info.get(name, {}).get('name', name)
        def walk(node, parents):
            if node.get('type') == 'url' and safe_url(node.get('url')):
                rows.append({'url': node['url'], 'title': node.get('name') or node['url'],
                             'paths': [' / '.join([browser, profile] + parents)], 'sources': [browser]})
            else:
                path = parents + ([node['name']] if node.get('name') else [])
                for child in node.get('children', []):
                    walk(child, path)
        for node in data.get('roots', {}).values():
            walk(node, [])
    return rows


class Engine(threading.Thread):
    def __init__(self, folder, output, demo=False):
        super().__init__(daemon=True)
        self.archive = Archive(folder)
        config = self.archive.settings()
        roots = default_roots()
        if not config['chrome_root']:
            config['chrome_root'] = roots['Chrome']
        if not config['edge_root']:
            config['edge_root'] = roots['Edge']
        self.archive.save_settings(config)
        self.archive.preserve_workspace_on_start()
        self.commands, self.output = queue.Queue(), output
        self.clients, self.current, self.last_urls = {}, [], {}
        self.status = {'Chrome': '未连接', 'Edge': '未连接'}
        self.stop_event = threading.Event()
        self.last_scan, self.last_capture = 0, 0
        self.last_workspace_capture = 0
        self.demo = demo
        self.pending_connections = set()
        self.connection_attempts = set()
        self.setup_opened = set()
        self.last_connection_check = 0
        self.connection_deadline = 0

    def submit(self, action, **values):
        self.commands.put((action, values))

    def emit(self, kind, **values):
        self.output.put({'kind': kind, **values})

    def observe(self, rows):
        changed = set()
        for r in rows:
            key = (r['browser'], r['target'])
            if self.last_urls.get(key) != r['url']:
                changed.add(key)
                self.last_urls[key] = r['url']
        self.archive.record(rows, changed)

    def scan(self):
        if self.demo:
            return
        current = []
        for browser, client in list(self.clients.items()):
            try:
                rows = client.tabs()
                current.extend(rows)
                self.observe(rows)
                windows = len({row['window'] for row in rows})
                self.status[browser] = f'已连接 · {windows} 个窗口 · {len(rows)} 个标签页'
            except Exception:
                client.close()
                self.clients.pop(browser, None)
                self.status[browser] = '连接已断开，点击重新连接'
        self.current = sorted(current, key=lambda r: (r['browser'], r['window'], r.get('index', float('inf')), r['target']))
        self.last_scan = time.monotonic()

    def save(self):
        self.scan()
        connected = sorted(self.clients) if not self.demo else ['Chrome', 'Edge']
        # Legacy page-only inventory can omit sleeping tabs. Daily archives
        # require full tab-container enumeration in both browser connections.
        complete = self.demo or (set(connected) == {'Chrome', 'Edge'} and all(
            client.inventory.get('target_type') == 'tab' and
            client.inventory.get('unmapped_tabs', 0) == 0 and
            client.inventory.get('with_tab_index') == client.inventory.get('tabs') and
            client.inventory.get('tabs') == sum(r['browser'] == browser for r in self.current)
            for browser, client in self.clients.items()))
        result = self.archive.capture(self.current, connected, complete=complete)
        self.last_capture = time.monotonic()
        return result

    def state(self):
        settings = self.archive.settings()
        self.emit('state', settings=settings, status=dict(self.status), current=self.current,
                  stats=self.archive.stats(), last_saved=self.archive.meta('last_saved'),
                  last_workspace_saved=self.archive.meta('last_workspace_saved'),
                  fallback=self.archive.meta('fallback', False))

    def save_workspace(self, manual=False, interactive=None):
        interactive = manual if interactive is None else interactive
        settings = self.archive.settings()
        payload = demo_workspace() if self.demo else capture_desktop(include_minimized=settings['workspace_include_minimized'])
        if not self.demo:
            previous = self.archive.listing('workspaces', limit=1)
            if interactive and settings['workspace_previews']:
                self.emit('message', text='正在拍摄全部窗口；会短暂显示预览板，不会展开或切换原窗口。')
            payload = attach_previews(payload, previous[0]['payload'] if previous else None,
                                      enabled=settings['workspace_previews'], interactive=interactive, options=settings)
        result = self.archive.capture_workspace(payload, manual=manual)
        self.last_workspace_capture = time.monotonic()
        if interactive:
            windows, tabs, sessions = workspace_counts(payload)
            self.emit('message', text=f'桌面窗口已保留：{windows} 个窗口、{tabs} 个文件夹标签、{sessions} 个终端会话。' if result
                      else '当前没有可保存的非最小化窗口；之前的桌面窗口已保留。')
        if result:
            self.emit('workspace_saved', manual=manual)
        return result

    def open_connection_setup(self, browser, force=False):
        if browser in self.setup_opened and not force:
            return
        self.setup_opened.add(browser)
        url = SETTINGS_URLS[browser]
        self.status[browser] = '正在打开连接设置，请稍候'
        self.state()
        try:
            settings = self.archive.settings()
            root = settings['chrome_root' if browser == 'Chrome' else 'edge_root']
            result = open_settings(browser, browser_exe(browser), root)
            if result.opened:
                self.status[browser] = '已找到连接开关，请开启并留意授权提示'
                self.emit('message', text=f'{browser}连接设置已打开并找到远程调试开关。请自行开启；软件不会替你改变授权。')
            else:
                self.status[browser] = '自动打开未完成，请按提示手动打开'
                self.emit('setup_failed', browser=browser, url=result.url, text=result.reason)
        except Exception as e:
            self.pending_connections.discard(browser)
            self.status[browser] = '连接设置未打开：' + str(e)
            self.emit('setup_failed', browser=browser, url=url, text=str(e))
        self.state()

    def check_connections(self):
        now = time.monotonic()
        if not self.pending_connections or now - self.last_connection_check < 2:
            return
        self.last_connection_check = now
        if now >= self.connection_deadline:
            for browser in self.pending_connections:
                self.status[browser] = '等待已结束，点击连接两个浏览器重试'
            self.pending_connections.clear()
            self.state()
            return
        settings = self.archive.settings()
        for browser in sorted(self.pending_connections):
            root = Path(settings['chrome_root' if browser == 'Chrome' else 'edge_root'])
            try:
                signature = (root / 'DevToolsActivePort').read_text(encoding='utf-8')
            except OSError:
                self.open_connection_setup(browser)
                continue
            attempt = (browser, signature)
            if attempt in self.connection_attempts:
                continue  # A declined request must not produce repeated permission dialogs.
            self.connection_attempts.add(attempt)
            try:
                self.handle('connect', {'browser': browser})
                self.pending_connections.discard(browser)
            except Exception as e:
                # An existing debug endpoint already has its settings enabled.
                # A timeout/decline must not open more windows or reconnect.
                self.emit('message', text=f'{browser}连接尚未成功：{e}。请核对连接设置；允许后仍未接上可点击连接重试。')
        self.state()

    def handle(self, action, values):
        s = self.archive.settings()
        if action == 'connect_both':
            if self.demo:
                self.emit('message', text='演示模式不会连接实际浏览器。')
                return
            self.pending_connections = {'Chrome', 'Edge'} - set(self.clients)
            self.connection_attempts.clear()
            self.setup_opened.clear()
            self.last_connection_check = 0
            self.connection_deadline = time.monotonic() + 600
            self.emit('message', text='正在查找两个浏览器。软件会尝试打开并核对设置页；看到开关后请自行开启，授权提示请点允许。')
            self.check_connections()
        elif action == 'open_setup':
            if self.demo:
                self.emit('message', text='演示模式不会打开实际浏览器。')
                return
            self.open_connection_setup(values['browser'], force=True)
        elif action == 'connect':
            browser = values['browser']
            if browser in self.clients:
                if not self.clients[browser].closed:
                    self.scan()
                    self.emit('message', text=f'{browser}已连接，已刷新标签列表。')
                    return
                self.clients.pop(browser).close()
            self.status[browser] = '等待连接，请留意浏览器授权提示'
            self.state()
            try:
                client = BrowserConnection(s['chrome_root' if browser == 'Chrome' else 'edge_root'], browser, include_all_profiles=s['include_all_profiles'])
                self.clients[browser] = client
                self.pending_connections.discard(browser)
                self.save()
                scope = '全部可见个人资料，包括无痕窗口' if s['include_all_profiles'] else '默认普通个人资料的所有窗口'
                self.emit('message', text=f'{browser}已连接。读取范围：{scope}。')
            except Exception as e:
                self.status[browser] = '连接未成功：' + str(e)
                raise e
        elif action == 'disconnect':
            browser = values['browser']
            self.pending_connections.discard(browser)
            client = self.clients.pop(browser, None)
            if client:
                client.close()
            self.status[browser] = '已断开'
            self.scan()
        elif action == 'save':
            browser_saved = self.save()
            desktop_saved = self.save_workspace(manual=True)
            self.emit('message', text=('浏览器快照及桌面窗口已保存。' if browser_saved and desktop_saved else
                                      '桌面窗口已保存；浏览器尚未连接。' if desktop_saved else
                                      '浏览器快照已保存；没有可保存的工作窗口。' if browser_saved else '当前没有可保存的窗口。'))
        elif action == 'workspace_save':
            self.save_workspace(manual=not values.get('daily', False), interactive=True)
        elif action == 'workspace_enabled':
            self.archive.save_settings({'workspace_enabled': bool(values['enabled'])})
            self.emit('message', text='桌面窗口自动保存已开启。' if values['enabled'] else '桌面窗口自动保存已暂停。')
        elif action == 'workspace_options':
            values = {key: bool(values[key]) for key in ('workspace_include_minimized', 'workspace_previews') if key in values}
            self.archive.save_settings(values)
            self.last_workspace_capture = 0
            self.emit('message', text='窗口保存范围与预览设置已更新。')
        elif action == 'workspace_restore':
            if self.demo:
                self.emit('message', text='演示模式不会打开真实工作窗口。')
            else:
                snapshot = self.archive.workspace(values['id'])
                result = restore_desktop(snapshot['payload'], values['selected'],
                                         progress=lambda text: self.emit('message', text=text))
                self.emit('workspace_restored', **result)
        elif action == 'list':
            self.emit('list', view=values['view'], rows=self.archive.listing(values['view'], values.get('query', ''), values.get('day', ''), thumbnails_only=values['view'] in ('workspaces', 'workspace_daily')))
        elif action == 'settings':
            previous = self.archive.settings()
            saved = self.archive.save_settings(values['value'])
            for browser, key in [('Chrome', 'chrome_root'), ('Edge', 'edge_root')]:
                if previous[key] != saved[key] or previous['include_all_profiles'] != saved['include_all_profiles']:
                    self.pending_connections.discard(browser)
                    client = self.clients.pop(browser, None)
                    if client:
                        client.close()
                    self.status[browser] = '连接设置已更新，请重新连接'
            self.emit('message', text='设置已保存。')
        elif action == 'bookmarks':
            rows = []
            for browser, key in [('Chrome', 'chrome_root'), ('Edge', 'edge_root')]:
                rows.extend(read_bookmarks(s[key], browser))
            added = self.archive.merge_bookmarks(rows)
            self.emit('message', text=f'已读取两种浏览器的收藏，合并后新增{added}个网址。')
        elif action == 'note':
            self.archive.note(values['view'], values['id'], values['text'])
            self.emit('message', text='备注已保存。')
        elif action == 'daily_add':
            self.archive.add_daily(values['id'], values['day'], values['mode'])
            self.emit('daily_changed', day=values['day'])
            self.emit('message', text='已加入每日存档。主存档已固定，原有快照仍保留。')
        elif action == 'daily_primary':
            self.archive.primary_daily(values['entry_id'])
            self.emit('daily_changed', day=self.archive.daily_entry(values['entry_id'])['day'], primary=True)
            self.emit('message', text='已设为当天主存档，原主存档保留在子项中。')
        elif action == 'delete':
            self.archive.delete(values['view'], values['id'])
            self.emit('message', text='已删除档案，浏览器原有标签页和书签不会改变。')
        elif action == 'export':
            self.archive.export(values['path'])
            self.emit('message', text='备份已保存。')
        elif action == 'import':
            self.archive.import_backup(values['path'])
            self.emit('message', text='备份已合并，原有档案已保留。')
        elif action == 'restore':
            view = 'daily' if values.get('view') == 'daily' else 'snapshots'
            snapshot = self.archive.daily_entry(values['entry_id']) if view == 'daily' and values.get('entry_id') else next((r for r in self.archive.listing(view, limit=100000) if r['id'] == values['id']), None)
            if snapshot is None:
                raise ValueError('这个快照已不存在。')
            groups = {}
            for tab in snapshot['tabs']:
                if safe_url(tab['url']):
                    groups.setdefault((tab['browser'], tab['window']), []).append(tab['url'])
            count = 0
            for (browser, window), urls in groups.items():
                # Launch into the user's normal browser, preserving existing windows.
                # Browser command-line length is finite; split oversized sessions into new windows.
                batch, length = [], 0
                for url in urls:
                    if len(url) > 24000:
                        continue
                    if batch and length + len(url) > 24000:
                        subprocess.Popen([browser_exe(browser), '--new-window', *batch])
                        count += len(batch)
                        batch, length = [], 0
                    batch.append(url)
                    length += len(url) + 3
                if batch:
                    subprocess.Popen([browser_exe(browser), '--new-window', *batch])
                    count += len(batch)
            self.emit('message', text=f'已请求在原浏览器的新窗口打开{count}个网页。内部页面未自动恢复。')
        self.state()

    def run(self):
        if self.demo:
            self.current = [{'browser': b, 'window': w, 'target': f'{b}-{i}', 'title': title, 'url': url, 'source': '演示数据'}
                            for i, (b, w, title, url) in enumerate([
                                ('Chrome', '1', '武汉老街与城市记忆', 'https://example.com/wuhan'),
                                ('Chrome', '1', '家庭照片整理笔记', 'https://example.com/photos'),
                                ('Chrome', '2', '旧居户型资料', 'https://example.com/home'),
                                ('Edge', '3', '旅行计划与航程记录', 'https://example.com/travel'),
                                ('Edge', '3', '家具与物件的故事', 'https://example.com/objects')])]
            self.status = {'Chrome': '演示 · 3个标签页', 'Edge': '演示 · 2个标签页'}
            self.archive.capture(self.current, ['Chrome', 'Edge'])
            self.save_workspace()
        self.state()
        while not self.stop_event.is_set():
            try:
                action, values = self.commands.get(timeout=.5)
            except queue.Empty:
                action = None
            try:
                if action:
                    self.handle(action, values)
                if not self.demo:
                    self.check_connections()
                    for client in list(self.clients.values()):
                        self.observe(client.drain_events())
                    now = time.monotonic()
                    s = self.archive.settings()
                    if now - self.last_scan >= 5:
                        self.scan()
                        self.state()
                    if s['enabled'] and self.current and now - self.last_capture >= s['interval']:
                        self.save()
                        self.state()
                    if s['workspace_enabled'] and now - self.last_workspace_capture >= s['interval']:
                        # No browser connection or permission required for desktop metadata.
                        self.last_workspace_capture = now
                        self.save_workspace()
                        self.state()
            except Exception as e:
                self.emit('error', text=str(e))
                self.last_capture = time.monotonic()
                self.state()
        for client in self.clients.values():
            client.close()
        self.archive.db.close()
