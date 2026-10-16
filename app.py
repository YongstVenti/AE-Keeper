import argparse
import base64
import ctypes
import hashlib
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, simpledialog
from tkinter import font as tkfont
from engine import Engine, browser_exe
from browser_pane import BrowserPane
from workspace_pane import WorkspacePane
from rounded_theme import install_rounded_theme
from window_effects import apply_window_effects
from branding import NAME, TITLE, BG, WHITE, INK, MUTED, ACCENT as GREEN, HOVER, TINT, SELECTED_INK, apply_icon, logo_photo, logo_image

APP_NAME = TITLE


def load_heading_font(root):
    font_file = Path(__file__).parent / 'assets/SmileySans-Oblique.ttf'
    if os.name == 'nt' and font_file.is_file():
        ctypes.windll.gdi32.AddFontResourceExW(str(font_file), 0x10, None)
    families = set(tkfont.families(root))
    for family in ('Smiley Sans', 'Smiley Sans Oblique', '得意黑'):
        if family in families:
            return family
    return 'Microsoft YaHei UI'


def fmt_time(value):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(value)) if value else '尚未保存'


def set_startup(enabled):
    if not getattr(sys, 'frozen', False):
        if enabled:
            raise ValueError('请先安装桌面版，再启用开机启动。')
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled:
            winreg.SetValueEx(key, 'BrowseTraceDesktop', 0, winreg.REG_SZ, f'"{sys.executable}" --tray')
        else:
            try:
                winreg.DeleteValue(key, 'BrowseTraceDesktop')
            except FileNotFoundError:
                pass


class App:
    def __init__(self, folder, demo=False, start_hidden=False, initial_view='tabs', capture_workspace=False):
        self.root = tk.Tk()
        self.root.title(APP_NAME + ('（演示）' if demo else ''))
        apply_icon(self.root)
        self.root.geometry('1320x850')
        self.root.minsize(1100, 760)
        self.root.configure(bg=BG)
        self.folder, self.demo = Path(folder), demo
        self.output = queue.Queue()
        self.engine = Engine(folder, self.output, demo)
        self.view, self.rows, self.settings = 'tabs', {}, {}
        self.daily_view = 'daily'
        self.current, self.stats, self.tray = [], {}, None
        self.preview_new_workspace = capture_workspace
        self.preview_workspace_ready = False
        self.heading_family = load_heading_font(self.root)
        self.active_browser = None
        self.rendered_view = None
        self.search_var, self.day_var, self.message_var = tk.StringVar(), tk.StringVar(), tk.StringVar(value='请先连接 Edge 和 Chrome；不需要安装插件。')
        self.connection_vars = {b: tk.StringVar(value='未连接') for b in ('Chrome', 'Edge')}
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=(self.heading_family, 11), foreground=INK)
        for name in tkfont.names(self.root):
            tkfont.nametofont(name).configure(family=self.heading_family)
        for name in ('TkDefaultFont','TkTextFont','TkHeadingFont'):
            tkfont.nametofont(name).configure(size=11)
        style.configure('TFrame', background=BG)
        style.configure('White.TFrame', background=WHITE)
        style.configure('TLabel', background=BG, foreground=INK)
        style.configure('TCheckbutton', background=BG, foreground=INK)
        style.map('TCheckbutton', background=[('active', BG)])
        style.configure('White.TLabel', background=WHITE, foreground=INK)
        style.configure('Muted.TLabel', foreground=MUTED)
        style.configure('Heading.TLabel', font=(self.heading_family, 24))
        style.configure('TButton', padding=(13, 8), background=WHITE, borderwidth=0, relief='flat')
        style.map('TButton', background=[('active', TINT)])
        style.configure('Accent.TButton', foreground=WHITE, background=GREEN)
        style.map('Accent.TButton', background=[('active', HOVER)], foreground=[('active', WHITE)])
        style.configure('Primary.TButton', foreground=WHITE, background=GREEN, font=(self.heading_family, 13), padding=(18, 9))
        style.map('Primary.TButton', background=[('active', HOVER)], foreground=[('active', WHITE)])
        style.configure('Small.TButton', padding=(12, 7), font=(self.heading_family, 10))
        style.configure('Quiet.TButton', padding=(10, 7), foreground=MUTED, borderwidth=0, font=(self.heading_family, 10))
        style.configure('Nav.TButton', padding=(15, 7), background=BG, borderwidth=0)
        style.configure('SelectedNav.TButton', padding=(15, 7), background=TINT, foreground=GREEN, borderwidth=0)
        style.configure('Treeview', rowheight=34, font=(self.heading_family, 11), background=WHITE, fieldbackground=WHITE, borderwidth=0)
        style.layout('Treeview', [('Treeview.treearea', {'sticky':'nsew'})])
        style.configure('Treeview.Heading', background=BG, font=(self.heading_family, 11), padding=(10, 8), borderwidth=0, relief='flat')
        style.map('Treeview', background=[('selected', TINT)], foreground=[('selected', SELECTED_INK)])
        install_rounded_theme(self.root, style)
        self.build()
        if initial_view == 'workspaces':
            self.switch('workspaces')
        self.window_effects = apply_window_effects(self.root)
        self.root.bind_all('<Map>', lambda event: apply_window_effects(event.widget)
                           if event.widget == self.root or isinstance(event.widget, tk.Toplevel) else None, add='+')
        self.root.protocol('WM_DELETE_WINDOW', self.hide)
        self.root.after(100, self.poll)
        self.engine.start()
        if capture_workspace:
            self.engine.submit('workspace_save')
        self.make_tray()
        if start_hidden:
            self.root.after(500, self.hide)

    def build(self):
        top = ttk.Frame(self.root, padding=(24, 12))
        top.pack(fill='x')
        brand = ttk.Frame(top)
        brand.pack(side='left')
        self.brand_logo = logo_photo(self.root, 64)
        ttk.Label(brand, image=self.brand_logo).pack(side='left',padx=(0,14))
        copy=ttk.Frame(brand);copy.pack(side='left')
        ttk.Label(copy, text=NAME, style='Heading.TLabel').pack(anchor='w')
        ttk.Label(copy, text='网页与桌面窗口 · 按天归档，随时找回', style='Muted.TLabel').pack(anchor='w', pady=(3, 0))
        ttk.Button(top, text='现在保存', style='Primary.TButton', command=lambda: self.engine.submit('save')).pack(side='right')
        ttk.Button(top, text='连接两个浏览器', command=lambda: self.engine.submit('connect_both')).pack(side='right', padx=10)
        ttk.Button(top, text='导出备份', style='Quiet.TButton', command=self.export).pack(side='right', padx=8)
        strip = ttk.Frame(self.root, padding=(24, 0))
        strip.pack(fill='x', pady=(0, 8))
        self.summary = ttk.Label(strip, text='最近保存：尚未保存', style='Muted.TLabel')
        self.summary.pack(side='left')
        ttk.Button(strip, text='连接帮助', style='Quiet.TButton', command=self.guide).pack(side='right')
        nav = ttk.Frame(self.root, padding=(24, 0))
        nav.pack(fill='x')
        self.nav_buttons = {}
        for key, title in [('tabs', '浏览器标签页'), ('workspaces', '桌面窗口'), ('daily', '每日存档'), ('snapshots', '窗口快照'), ('visits', '浏览记录'), ('bookmarks', '收藏夹'), ('settings', '保存设置')]:
            button = ttk.Button(nav, text=title, style='Nav.TButton', command=lambda k=key: self.switch(self.daily_view if k == 'daily' else k))
            button.pack(side='left', padx=(0, 8))
            self.nav_buttons[key] = button
        self.content = ttk.Frame(self.root, padding=(24, 12))
        self.content.pack(fill='both', expand=True)
        self.filter_bar = ttk.Frame(self.content)
        self.daily_selector = ttk.Frame(self.content)
        self.daily_buttons = {}
        for key, title in [('daily', '浏览器页面'), ('workspace_daily', '桌面窗口')]:
            button = ttk.Button(self.daily_selector, text=title, style='Nav.TButton', command=lambda k=key: self.switch(k))
            button.pack(side='left', padx=(0, 10))
            self.daily_buttons[key] = button
        self.daily_hint = ttk.Label(self.content, text='每天一个文件夹 · 父级显示主存档，展开可选其他快照 · 自动最新包含 Edge + Chrome 全部窗口',style='Muted.TLabel')
        ttk.Label(self.filter_bar, text='搜索').pack(side='left')
        self.search_entry = ttk.Entry(self.filter_bar, textvariable=self.search_var, width=34)
        self.search_entry.pack(side='left', padx=10, ipady=4)
        self.search_entry.bind('<Return>', lambda event: self.refresh())
        self.date_label = ttk.Label(self.filter_bar, text='日期')
        self.day_entry = ttk.Entry(self.filter_bar, textvariable=self.day_var, width=13)
        self.search_button = ttk.Button(self.filter_bar, text='搜索', style='Small.TButton', command=self.refresh)
        self.search_button.pack(side='left', padx=8)
        ttk.Button(self.filter_bar, text='清空', style='Quiet.TButton', command=self.clear_search).pack(side='left')
        self.bookmark_button = ttk.Button(self.filter_bar, text='读取 Edge + Chrome 收藏', command=lambda: self.engine.submit('bookmarks'))
        self.dual_frame = ttk.Frame(self.content)
        self.dual_frame.columnconfigure(0, weight=1, uniform='browsers')
        self.dual_frame.columnconfigure(1, weight=1, uniform='browsers')
        self.dual_frame.rowconfigure(0, weight=1)
        self.browser_panes = {}
        for column, browser in enumerate(('Edge', 'Chrome')):
            pane = BrowserPane(self.dual_frame, browser, (self.heading_family, 18),
                               status_var=self.connection_vars[browser],
                               connect=lambda b=browser: self.connect(b),
                               disconnect=lambda b=browser: self.engine.submit('disconnect', browser=b),
                               on_select=self.select_browser, on_open=self.open_tab)
            pane.frame.grid(row=0, column=column, sticky='nsew', padx=(0, 8) if column == 0 else (8, 0))
            self.browser_panes[browser] = pane
        self.tree_frame = ttk.Frame(self.content, style='Rounded.TFrame',padding=12)
        self.tree = ttk.Treeview(self.tree_frame, columns=('browser', 'window', 'title', 'url'), show='headings', selectmode='browse', height=2)
        scroll = ttk.Scrollbar(self.tree_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<Double-1>', lambda e: self.open_selected())
        self.actions = ttk.Frame(self.content)
        self.action_buttons = {}
        for key, title, command in [('open', '查看 / 打开', self.open_selected), ('restore', '恢复这个快照', self.restore),
                                     ('daily_add', '加入每日存档', self.add_daily), ('daily_primary', '设为当天主存档', self.primary_daily),
                                     ('daily_move', '更改日期', self.move_daily),
                                     ('note', '写备注', self.note), ('delete', '删除这条档案', self.delete),
                                     ('expand', '展开所有窗口', lambda: self.expand_windows(True)),
                                     ('collapse', '收起所有窗口', lambda: self.expand_windows(False))]:
            self.action_buttons[key] = ttk.Button(self.actions, text=title, style='Small.TButton', command=command)
        self.count_label = ttk.Label(self.actions, text='', style='Muted.TLabel')
        self.count_label.pack(side='right')
        self.settings_frame = ttk.Frame(self.content)
        self.workspace_pane = WorkspacePane(self.content, self.engine, self.heading_family, self.message_var)
        self.daily_workspace_pane = WorkspacePane(self.content, self.engine, self.heading_family, self.message_var, daily=True)
        footer = ttk.Frame(self.root, padding=(24, 10))
        footer.pack(fill='x')
        self.message_label = ttk.Label(footer, textvariable=self.message_var, style='Muted.TLabel', wraplength=950)
        self.message_label.pack(side='left', fill='x', expand=True)
        ttk.Button(footer, text='隐藏到托盘', style='Quiet.TButton', command=self.hide).pack(side='right')
        self.switch('tabs')

    def clear_search(self):
        self.search_var.set('')
        self.day_var.set('')
        self.refresh()

    def select_browser(self, browser):
        self.active_browser = browser
        for name, pane in self.browser_panes.items():
            if name != browser and pane.tree.selection():
                pane.tree.selection_remove(*pane.tree.selection())

    def expand_windows(self, expanded):
        for pane in self.browser_panes.values():
            pane.expand_all(expanded)

    def open_tab(self, row):
        from storage import safe_url
        if not row.get('url'):
            self.message_var.set('这个标签的网址尚未读取，暂时无法打开或恢复。')
        elif safe_url(row['url']):
            try:
                subprocess.Popen([browser_exe(row.get('browser', 'Chrome')), '--new-tab', row['url']])
            except Exception as error:
                messagebox.showerror('打开失败', str(error), parent=self.root)
        else:
            self.message_var.set('这是本地文件或浏览器内部页面，请在原浏览器中查看。')

    def connect(self, browser):
        if self.demo:
            messagebox.showinfo('演示模式', '演示模式不会连接或读取你实际的浏览器。', parent=self.root)
            return
        self.message_var.set(f'正在连接{browser}，请留意浏览器弹出的允许连接提示…')
        self.engine.submit('connect', browser=browser)

    def guide(self):
        win = tk.Toplevel(self.root)
        win.title('首次连接 · 无需插件')
        win.geometry('680x530')
        win.configure(bg=BG)
        frame = ttk.Frame(win, padding=25)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='让软件连接你正在用的两个浏览器', font=(self.heading_family, 15)).pack(anchor='w', pady=(0, 16))
        text = ('1. 点击主窗口的“连接两个浏览器”。\n'
                '2. 如果还未开放连接，软件尝试打开并核对两边的设置页。\n'
                '3. 在设置页打开“允许对此浏览器实例进行远程调试”。\n'
                '4. 软件自动接上；浏览器询问时点击“允许”。\n\n'
                '看到当前标签列表后，先点“现在保存”。以后到“窗口快照”\n'
                '选择记录，点击“恢复这个快照”即可重新打开这组网页。\n\n'
                '这只是本机连接，不需要安装插件，也不需要关掉现有窗口。\n'
                '浏览器重启后，点击同一个按钮；可能需重新开启开关、允许连接。\n'
                '浏览器显示的授权范围较广；本软件只采集标签页标题、网址与窗口信息，不读取Cookie和页面正文。\n\n'
                '默认只记录普通个人资料；设置中可开启全范围读取（含无痕）。\n'
                '未连接时不会把磁盘上的旧会话冒充当前标签页。')
        ttk.Label(frame, text=text, wraplength=620, justify='left').pack(anchor='w')
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=16)
        for b in ('Chrome', 'Edge'):
            ttk.Button(buttons, text=f'打开{b}连接设置', command=lambda browser=b: self.open_setup(browser)).pack(side='left', padx=(0, 10))

    def open_setup(self, browser):
        self.message_var.set(f'正在打开{browser}连接设置，请稍候…')
        self.engine.submit('open_setup', browser=browser)

    def setup_failed(self, browser, url, reason):
        win = tk.Toplevel(self.root)
        win.title(f'{browser} · 手动打开连接设置')
        win.geometry('640x330')
        win.configure(bg=BG)
        frame = ttk.Frame(win, padding=25)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='请把下面的网址粘贴到浏览器地址栏',
                  font=(self.heading_family, 14)).pack(anchor='w', pady=(0, 16))
        ttk.Label(frame, text=reason, wraplength=580, justify='left').pack(anchor='w', pady=(0, 15))
        value = tk.StringVar(value=url)
        address = ttk.Entry(frame, textvariable=value, state='readonly', width=58)
        address.pack(fill='x')

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self.message_var.set(f'{browser}连接设置网址已复制，请粘贴到浏览器地址栏。')

        ttk.Button(frame, text='复制设置网址', command=copy).pack(anchor='w', pady=12)
        ttk.Label(frame, text='打开后找到“Allow remote debugging for this browser instance”并自行开启。\n'
                  '若仍没有开关，请检查浏览器版本及组织管理策略。',
                  wraplength=580, justify='left').pack(anchor='w')

    def switch(self, view):
        self.view = view
        if view in ('daily', 'workspace_daily'):
            self.daily_view = view
        for key, button in self.nav_buttons.items():
            button.configure(style='SelectedNav.TButton' if key == ('daily' if view == 'workspace_daily' else view) else 'Nav.TButton')
        for widget in (self.filter_bar, self.daily_hint, self.daily_selector, self.dual_frame, self.tree_frame, self.actions, self.settings_frame, self.workspace_pane.frame, self.daily_workspace_pane.frame):
            widget.pack_forget()
        for button in self.action_buttons.values():
            button.pack_forget()
        self.bookmark_button.pack_forget()
        if view in ('daily', 'workspace_daily'):
            self.daily_selector.pack(fill='x', pady=(0, 12))
            for key, button in self.daily_buttons.items():
                button.configure(style='SelectedNav.TButton' if key == view else 'Nav.TButton')
        if view == 'workspace_daily':
            self.daily_workspace_pane.frame.pack(fill='both', expand=True)
            self.daily_workspace_pane.refresh()
            return
        if view == 'workspaces':
            self.workspace_pane.frame.pack(fill='both', expand=True)
            self.workspace_pane.refresh()
            return
        if view == 'settings':
            self.settings_frame.pack(fill='both', expand=True)
            self.build_settings()
            return
        self.date_label.pack_forget()
        self.day_entry.pack_forget()
        if view != 'tabs':
            self.date_label.pack(side='left', padx=(12, 5), before=self.search_button)
            self.day_entry.pack(side='left', before=self.search_button, ipady=4)
        if view == 'bookmarks':
            self.bookmark_button.pack(side='right')
        self.filter_bar.pack(fill='x', pady=(0, 14))
        if view == 'daily':
            self.daily_hint.pack(fill='x',pady=(0,12))
        if view == 'tabs':
            self.dual_frame.pack(fill='both', expand=True)
            keys = ('open', 'expand', 'collapse')
            self.action_buttons['open'].configure(text='打开选中网页')
        else:
            self.tree_frame.pack(fill='both', expand=True)
            keys = ('open', 'restore', 'note', 'delete') if view in ('snapshots','daily') else ('open', 'note', 'delete')
            if view == 'snapshots':
                keys = ('open', 'daily_add', 'restore', 'note', 'delete')
            elif view == 'daily':
                keys = ('open', 'daily_move', 'daily_primary', 'restore', 'note', 'delete')
            self.action_buttons['open'].configure(text='查看选中快照' if view == 'daily' else '查看快照' if view == 'snapshots' else '打开选中网页')
            self.action_buttons['restore'].configure(text='恢复选中网页' if view == 'daily' else '恢复这个快照')
        for key in keys:
            self.action_buttons[key].pack(side='left', padx=(0, 8))
        self.actions.pack(fill='x', pady=(12, 0))
        self.refresh()

    def refresh(self):
        if self.view == 'workspace_daily':
            self.daily_workspace_pane.refresh()
        elif self.view == 'workspaces':
            self.workspace_pane.refresh()
        elif self.view == 'tabs':
            self.show_rows(self.current)
        elif self.view != 'settings':
            self.engine.submit('list', view=self.view, query=self.search_var.get().strip(), day=self.day_var.get().strip())

    def show_rows(self, rows, reset_scroll=False):
        if self.view == 'workspace_daily':
            self.daily_workspace_pane.update(rows)
            return
        if self.view == 'workspaces':
            self.workspace_pane.update(rows)
            return
        if self.view == 'tabs':
            query = self.search_var.get().strip()
            for pane in self.browser_panes.values():
                pane.update(rows, query=query, reset_scroll=reset_scroll)
            displayed = sum(len(pane.rows) for pane in self.browser_panes.values())
            missing = sum(not row.get('url') for row in rows)
            self.count_label.configure(text=f'显示 {displayed} / {len(rows)} 个标签页' + (f' · {missing} 个网址待补全' if missing else ''))
            return
        if self.view == 'daily':
            self.show_daily(rows, reset_scroll)
            return
        self.tree.configure(show='headings')
        same_view = self.rendered_view == self.view
        children = self.tree.get_children()
        position = self.tree.yview()[0]
        horizontal = self.tree.xview()[0]
        top_row = children[min(int(position * len(children)), len(children) - 1)] if children else None
        selection, focus = self.tree.selection(), self.tree.focus()
        if not same_view:
            self.tree.delete(*children)
            reset_scroll = True
        self.rendered_view = self.view
        headings = ('浏览器', '窗口', '标题', '网址')
        if self.view == 'snapshots':
            headings = ('保存时间', '覆盖浏览器', '窗口 / 标签页', '备注')
        elif self.view == 'daily':
            headings = ('日期', '当天最晚保存', 'Edge / Chrome 标签', '窗口 / 备注')
        elif self.view == 'visits':
            headings = ('浏览器', '最近记录', '标题', '网址')
        elif self.view == 'bookmarks':
            headings = ('来源', '文件夹', '标题', '网址')
        for col, title, width in zip(self.tree['columns'], headings, (130, 170, 320, 430)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=80, stretch=col in ('title', 'url'))
        items, self.rows = [], {}
        for i, row in enumerate(rows):
            rid = f"{self.view}:{row.get('id', i)}"
            self.rows[rid] = row
            if self.view == 'daily':
                counts = {b: sum(t['browser'] == b for t in row['tabs']) for b in ('Edge','Chrome')}
                windows = {(t['browser'], t['window']) for t in row['tabs']}
                values = (row['day'], fmt_time(row['time']).split(' ')[1], f"Edge {counts['Edge']}  ·  Chrome {counts['Chrome']}", f"{len(windows)} 个窗口" + (' · ' + row['note'] if row['note'] else ''))
            elif self.view == 'snapshots':
                windows = {(r['browser'], r['window']) for r in row['tabs']}
                values = (fmt_time(row['time']), ' + '.join(row.get('connected', [])), f"{len(windows)}个窗口 · {len(row['tabs'])}个标签页", row['note'])
            elif self.view == 'visits':
                values = (row['browser'], fmt_time(row['time']), row['title'], row['url'])
            elif self.view == 'bookmarks':
                values = (' / '.join(row['sources']), ' / '.join(row['paths']), row['title'], row['url'])
            else:
                values = (row.get('browser', ''), row.get('window', ''), row.get('title', ''), row.get('url', ''))
            items.append((rid, values))
        wanted = tuple(rid for rid, values in items)
        for rid in self.tree.get_children():
            if rid not in self.rows:
                self.tree.delete(rid)
        existing = self.tree.get_children()
        for index, (rid, values) in enumerate(items):
            if self.tree.exists(rid):
                if tuple(self.tree.item(rid, 'values')) != tuple(str(value) for value in values):
                    self.tree.item(rid, values=values)
            else:
                self.tree.insert('', 'end', iid=rid, values=values)
            if index >= len(existing) or existing[index] != rid:
                self.tree.move(rid, '', index)
        retained = tuple(rid for rid in selection if rid in self.rows)
        self.tree.selection_set(retained)
        if focus in self.rows:
            self.tree.focus(focus)
        if reset_scroll:
            self.tree.yview_moveto(0)
        elif same_view:
            self.tree.yview_moveto(wanted.index(top_row) / len(wanted) if top_row in wanted else position)
            self.tree.xview_moveto(horizontal)
        self.count_label.configure(text=f'{len(rows)}条' + (' · 搜索可缩小范围（最多显示500条）' if self.view != 'tabs' else ' · 可按窗口查看'))

    def show_daily(self, rows, reset_scroll=False):
        same = self.rendered_view == 'daily'
        selected, focus, position = self.tree.selection(), self.tree.focus(), self.tree.yview()[0]
        if not same:
            self.tree.delete(*self.tree.get_children())
        self.rendered_view = 'daily'
        self.tree.configure(show='tree headings')
        self.tree.heading('#0', text='日期 / 保存的快照')
        self.tree.column('#0', width=200, minwidth=180, stretch=False)
        for col, title, width in zip(self.tree['columns'], ('快照保存时间','Edge / Chrome 标签','保存方式 / 窗口','备注'), (190,200,180,260)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=80, stretch=col=='url')
        wanted, self.rows = set(), {}
        def update(parent, iid, label, row, index):
            wanted.add(iid);self.rows[iid] = row
            tabs = row['tabs']
            counts = {b: sum(t['browser']==b for t in tabs) for b in ('Edge','Chrome')}
            windows = len({(t['browser'],t['window']) for t in tabs})
            missing = sum(not t.get('url') for t in tabs)
            mode = ('主存档 · 已固定' if row.get('primary_locked') else '主存档 · 自动最新') if row.get('is_day') else ('自动最新' if row.get('origin')=='auto' else '保留的快照')
            note = (f'{missing} 个网址未读取 · ' if missing else '') + row.get('note','')
            values = (fmt_time(row['time']), f"Edge {counts['Edge']} · Chrome {counts['Chrome']}", f'{mode} · {windows} 窗口', note)
            if self.tree.exists(iid):
                self.tree.item(iid, text=label, values=values)
                self.tree.move(iid,parent,index)
            else:
                self.tree.insert(parent,index,iid=iid,text=label,values=values)
        for index, row in enumerate(rows):
            root = 'day:'+row['day']
            update('',root,row['day'],row,index)
            for child_index, child in enumerate(row.get('children', [])):
                update(root,'daily-entry:'+str(child['entry_id']), '其他快照 '+str(child_index+1),child,child_index)
        for root in self.tree.get_children():
            if root not in wanted:
                self.tree.delete(root)
            else:
                for child in self.tree.get_children(root):
                    if child not in wanted:self.tree.delete(child)
        retained = [iid for iid in selected if iid in wanted]
        pending = getattr(self,'pending_daily_selection',None)
        if pending in wanted:
            retained=[pending];self.pending_daily_selection=None
        self.tree.selection_set(retained)
        if focus in wanted:self.tree.focus(focus)
        if reset_scroll or not same:self.tree.yview_moveto(0)
        else:self.tree.yview_moveto(position)
        if pending in wanted:self.tree.see(pending)
        self.count_label.configure(text=f'{len(rows)} 天')

    def add_daily(self):
        row = self.selected()
        if self.view != 'snapshots' or not row:
            self.message_var.set('请先选中一条窗口快照。')
            return
        win = tk.Toplevel(self.root)
        win.title('加入每日存档');win.geometry('600x360');win.resizable(False,False);win.configure(bg=BG)
        win.transient(self.root)
        frame = ttk.Frame(win,padding=24);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='选择日期与保存方式',font=(self.heading_family,16)).pack(anchor='w')
        ttk.Label(frame,text='原快照保存于 '+fmt_time(row['time']),style='Muted.TLabel').pack(anchor='w',pady=(8,12))
        line=ttk.Frame(frame);line.pack(fill='x')
        ttk.Label(line,text='存档日期').pack(side='left')
        day=tk.StringVar(value=row['day']);mode=tk.StringVar(value='append')
        ttk.Entry(line,textvariable=day,width=16).pack(side='left',padx=14)
        ttk.Label(line,text='YYYY-MM-DD',style='Muted.TLabel').pack(side='left')
        ttk.Radiobutton(frame,text='保留为另一份快照（首次加入会成为主存档）',variable=mode,value='append').pack(anchor='w',pady=(18,8))
        ttk.Radiobutton(frame,text='替换当天主存档（原来的保留在子项中）',variable=mode,value='replace').pack(anchor='w')
        missing=sum(not t.get('url') for t in row['tabs'])
        info=f'{missing} 个网址在原记录中未读取，将原样保留。' if missing else '保留这份快照的原始窗口、标签页和网址。'
        ttk.Label(frame,text=info,style='Muted.TLabel').pack(anchor='w',pady=16)
        def save():
            try:
                from storage import Archive
                Archive._valid_day(day.get().strip())
            except ValueError:
                messagebox.showerror('日期格式','请输入 YYYY-MM-DD 格式的日期。',parent=win);return
            self.engine.submit('daily_add',id=row['id'],day=day.get().strip(),mode=mode.get())
            win.destroy()
        win.save_daily=save;win.day_var=day;win.mode_var=mode
        bar=ttk.Frame(frame);bar.pack(fill='x')
        ttk.Button(bar,text='保存到每日存档',style='Accent.TButton',command=save).pack(side='left')
        ttk.Button(bar,text='取消',command=win.destroy).pack(side='left',padx=10)

    def primary_daily(self):
        row=self.selected()
        if self.view=='daily' and row:
            self.engine.submit('daily_primary',entry_id=row['entry_id'])

    def move_daily(self):
        from archive_date import ask_archive_date
        row = self.selected()
        if self.view != 'daily' or not row:
            self.message_var.set('请先选中一份每日存档。')
            return
        day = ask_archive_date(self.root, row['day'], desktop=False)
        if day is not None and day != row['day']:
            self.engine.submit('daily_move', view='daily', id=row['entry_id'], day=day)

    def selected(self):
        if self.view == 'tabs':
            return self.browser_panes[self.active_browser].selected() if self.active_browser else None
        selected = self.tree.selection()
        return self.rows.get(selected[0]) if selected else None

    def open_selected(self):
        row = self.selected()
        if not row:
            return
        if self.view in ('snapshots','daily'):
            win = tk.Toplevel(self.root)
            win.title(('每日存档 · ' if self.view == 'daily' else '窗口快照 · ') + fmt_time(row['time']))
            win.geometry('1180x650')
            win.minsize(1020, 500)
            win.configure(bg=BG)
            apply_window_effects(win)
            ttk.Label(win, text='保存于 ' + fmt_time(row['time']) + ' · 点击窗口展开，双击网页打开，右键窗口恢复全部标签页',
                      style='Muted.TLabel').pack(anchor='w', padx=20, pady=15)
            body = ttk.Frame(win, padding=(20, 0, 20, 15))
            body.pack(fill='both', expand=True)
            body.columnconfigure(0, weight=1, uniform='snapshots')
            body.columnconfigure(1, weight=1, uniform='snapshots')
            body.rowconfigure(0, weight=1)
            win.browser_panes = {}
            for column, browser in enumerate(('Edge', 'Chrome')):
                pane = BrowserPane(body, browser, (self.heading_family, 18), on_open=self.open_tab,
                                   on_restore_window=lambda b, w, snapshot=row, view=self.view:
                                       self.restore_browser_window(snapshot, view, b, w))
                pane.frame.grid(row=0, column=column, sticky='nsew', padx=(0, 7) if column == 0 else (7, 0))
                pane.update(row['tabs'])
                win.browser_panes[browser] = pane
        else:
            self.open_tab(row)

    def restore_browser_window(self, snapshot, view, browser, window):
        self.engine.submit('restore', id=snapshot['id'], view=view, entry_id=snapshot.get('entry_id'),
                           browser=browser, window=window)

    def restore(self):
        row = self.selected()
        if self.view not in ('snapshots','daily') or not row:
            messagebox.showinfo('选择快照', '请到“每日存档”或“窗口快照”选择一条记录。', parent=self.root)
            return
        if messagebox.askyesno('恢复快照', f"将向原浏览器的新窗口恢复这组网页（共{len(row['tabs'])}个标签页，内部页面会跳过），现有窗口会保留。继续？", parent=self.root):
            self.engine.submit('restore', id=row['id'], view=self.view, entry_id=row.get('entry_id'))

    def note(self):
        row = self.selected()
        if self.view == 'tabs' or not row:
            return
        text = simpledialog.askstring('备注', '为这条档案写点说明：', initialvalue=row.get('note', ''), parent=self.root)
        if text is not None:
            self.engine.submit('note', view='daily_entry' if self.view=='daily' else self.view, id=row.get('entry_id',row['id']), text=text)

    def delete(self):
        row = self.selected()
        if self.view == 'tabs' or not row:
            return
        whole_day=self.view=='daily' and row.get('is_day')
        text='删除这一天的整个存档文件夹（含所有子快照）？' if whole_day else '删除这条保存的档案？'
        if messagebox.askyesno('删除档案', text+'不会关闭浏览器标签页或删除原书签。', parent=self.root):
            child=self.view=='daily' and not whole_day
            self.engine.submit('delete', view='daily_entry' if child else self.view, id=row['entry_id'] if child else row['id'])

    def build_settings(self):
        for widget in self.settings_frame.winfo_children():
            widget.destroy()
        s = self.settings
        self.form_vars = {key: tk.StringVar(value=str(s.get(key, default))) for key, default in [('interval', 60), ('retention', 90), ('limit_mb', 100), ('workspace_limit_mb', 20), ('chrome_root', ''), ('edge_root', '')]}
        self.enabled_var = tk.BooleanVar(value=s.get('enabled', True))
        self.startup_var = tk.BooleanVar(value=s.get('startup', False))
        self.all_profiles_var = tk.BooleanVar(value=s.get('include_all_profiles', False))
        self.hd_var = tk.BooleanVar(value=s.get('workspace_hd', False))
        self.hd_edge_var = tk.StringVar(value=str(s.get('workspace_hd_edge', 1920)))
        self.thumb_edge_var = tk.StringVar(value=str(s.get('workspace_thumb_edge', 640)))
        self.image_mode_var = tk.StringVar(value='无损清晰（适合文字）' if s.get('workspace_image_mode', 'lossless') == 'lossless' else '节省空间（轻微有损）')
        modes = {'auto': '自动：记录量满后只保留快照', 'all': '全部网页：满后清理最早记录', 'snapshots': '仅保存打开的标签页快照'}
        self.mode_var = tk.StringVar(value=modes.get(s.get('mode', 'auto')))
        frame = ttk.Frame(self.settings_frame)
        frame.pack(fill='x')
        ttk.Checkbutton(frame, text='启用浏览器自动保存与浏览记录', variable=self.enabled_var).grid(row=0, column=0, columnspan=2, sticky='w', pady=8)
        labels = [('interval', '保存间隔（秒，10—3600）'), ('retention', '保留天数（1—3650）'), ('limit_mb', '档案容量上限（MB，10—1000）'), ('chrome_root', 'Chrome数据目录'), ('edge_root', 'Edge数据目录')]
        for index, (key, label) in enumerate(labels, 1):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky='w', pady=8)
            ttk.Entry(frame, textvariable=self.form_vars[key], width=36 if key.endswith('root') else 25).grid(row=index, column=1, sticky='w', padx=18)
        ttk.Label(frame, text='记录方式').grid(row=6, column=0, sticky='w', pady=8)
        ttk.Combobox(frame, textvariable=self.mode_var, values=list(modes.values()), state='readonly', width=32).grid(row=6, column=1, sticky='w', padx=18)
        ttk.Checkbutton(frame, text='开机启动软件并隐藏到托盘', variable=self.startup_var).grid(row=7, column=0, columnspan=2, sticky='w', pady=8)
        ttk.Checkbutton(frame, text='读取其他个人资料与无痕窗口（需重新连接）', variable=self.all_profiles_var).grid(row=8, column=0, columnspan=2, sticky='w', pady=8)
        ttk.Label(frame, text='桌面窗口独立容量（MB，5—2000）').grid(row=9, column=0, sticky='w', pady=8)
        ttk.Entry(frame, textvariable=self.form_vars['workspace_limit_mb'], width=25).grid(row=9, column=1, sticky='w', padx=18)
        images = ttk.Frame(frame, padding=(25, 0, 0, 0))
        images.grid(row=0, column=2, rowspan=10, sticky='nw')
        ttk.Checkbutton(images, text='同时保存高清画面（保留缩略图）', variable=self.hd_var).grid(row=0, column=0, columnspan=2, sticky='w', pady=8)
        for row, label, var, options in [(1, '高清长边上限', self.hd_edge_var, ('1280', '1920', '2560', '3840')),
                                        (2, '高清压缩方式', self.image_mode_var, ('无损清晰（适合文字）', '节省空间（轻微有损）')),
                                        (3, '缩略图长边', self.thumb_edge_var, ('320', '480', '640'))]:
            ttk.Label(images, text=label).grid(row=row, column=0, sticky='w', pady=10)
            ttk.Combobox(images, textvariable=var, values=options, state='readonly', width=24).grid(row=row, column=1, sticky='w', padx=10)
        ttk.Label(images, text='双击窗口卡片查看高清图，可切换原始比例。\n不放大小图；历史缩略图不会自动变高清。\n自动保存不展开窗口；最小化画面不可读时\n保留上次画面，显示原截图时间。\n手动拍摄可保存最小化窗口的高清画面。', style='Muted.TLabel', wraplength=370).grid(row=4, column=0, columnspan=2, sticky='w', pady=16)
        ttk.Label(self.settings_frame, text='图片按内容去重，快照无损压缩。高清默认关闭；开启后仍受桌面窗口容量限制。调小容量会清理最早存档。\n保存任务视图中的窗口（可含最小化窗口），画面用于回顾；未保存文档及运行中的终端任务不能靠截图恢复。', style='Muted.TLabel', wraplength=1060).pack(anchor='w', pady=12)
        bar = ttk.Frame(self.settings_frame)
        bar.pack(fill='x')
        ttk.Button(bar, text='保存设置', style='Accent.TButton', command=lambda: self.save_settings(modes)).pack(side='left')
        ttk.Button(bar, text='导出完整备份', command=self.export).pack(side='left', padx=10)
        ttk.Button(bar, text='导入备份', command=self.import_backup).pack(side='left', padx=(0, 10))
        ttk.Button(bar, text='打开数据文件夹', command=lambda: os.startfile(str(self.folder))).pack(side='left')

    def save_settings(self, modes):
        try:
            values = {k: int(v.get()) if k in ('interval', 'retention', 'limit_mb', 'workspace_limit_mb') else v.get().strip() for k, v in self.form_vars.items()}
            values.update(enabled=self.enabled_var.get(), startup=self.startup_var.get(), include_all_profiles=self.all_profiles_var.get(), mode=next(k for k, v in modes.items() if v == self.mode_var.get()))
            values.update(workspace_hd=self.hd_var.get(), workspace_hd_edge=int(self.hd_edge_var.get()),
                          workspace_thumb_edge=int(self.thumb_edge_var.get()),
                          workspace_image_mode='lossless' if self.image_mode_var.get().startswith('无损') else 'compact')
            if (values['retention'] < self.settings.get('retention', 90) or values['limit_mb'] < self.settings.get('limit_mb', 100)
                    or values['workspace_limit_mb'] < self.settings.get('workspace_limit_mb', 20)):
                if not messagebox.askyesno('清理超限记录', '调小上限会立即清理超限的旧记录。确认已备份并继续？', parent=self.root):
                    return
            set_startup(values['startup'])
            self.engine.submit('settings', value=values)
        except Exception as e:
            messagebox.showerror('设置未保存', str(e), parent=self.root)

    def export(self):
        path = filedialog.asksaveasfilename(parent=self.root, title='保存完整档案备份', initialfile=time.strftime('AE-Keeper-备份-%Y-%m-%d.json'), defaultextension='.json', filetypes=[('JSON备份', '*.json')])
        if path:
            self.engine.submit('export', path=path)

    def import_backup(self):
        path = filedialog.askopenfilename(parent=self.root, title='导入桌面版档案备份', filetypes=[('JSON备份', '*.json')])
        if path:
            self.engine.submit('import', path=path)

    def poll(self):
        try:
            while True:
                event = self.output.get_nowait()
                kind = event['kind']
                if kind == 'state':
                    self.settings, self.current, self.stats = event['settings'], event['current'], event['stats']
                    self.workspace_pane.enabled.set(self.settings.get('workspace_enabled', True))
                    self.workspace_pane.include_minimized.set(self.settings.get('workspace_include_minimized', True))
                    self.workspace_pane.previews_enabled.set(self.settings.get('workspace_previews', True))
                    scope = '包含任务栏中的最小化窗口' if self.settings.get('workspace_include_minimized', True) else '排除最小化窗口'
                    self.workspace_pane.hint.configure(text=f"{scope} · 每 {self.settings['interval']} 秒更新当天一份 · 最小化时沿用上次可读预览，手动拍摄可更新全部画面")
                    for browser in self.connection_vars:
                        status = event['status'][browser]
                        if status.startswith('已连接'):
                            status = '已连接 · ' + (f"每 {self.settings['interval']} 秒自动保存" if self.settings['enabled'] else '自动保存已关闭')
                        self.connection_vars[browser].set(status)
                    total = sum(v['bytes'] for v in self.stats.values()) / 1048576
                    size = f'{total:.2f}MB' if total >= 1 else f'{total * 1024:.0f}KB'
                    latest = max(event['last_saved'] or 0, event.get('last_workspace_saved') or 0)
                    self.summary.configure(text=f"最近保存：{fmt_time(latest)}   |   网页存档 {self.stats.get('daily_archives',{}).get('count',0)} 天   |   桌面存档 {self.stats.get('workspaces',{}).get('days',0)} 天   |   档案 {size}")
                    if event['fallback']:
                        self.message_var.set('浏览记录容量已满，继续保存窗口快照。可提高容量上限。')
                    if self.view == 'tabs':
                        self.show_rows(self.current)
                    elif self.view == 'daily' and getattr(self,'daily_refresh_time',None) != event['last_saved']:
                        self.daily_refresh_time = event['last_saved']
                        self.refresh()
                    elif self.view == 'workspace_daily' and getattr(self,'desktop_daily_refresh_time',None) != event.get('last_workspace_saved'):
                        self.desktop_daily_refresh_time = event.get('last_workspace_saved')
                        self.refresh()
                elif kind == 'list' and event['view'] == self.view:
                    self.show_rows(event['rows'])
                    if self.view == 'workspaces' and self.preview_new_workspace and self.preview_workspace_ready and event['rows']:
                        self.preview_new_workspace = False
                        self.workspace_pane.tree.selection_set(str(event['rows'][0]['id']))
                        self.workspace_pane.preview()
                elif kind == 'workspace_saved' and self.view in ('workspaces', 'workspace_daily'):
                    if event.get('manual'):
                        self.preview_workspace_ready = True
                    self.refresh()
                elif kind == 'workspace_restored':
                    self.message_var.set(f"已请求恢复 {event['opened']} 组工作窗口。")
                    if event['messages']:
                        messagebox.showinfo('桌面窗口恢复结果', '\n'.join(event['messages'])[:12000], parent=self.root)
                elif kind == 'archive_moved':
                    self.archive_moved(event)
                elif kind == 'daily_changed':
                    self.pending_daily_selection='day:'+event['day']
                    if not event.get('primary'):
                        self.search_var.set('');self.day_var.set('');self.switch('daily')
                    else:self.refresh()
                elif kind == 'message':
                    self.message_var.set(event['text'])
                    self.refresh()
                elif kind == 'setup_failed':
                    self.message_var.set(f"{event['browser']}连接设置未自动打开，请按提示操作。")
                    self.setup_failed(event['browser'], event['url'], event['text'])
                elif kind == 'error':
                    self.message_var.set('操作未完成：' + event['text'])
                    messagebox.showerror('操作未完成', event['text'], parent=self.root)
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

    def archive_moved(self, event):
        # Locate the result by selection, never by silently hiding other days.
        if event['view'] == 'workspace_daily':
            self.daily_workspace_pane.query.set('')
            self.daily_workspace_pane.day.set('')
            self.daily_workspace_pane.pending_day = event['day']
        else:
            self.search_var.set('')
            self.day_var.set('')
            self.pending_daily_selection = 'day:' + event['day']
        if self.view == event['view']:
            self.refresh()

    def make_tray(self):
        try:
            import pystray
            from PIL import Image
            image = logo_image().resize((64,64), Image.Resampling.LANCZOS)
            self.tray = pystray.Icon('BrowseTraceDesktop', image, APP_NAME,
                pystray.Menu(pystray.MenuItem('打开 AE Keeper', lambda: self.root.after(0, self.show), default=True),
                             pystray.MenuItem('现在保存', lambda: self.engine.submit('save')),
                             pystray.MenuItem('退出软件', lambda: self.root.after(0, self.quit))))
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception:
            self.tray = None

    def hide(self):
        if self.tray:
            self.root.withdraw()
        elif messagebox.askyesno('退出软件', '托盘不可用。退出后将停止自动保存，继续退出？', parent=self.root):
            self.quit()

    def show(self):
        self.root.deiconify()
        self.root.lift()

    def quit(self):
        self.engine.stop_event.set()
        self.engine.join(timeout=2)
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def installer():
    win = tk.Tk()
    win.title('安装 AE Keeper')
    apply_icon(win)
    win.geometry('600x340')
    win.configure(bg=BG)
    heading_family = load_heading_font(win)
    style = ttk.Style(win)
    style.theme_use('clam')
    style.configure('.',font=(heading_family,11))
    style.configure('TFrame',background=BG)
    style.configure('TLabel',background=BG)
    for name in tkfont.names(win):
        tkfont.nametofont(name).configure(family=heading_family)
    install_rounded_theme(win,style)
    apply_window_effects(win)
    frame = ttk.Frame(win, padding=28)
    frame.pack(fill='both', expand=True)
    brand=ttk.Frame(frame);brand.pack(fill='x',pady=(0,12))
    win.brand_logo=logo_photo(win,56)
    ttk.Label(brand,image=win.brand_logo).pack(side='left',padx=(0,16))
    ttk.Label(brand, text=NAME+' · 网页留痕', font=(heading_family, 24)).pack(side='left')
    ttk.Label(frame, text='一个软件，同时管理 Edge 与 Chrome 的标签页。\n不安装浏览器插件。首次使用需在浏览器中允许本机连接。', wraplength=520).pack(anchor='w', pady=12)
    destination = Path(os.environ['LOCALAPPDATA']) / 'BrowseTraceDesktop'
    ttk.Label(frame, text=f'安装到：{destination}\n档案保存在此目录下的data文件夹。安装不需要管理员权限。', wraplength=520).pack(anchor='w', pady=12)
    def install():
        try:
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / 'BrowseTrace.exe'
            shutil.copy2(sys.executable, target)
            font_license = Path(__file__).parent / 'assets/SmileySans-LICENSE.txt'
            if font_license.is_file():
                shutil.copy2(font_license, destination / '得意黑字体许可.txt')
            shutil.copy2(Path(__file__).parent / 'assets/ae-keeper.ico', destination / 'ae-keeper-sharp.ico')
            # Get the real Windows desktop, including redirected/OneDrive desktops.
            target_literal = str(target).replace("'", "''")
            code = f"""$ErrorActionPreference='Stop'
$taskDesktop=[Environment]::GetFolderPath('Desktop')
$taskShell=New-Object -ComObject WScript.Shell
$taskLink=Join-Path $taskDesktop 'AE Keeper.lnk'
if (Test-Path -LiteralPath $taskLink) {{
    $taskExisting=$taskShell.CreateShortcut($taskLink)
    if ($taskExisting.TargetPath -ne '{target_literal}') {{ throw 'AE Keeper shortcut already points to another application.' }}
}}
$taskShortcut=$taskShell.CreateShortcut($taskLink)
$taskShortcut.TargetPath='{target_literal}'
$taskShortcut.WorkingDirectory='{str(destination).replace(chr(39), chr(39)*2)}'
$taskShortcut.IconLocation='{str(destination / 'ae-keeper-sharp.ico').replace(chr(39),chr(39)*2)},0'
$taskShortcut.Description='AE Keeper · 网页留痕'
$taskShortcut.Save()
$taskOld=Join-Path $taskDesktop '浏览留痕.lnk'
if (Test-Path -LiteralPath $taskOld) {{
    if ($taskShell.CreateShortcut($taskOld).TargetPath -eq '{target_literal}') {{ Remove-Item -LiteralPath $taskOld }}
}}
"""
            encoded = base64.b64encode(code.encode('utf-16le')).decode()
            subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-EncodedCommand', encoded], creationflags=0x08000000, check=True)
            subprocess.Popen([str(target)])
            win.destroy()
        except Exception as e:
            messagebox.showerror('安装未完成', '请先退出已运行的 AE Keeper，再重试。\n' + str(e), parent=win)
    ttk.Button(frame, text='安装并打开', command=install).pack(side='left', pady=15)
    ttk.Button(frame, text='暂不安装，直接试用', command=lambda: (win.destroy(), main(force_app=True))).pack(side='left', padx=12, pady=15)
    win.mainloop()


def main(force_app=False):
    import multiprocessing
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--tray', action='store_true')
    parser.add_argument('--workspaces', action='store_true', help='打开桌面窗口栏目')
    parser.add_argument('--capture-workspace', action='store_true', help='拍摄并预览一份当前桌面窗口，包含最小化窗口')
    parser.add_argument('--data-dir')
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    if os.name == 'nt':
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('AE.Tools.Keeper')
    if not force_app and getattr(sys, 'frozen', False) and (args.install or '安装' in Path(sys.executable).stem):
        installer()
        return
    folder = Path(args.data_dir) if args.data_dir else Path(os.environ['LOCALAPPDATA']) / 'BrowseTraceDesktop/data'
    if os.name == 'nt':
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        key = hashlib.sha256(str(folder.resolve()).encode()).hexdigest()[:20]
        ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
        ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\BrowseTraceDesktop-' + key)
        if ctypes.windll.kernel32.GetLastError() == 183:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(APP_NAME, '软件已经在运行，请从任务栏右下角的托盘图标打开。')
            root.destroy()
            return
    App(folder, args.demo, args.tray, initial_view='workspaces' if args.workspaces or args.capture_workspace else 'tabs',
        capture_workspace=args.capture_workspace).run()


if __name__ == '__main__':
    main()
