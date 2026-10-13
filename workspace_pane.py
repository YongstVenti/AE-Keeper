"""Desktop archive list and an explicit, selectable restore preview."""
import time
import tkinter as tk
from tkinter import ttk

from branding import BG, apply_icon
from desktop_windows import restorable
from window_effects import apply_window_effects
from workspace_model import workspace_counts

LABELS = {'explorer': '资源管理器', 'terminal': 'Windows 终端', 'console': '控制台',
          'app': '软件', 'browser': '浏览器'}


def time_text(stamp):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stamp))


class WorkspacePane:
    def __init__(self, parent, engine, font_family, message):
        self.engine, self.font_family, self.message = engine, font_family, message
        self.frame = ttk.Frame(parent)
        self.rows = {}
        self.enabled = tk.BooleanVar(value=True)
        bar = ttk.Frame(self.frame)
        bar.pack(fill='x', pady=(0, 10))
        ttk.Label(bar, text='工作现场', font=(font_family, 18)).pack(side='left')
        ttk.Button(bar, text='保留现在的工作现场', style='Accent.TButton',
                   command=lambda: engine.submit('workspace_save')).pack(side='right')
        ttk.Checkbutton(bar, text='自动保存当天最新', variable=self.enabled,
                        command=lambda: engine.submit('workspace_enabled', enabled=self.enabled.get())).pack(side='right', padx=18)
        self.hint = ttk.Label(self.frame, style='Muted.TLabel', wraplength=1120,
                             text='只记录打开且未最小化的窗口；被其他窗口遮住也会保存。每天一份自动最新，手动保留的不会覆盖。')
        self.hint.pack(anchor='w', pady=(0, 10))
        filters = ttk.Frame(self.frame); filters.pack(fill='x', pady=(0, 10))
        self.query, self.day = tk.StringVar(), tk.StringVar()
        ttk.Label(filters, text='搜索').pack(side='left')
        entry = ttk.Entry(filters, textvariable=self.query, width=30)
        entry.pack(side='left', padx=10); entry.bind('<Return>', lambda event: self.refresh())
        ttk.Label(filters, text='日期').pack(side='left')
        ttk.Entry(filters, textvariable=self.day, width=13).pack(side='left', padx=10)
        ttk.Button(filters, text='查找存档', command=self.refresh).pack(side='left')
        ttk.Button(filters, text='清空筛选', style='Quiet.TButton', command=self.clear).pack(side='left', padx=8)
        body = ttk.Frame(self.frame, style='Rounded.TFrame', padding=12); body.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(body, columns=('time', 'mode', 'counts', 'note'), show='headings', selectmode='browse')
        for col, title, width in [('time', '保存时间', 210), ('mode', '保存方式', 140),
                                  ('counts', '窗口 / 文件夹标签 / 终端会话', 420), ('note', '备注', 280)]:
            self.tree.heading(col, text=title); self.tree.column(col, width=width, minwidth=100, stretch=col in ('counts', 'note'))
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')
        self.tree.bind('<Double-1>', lambda event: self.preview())
        actions = ttk.Frame(self.frame); actions.pack(fill='x', pady=(12, 0))
        ttk.Button(actions, text='查看并选择恢复', command=self.preview).pack(side='left')
        ttk.Button(actions, text='写备注', command=self.note).pack(side='left', padx=10)
        ttk.Button(actions, text='删除这份存档', style='Quiet.TButton', command=self.delete).pack(side='left')
        self.count = ttk.Label(actions, text='', style='Muted.TLabel'); self.count.pack(side='right')
        ttk.Label(self.frame, text='恢复的是文件夹、进程目录和软件入口。终端命令、运行中的任务、软件未保存内容请在原软件中保存。',
                  style='Muted.TLabel', wraplength=1120).pack(anchor='w', pady=(12, 0))

    def clear(self):
        self.query.set(''); self.day.set(''); self.refresh()

    def refresh(self):
        self.engine.submit('list', view='workspaces', query=self.query.get().strip(), day=self.day.get().strip())

    def update(self, rows):
        selection, position = self.tree.selection(), self.tree.yview()[0]
        self.rows = {str(r['id']): r for r in rows}
        for iid in self.tree.get_children():
            if iid not in self.rows:
                self.tree.delete(iid)
        for index, row in enumerate(rows):
            iid = str(row['id']); n, tabs, sessions = workspace_counts(row['payload'])
            values = (time_text(row['time']), {'auto': '当天自动最新', 'previous': '上次运行保留', 'manual': '手动保留'}[row['origin']],
                      f'{n} 个窗口 · {tabs} 个文件夹标签 · {sessions} 个终端会话', row['note'])
            if self.tree.exists(iid):
                self.tree.item(iid, values=values)
            else:
                self.tree.insert('', 'end', iid=iid, values=values)
            self.tree.move(iid, '', index)
        self.tree.selection_set([iid for iid in selection if iid in self.rows])
        self.tree.yview_moveto(position)
        self.count.configure(text=f'{len(rows)} 份存档')

    def selected(self):
        selection = self.tree.selection()
        return self.rows.get(selection[0]) if selection else None

    def preview(self):
        row = self.selected()
        if not row:
            self.message.set('请先选中一份工作现场。'); return
        win = tk.Toplevel(self.frame); win.title('工作现场 · ' + time_text(row['time']))
        win.geometry('1150x680'); win.minsize(980, 550); win.configure(bg=BG)
        apply_icon(win); apply_window_effects(win)
        body = ttk.Frame(win, padding=20); body.pack(fill='both', expand=True)
        ttk.Label(body, text='选择要恢复的工作窗口', font=(self.font_family, 18)).pack(anchor='w')
        ttk.Label(body, text='点击窗口前的方框选择；展开查看文件夹和终端目录。默认选择资源管理器及终端，其他软件可手动勾选。',
                  style='Muted.TLabel', wraplength=1070).pack(anchor='w', pady=12)
        table = ttk.Frame(body); table.pack(fill='both', expand=True)
        tree = ttk.Treeview(table, columns=('type', 'path', 'detail'), show='tree headings', selectmode='browse')
        tree.heading('#0', text='选择 / 窗口与标签'); tree.column('#0', width=240, minwidth=180)
        for col, label, width in [('type', '类型', 130), ('path', '路径 / 工作目录', 330), ('detail', '能恢复什么', 390)]:
            tree.heading(col, text=label); tree.column(col, width=width, minwidth=90)
        scroll = ttk.Scrollbar(table, orient='vertical', command=tree.yview); tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')
        selected, available = set(), set()
        for index, window in enumerate(row['payload']['windows']):
            allowed = restorable(window) or self.engine.demo and window['kind'] != 'browser'
            if allowed:
                available.add(index)
                if window['kind'] in ('explorer', 'terminal', 'console'):
                    selected.add(index)
            mark = '☑' if index in selected else '☐' if allowed else '—'
            tree.insert('', 'end', iid=str(index), text=mark + '  ' + (window['title'] or window['app']),
                        values=(LABELS[window['kind']], window['exe'] if window['kind'] == 'app' else '', window['detail']), open=False)
            for number, tab in enumerate(window.get('tabs', [])):
                tree.insert(str(index), 'end', iid=f'{index}:folder:{number}', text=tab['title'] or tab['path'],
                            values=('文件夹标签', tab['path'], '目录不存在时跳过'))
            for number, session in enumerate(window.get('sessions', [])):
                tree.insert(str(index), 'end', iid=f'{index}:shell:{number}', text=session['title'],
                            values=('终端会话', session['cwd'], '进程目录，可能与 shell 的 pwd 不同'))
        def toggle(event):
            iid = tree.identify_row(event.y)
            if ':' in iid or not iid or tree.identify_column(event.x) != '#0':
                return
            if tree.identify('element', event.x, event.y).endswith('indicator'):
                return
            index = int(iid)
            if index not in available:
                return
            if index in selected:
                selected.remove(index)
            else:
                selected.add(index)
            window = row['payload']['windows'][index]
            tree.item(iid, text=('☑' if index in selected else '☐') + '  ' + (window['title'] or window['app']))
        tree.bind('<Button-1>', toggle)
        ttk.Label(body, text='浏览器的网页请从“每日存档”恢复。现有窗口会保留；不会自动运行存档时的命令。',
                  style='Muted.TLabel', wraplength=1070).pack(anchor='w', pady=10)
        bar = ttk.Frame(body); bar.pack(fill='x')
        def restore():
            if not selected:
                self.message.set('尚未选择能恢复的窗口。'); return
            self.engine.submit('workspace_restore', id=row['id'], selected=sorted(selected))
            win.destroy()
        ttk.Button(bar, text='恢复选中的工作窗口', style='Accent.TButton', command=restore).pack(side='right')
        ttk.Button(bar, text='关闭', command=win.destroy).pack(side='right', padx=10)
        win.workspace_tree, win.workspace_selected = tree, selected
        self.preview_window = win

    def note(self):
        from tkinter import simpledialog
        row = self.selected()
        if row:
            text = simpledialog.askstring('工作现场备注', '记录这时在做什么：', initialvalue=row['note'], parent=self.frame)
            if text is not None:
                self.engine.submit('note', view='workspaces', id=row['id'], text=text)
                self.refresh()

    def delete(self):
        from tkinter import messagebox
        row = self.selected()
        if row and messagebox.askyesno('删除工作现场', '删除这份存档？原文件和打开的工作窗口会保留。', parent=self.frame):
            self.engine.submit('delete', view='workspaces', id=row['id']); self.refresh()
