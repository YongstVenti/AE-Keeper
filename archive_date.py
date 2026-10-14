"""Archive dates are user labels, independent of actual capture timestamps."""
import datetime as dt
from tkinter import simpledialog, messagebox


def ask_archive_date(parent, current, desktop=False):
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    detail = ('目标日期已有桌面存档时，保留所有原快照，每日列表显示实际保存时间最晚的一份。'
              if desktop else '只移动选中的这一份，原日期的其他快照保留。目标日期已有存档时，移入的快照设为主存档，原快照保留为子项。')
    while True:
        value = simpledialog.askstring('更改归档日期',
            f'当前归档日期：{current}\n实际保存时间和内容不会改变。\n{detail}\n\n输入日期（YYYY-MM-DD），例如昨天 {yesterday}：',
            initialvalue=current, parent=parent)
        if value is None:
            return None
        value = value.strip()
        try:
            if dt.date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except ValueError:
            messagebox.showerror('日期格式不正确', '请填写有效日期，例如 2026-10-14。', parent=parent)
            continue
        return value
