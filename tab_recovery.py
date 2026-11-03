"""A manually started, bounded queue. Never create, reload, discard or close tabs."""
import time
import threading


class TabRecovery:
    def __init__(self):
        self.running = False
        self.browser = ''
        self.message = '未启动逐个补读'
        self.cancel = threading.Event()

    def start(self, browser, rows, settings):
        if self.running:
            raise ValueError('请先暂停正在进行的补读。')
        remaining = {r['target']: i for i,r in enumerate(getattr(self, 'queue', []))} if self.browser == browser else {}
        self.browser = browser
        self.cancel.clear()
        self.queue = [dict(r) for r in rows if r['browser'] == browser and not r.get('url')]
        self.queue.sort(key=lambda r: (0, remaining[r['target']]) if r['target'] in remaining else
                        (1, r['window'], r.get('index', float('inf')), r['target']))
        self.settings = settings
        self.current = None
        self.done = self.failed = self.attempts = 0
        self.consecutive_failures = 0
        self.due = 0
        self.running = bool(self.queue)
        self.message = f'待补读 {len(self.queue)} 个标签；本批最多 {settings["recovery_batch"]} 个' if self.running else '当前没有未读取的网址'

    def pause(self, reason='已暂停补读'):
        self.cancel.set()
        self.running = False
        self.message = f'{reason} · 已读 {self.done} 个，失败 {self.failed} 个' if hasattr(self, 'done') else reason

    def request_pause(self):
        self.cancel.set()

    def step(self, client, on_url, resources, now=None):
        now = time.monotonic() if now is None else now
        if not self.running or now < self.due:
            return
        if self.cancel.is_set():
            self.pause('已手动暂停'); return
        available_mb, cpu = resources
        if available_mb < self.settings['recovery_memory_mb'] or cpu >= 85:
            self.pause('资源紧张，已暂停；不会继续唤醒网页')
            return
        if getattr(client, 'closed', False):
            self.pause('浏览器连接不可用，已暂停；不会自动重连')
            return
        try:
            if self.current is None:
                if self.attempts >= self.settings['recovery_batch'] or not self.queue:
                    self.pause('本批补读已结束，请按需继续')
                    return
                self.current = self.queue.pop(0)
                self.attempts += 1
                self.started = now
                self.activated = False
            info = client.recovery_info(self.current['target'])
            if self.cancel.is_set():
                self.pause('已手动暂停'); return
            if str(info['window']) != self.current['window']:
                self._finish(False, now)
                return
            if info.get('url'):
                on_url({**self.current, 'url': info['url'], 'title': info.get('title') or info['url'],
                        'source': '实时连接 · 逐个补读'})
                self._finish(True, now)
            elif now - self.started >= self.settings['recovery_timeout']:
                self._finish(False, now)
            elif not self.activated:
                # One existing target only; no page navigation or loading of a
                # replacement URL. Activation may wake a sleeping renderer.
                client.call('Target.activateTarget', {'targetId': self.current['target']}, timeout=2)
                self.activated = True
                self.due = now + 2
                self.message = f'正在读取第 {self.attempts} 个标签 · 已读 {self.done} 个；可随时暂停'
            else:
                self.due = now + 2
        except Exception:
            # A protocol timeout is a stop condition, not an invitation to send
            # more commands, disconnect, or ask for browser authorization again.
            self.pause('浏览器未能及时回应，已暂停')

    def _finish(self, success, now):
        self.done += int(success)
        self.failed += int(not success)
        self.consecutive_failures = 0 if success else self.consecutive_failures + 1
        self.current = None
        self.due = now + self.settings['recovery_interval']
        self.message = f'已读 {self.done} 个，失败 {self.failed} 个 · 等待 {self.settings["recovery_interval"]} 秒'
        if self.consecutive_failures >= 2:
            self.pause('连续两页未能读到网址，已暂停')
        elif self.attempts >= self.settings['recovery_batch'] or not self.queue:
            self.pause('本批补读已结束，请按需继续')
