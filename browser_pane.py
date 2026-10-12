"""One browser per column, with persistent, collapsible window groups."""
from collections import Counter
from urllib.parse import urlsplit
import tkinter as tk
from tkinter import ttk
from branding import INK, MUTED, TINT


def tab_label(row):
    if not row.get('url'):
        return '标题与网址尚未读取'
    return row.get('title') or row['url']


def site_label(row):
    url = row.get('url', '')
    if not url:
        return '网址待读取'
    parsed = urlsplit(url)
    return parsed.hostname or {'file': '本地文件', 'chrome': '浏览器设置',
                               'edge': '浏览器设置', 'about': '浏览器页面'}.get(parsed.scheme, parsed.scheme)


class BrowserPane:
    def __init__(self, parent, browser, heading_font, status_var=None,
                 connect=None, disconnect=None, on_select=None, on_open=None):
        self.browser, self.on_select, self.on_open = browser, on_select, on_open
        self.rows, self.window_names, self.rendered_ids = {}, {}, ()
        self.window_folds = {}
        self.accent = '#0D766D' if browser == 'Edge' else '#3F6F83'
        self.frame = ttk.Frame(parent, style='Rounded.TFrame', padding=(8, 8))
        header = tk.Frame(self.frame, bg='#ffffff')
        header.pack(fill='x', padx=18, pady=(10, 8))
        title_row = tk.Frame(header, bg='#ffffff')
        title_row.pack(fill='x')
        tk.Label(title_row, text='●', fg=self.accent, bg='#ffffff', font=(heading_font[0], 14)).pack(side='left', padx=(0, 8))
        tk.Label(title_row, text=browser, font=heading_font, fg=INK, bg='#ffffff').pack(side='left')
        if connect:
            ttk.Button(title_row, text='连接', style='Panel.Small.TButton', command=connect).pack(side='right')
            ttk.Button(title_row, text='断开', style='Panel.Quiet.TButton', command=disconnect).pack(side='right', padx=6)
        self.count = tk.Label(header, text='0 个窗口 · 0 个标签页', fg=MUTED, bg='#ffffff', anchor='w')
        self.count.pack(fill='x', pady=(8, 0))
        if status_var:
            self.status = tk.Label(header, textvariable=status_var, fg=MUTED, bg='#ffffff', anchor='w',
                                   justify='left', wraplength=390)
            self.status.pack(fill='x', pady=(4, 0))
        body = ttk.Frame(self.frame, style='White.TFrame')
        body.pack(fill='both', expand=True, padx=10)
        self.tree = ttk.Treeview(body, columns=('site',), show='tree headings', selectmode='browse', height=1)
        self.tree.heading('#0', text='窗口 / 网页标题', anchor='w')
        self.tree.heading('site', text='网站', anchor='w')
        self.tree.column('#0', width=330, minwidth=190, stretch=True)
        self.tree.column('site', width=155, minwidth=110, stretch=False)
        scrollbar = ttk.Scrollbar(body, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.tag_configure('window', background='#EEF5F1', foreground=self.accent, font=heading_font[0:1] + (12,))
        self.tree.tag_configure('missing', foreground='#9b651f')
        self.tree.bind('<<TreeviewSelect>>', self._select)
        self.tree.bind('<Double-1>', self._open)
        self.tree.bind('<Return>', self._open)
        self.detail = tk.Label(self.frame, text='展开窗口查看标签页；双击网页即可打开', fg=MUTED, bg='#ffffff',
                               anchor='w', justify='left', wraplength=460, height=2)
        self.detail.pack(fill='x', padx=18, pady=(6, 8))
        self.frame.bind('<Configure>', lambda e: self.detail.configure(wraplength=max(200, e.width - 36)))

    def _select(self, event=None):
        row = self.selected()
        if row:
            url = row.get('url', '')
            self.detail.configure(text=(url[:120] + '…' if len(url) > 120 else url) or '尚未读到这个标签的网址，暂时无法打开或恢复。')
            if self.on_select:
                self.on_select(self.browser)

    def _open(self, event=None):
        row = self.selected()
        if row and self.on_open:
            self.on_open(row)

    def selected(self):
        selected = self.tree.selection()
        return self.rows.get(selected[0]) if selected else None

    def expand_all(self, expanded):
        for group in self.tree.get_children():
            self.tree.item(group, open=expanded)

    def update(self, rows, query='', reset_scroll=False):
        rows = [row for row in rows if row['browser'] == self.browser]
        windows = Counter(row['window'] for row in rows)
        missing = sum(not row.get('url') for row in rows)
        self.count.configure(text=f'{len(windows)} 个窗口 · {len(rows)} 个标签页' +
                             (f' · {missing} 个网址待读取' if missing else ''), fg='#9b651f' if missing else MUTED)
        ordered_windows = sorted(windows, key=lambda w: (not str(w).isdigit(), int(w) if str(w).isdigit() else str(w)))
        self.window_names = {window: f'窗口 {index + 1}' for index, window in enumerate(ordered_windows)}
        shown = [r for r in rows if query.lower() in
                 f"{self.window_names[r['window']]} {tab_label(r)} {r.get('url', '')}".lower()]
        groups = {}
        for row in shown:
            groups.setdefault(row['window'], []).append(row)
        selection, focus, position = self.tree.selection(), self.tree.focus(), self.tree.yview()[0]
        for group in self.tree.get_children():
            self.window_folds[group] = bool(self.tree.item(group, 'open'))
        # Anchor the first visible row so insertion above it does not shift the view.
        anchor = self.tree.identify_row(self.tree.winfo_height() and 45)
        wanted_groups = {f'window:{self.browser}:{w}' for w in groups}
        self.rows = {f"tab:{self.browser}:{r['target']}": r for r in shown}
        wanted = wanted_groups | set(self.rows)
        for group in self.tree.get_children():
            for child in self.tree.get_children(group):
                if child not in wanted:
                    self.tree.delete(child)
        for group in self.tree.get_children():
            if group not in wanted_groups:
                # Move surviving tabs before removing an obsolete group.
                for child in self.tree.get_children(group):
                    if child in self.rows:
                        self.tree.move(child, '', 'end')
                self.tree.delete(group)
        visible_ids = []
        for index, (window, tabs) in enumerate(groups.items()):
            group = f'window:{self.browser}:{window}'
            label = f"{self.window_names[window]}   ·   {len(tabs)} 个标签页"
            group_missing = sum(not r.get('url') for r in tabs)
            values = (f'{group_missing} 个网址待读取' if group_missing else '',)
            if not self.tree.exists(group):
                self.tree.insert('', 'end', iid=group, text=label, values=values, tags=('window',), open=self.window_folds.get(group, False))
            elif self.tree.item(group, 'text') != label or self.tree.item(group, 'values') != values:
                self.tree.item(group, text=label, values=values)
            if self.tree.index(group) != index:
                self.tree.move(group, '', index)
            visible_ids.append(group)
            for child_index, row in enumerate(tabs):
                rid = f"tab:{self.browser}:{row['target']}"
                text, values = tab_label(row), (site_label(row),)
                tags = ('missing',) if not row.get('url') else ()
                if not self.tree.exists(rid):
                    self.tree.insert(group, 'end', iid=rid, text=text, values=values, tags=tags)
                elif self.tree.item(rid, 'text') != text or self.tree.item(rid, 'values') != values or self.tree.item(rid, 'tags') != tags:
                    self.tree.item(rid, text=text, values=values, tags=tags)
                if self.tree.parent(rid) != group or self.tree.index(rid) != child_index:
                    self.tree.move(rid, group, child_index)
                if self.tree.item(group, 'open'):
                    visible_ids.append(rid)
        self.tree.selection_set(tuple(r for r in selection if r in self.rows))
        if focus in wanted:
            self.tree.focus(focus)
        if reset_scroll:
            self.tree.yview_moveto(0)
        elif anchor in visible_ids:
            self.tree.yview_moveto(visible_ids.index(anchor) / max(1, len(visible_ids)))
        else:
            self.tree.yview_moveto(position)
        if not rows:
            self.detail.configure(text='连接后，此处会按窗口显示当前标签页。')
        elif not shown:
            self.detail.configure(text='没有匹配的网页；清空搜索可查看全部窗口。')
        elif not self.selected():
            self.detail.configure(text='展开窗口查看标签页；双击网页即可打开')
