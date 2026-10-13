"""Scrollable saved-window cards; photos come only from the selected archive."""
import base64
from io import BytesIO
import time
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageTk

from branding import BG, WHITE, MUTED, INK, apply_icon
from window_effects import apply_window_effects

LABELS = {'explorer': '资源管理器', 'terminal': 'Windows 终端', 'console': '控制台',
          'app': '软件', 'browser': '浏览器'}


def preview_time(stamp):
    return time.strftime('%m-%d %H:%M:%S', time.localtime(stamp)) if stamp > 1 else '时间未记录'


class WorkspaceGallery:
    def __init__(self, parent, windows, selected, available, family, changed, image_loader=None):
        self.frame = ttk.Frame(parent)
        self.windows, self.selected, self.available = windows, selected, available
        self.changed, self.family = changed, family
        self.image_loader = image_loader
        self.photos, self.cards, self.vars = [], [], {}
        self.canvas = tk.Canvas(self.frame, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.frame, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        self.inner = ttk.Frame(self.canvas)
        self.inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.canvas.bind('<Configure>', self.layout)
        self.inner.bind('<Configure>', lambda event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        style = ttk.Style(parent)
        style.configure('Gallery.TCheckbutton', background=WHITE)
        style.map('Gallery.TCheckbutton', background=[('active', WHITE)])
        for index, row in enumerate(windows):
            card = ttk.Frame(self.inner, style='Rounded.TFrame', padding=12)
            self.cards.append(card)
            var = tk.BooleanVar(value=index in selected); self.vars[index] = var
            check = ttk.Checkbutton(card, text=LABELS[row['kind']] + (' · 已最小化' if row.get('minimized') else ' · 已打开'),
                                    variable=var, style='Gallery.TCheckbutton',
                                    command=lambda i=index: self.toggle(i))
            check.pack(fill='x', pady=(0, 7))
            if index not in available:
                check.configure(state='disabled')
            view = tk.Frame(card, width=264, height=166, bg='#eaf1ee')
            view.pack(fill='x'); view.pack_propagate(False)
            preview = row.get('preview', {})
            photo = None
            if preview.get('image'):
                try:
                    picture = Image.open(BytesIO(base64.b64decode(preview['image']))).convert('RGB')
                    picture.thumbnail((264, 166), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(picture, master=parent); self.photos.append(photo)
                except Exception:
                    pass
            if photo:
                content = tk.Label(view, image=photo, bg='#eaf1ee')
            else:
                reason = preview.get('reason') or '旧存档没有预览图\n拍摄新快照即可保存画面'
                content = tk.Label(view, text=reason, bg='#eaf1ee', fg=MUTED,
                                   font=(family, 10), wraplength=240, justify='center')
            content.pack(expand=True)
            content.bind('<Double-1>', lambda event, i=index: self.zoom(i))
            caption = tk.Label(card, text=row['title'] or row['app'], bg=WHITE, fg=INK,
                               font=(family, 11), wraplength=260, anchor='w', justify='left', height=2)
            caption.pack(fill='x', pady=(7, 3))
            status = ('上次截图 ' if preview.get('status') == 'cached' else '截图 ') + preview_time(preview.get('time', 0)) if photo else '窗口信息已保存'
            if preview.get('method') == 'demo':
                status = '合成演示图'
            tk.Label(card, text=status, bg=WHITE, fg=MUTED, font=(family, 9), anchor='w').pack(fill='x')
            if preview.get('reason') and photo and not (preview.get('full_image') or preview.get('full_image_ref')):
                tk.Label(card, text=preview['reason'], bg=WHITE, fg=MUTED, font=(family, 9), wraplength=260, justify='left').pack(fill='x')
            button = ttk.Button(card, text='查看高清画面' if preview.get('full_image') or preview.get('full_image_ref') else '查看缩略图', style='Panel.Quiet.TButton', command=lambda i=index: self.zoom(i))
            button.pack(anchor='e', pady=(3, 0))
            if not photo:
                button.configure(state='disabled')
        self.columns = 0
        self.canvas.bind('<MouseWheel>', self.scroll)
        # A dialog-local bindtag avoids hijacking scrolling in other windows.
        tag = 'WorkspaceGallery' + str(id(self))
        self.canvas.bind_class(tag, '<MouseWheel>', self.scroll)
        def tag_children(widget):
            widget.bindtags((tag, *widget.bindtags()))
            for child in widget.winfo_children():
                tag_children(child)
        tag_children(self.inner)
        self.canvas.bind('<Destroy>', lambda event: self.canvas.unbind_class(tag, '<MouseWheel>') if event.widget == self.canvas else None)

    def scroll(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), 'units')
        return 'break'

    def layout(self, event):
        width = max(event.width, 280)
        self.canvas.itemconfigure(self.inner_id, width=width)
        columns = max(1, width // 300)
        if columns == self.columns:
            return
        for col in range(max(columns, self.columns)):
            self.inner.columnconfigure(col, weight=1 if col < columns else 0, uniform='cards' if col < columns else '')
        for index, card in enumerate(self.cards):
            card.grid(row=index // columns, column=index % columns, sticky='nsew', padx=5, pady=5)
        self.columns = columns

    def toggle(self, index):
        if self.vars[index].get():
            self.selected.add(index)
        else:
            self.selected.discard(index)
        self.changed(index)

    def sync(self, index):
        self.vars[index].set(index in self.selected)

    def zoom(self, index):
        row = self.windows[index]; preview = row.get('preview', {})
        if not preview.get('image'):
            return
        try:
            raw = (self.image_loader(preview['full_image_ref']) if preview.get('full_image_ref') and self.image_loader
                   else base64.b64decode(preview.get('full_image') or preview['image']))
            picture = Image.open(BytesIO(raw)).convert('RGB')
        except Exception as error:
            messagebox.showerror('画面暂不可读取', str(error), parent=self.frame)
            return
        win = tk.Toplevel(self.frame); win.title('窗口画面 · ' + row['title'])
        win.configure(bg=BG); apply_icon(win); apply_window_effects(win)
        win.geometry(f'{min(1100, win.winfo_screenwidth()-80)}x{min(800, win.winfo_screenheight()-100)}')
        bar = ttk.Frame(win, padding=12); bar.pack(fill='x')
        text = ('高清画面' if preview.get('full_image') or preview.get('full_image_ref') else '仅缩略图') + f' · {picture.width} × {picture.height} · ' + preview_time(preview.get('time', 0))
        ttk.Label(bar, text=text).pack(side='left')
        panel = ttk.Frame(win); panel.pack(fill='both', expand=True, padx=12, pady=(0, 12))
        canvas = tk.Canvas(panel, bg='#eaf1ee', highlightthickness=0)
        ys = ttk.Scrollbar(panel, orient='vertical', command=canvas.yview)
        xs = ttk.Scrollbar(panel, orient='horizontal', command=canvas.xview)
        canvas.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        canvas.grid(row=0, column=0, sticky='nsew'); ys.grid(row=0, column=1, sticky='ns'); xs.grid(row=1, column=0, sticky='ew')
        panel.rowconfigure(0, weight=1); panel.columnconfigure(0, weight=1)
        def render(original=False):
            display = picture.copy()
            if not original:
                display.thumbnail((max(100, canvas.winfo_width()-12), max(100, canvas.winfo_height()-12)), Image.Resampling.LANCZOS)
            win.preview_photo = ImageTk.PhotoImage(display, master=win)
            canvas.delete('all'); canvas.create_image(0, 0, image=win.preview_photo, anchor='nw')
            canvas.configure(scrollregion=(0, 0, display.width, display.height))
        ttk.Button(bar, text='原始比例 100%', command=lambda: render(True)).pack(side='right')
        ttk.Button(bar, text='适应窗口', command=render).pack(side='right', padx=8)
        canvas.bind('<MouseWheel>', lambda event: canvas.yview_scroll(-int(event.delta / 120), 'units'))
        win.after(100, render)
