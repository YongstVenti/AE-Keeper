"""Bounded, versioned desktop metadata, independent of Windows APIs."""
import math

KINDS = {'explorer', 'terminal', 'console', 'app', 'browser'}


def clean_workspace(value, imported=False):
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('windows'), list):
        raise ValueError('工作现场格式无效。')
    if len(value['windows']) > 200:
        raise ValueError('工作现场超过 200 个窗口。')
    def text(item, key, limit=4096):
        val = item.get(key, '')
        if not isinstance(val, str) or '\x00' in val or len(val) > limit:
            raise ValueError('工作现场包含无效文本。')
        return val
    rows = []
    for item in value['windows']:
        if not isinstance(item, dict) or item.get('kind') not in KINDS:
            raise ValueError('工作现场包含未知窗口类型。')
        row = {k: text(item, k) for k in ('title', 'app', 'exe', 'detail')}
        row['kind'] = item['kind']
        rect = item.get('rect', [80, 80, 1000, 700])
        if (not isinstance(rect, list) or len(rect) != 4
                or any(type(v) is not int or abs(v) > 100000 for v in rect)
                or rect[2] <= 0 or rect[3] <= 0):
            raise ValueError('窗口位置格式无效。')
        row.update(rect=rect, maximized=bool(item.get('maximized')),
                   hwnd=int(item.get('hwnd', 0)), pid=int(item.get('pid', 0)),
                   created=float(item.get('created', 0)))
        if not math.isfinite(row['created']):
            raise ValueError('窗口时间格式无效。')
        if imported:
            row.update(hwnd=0, pid=0, created=0)
        for key, fields in [('tabs', ('path', 'title')), ('sessions', ('exe', 'cwd', 'title'))]:
            entries = item.get(key, [])
            if not isinstance(entries, list) or len(entries) > 128:
                raise ValueError('窗口标签数量无效。')
            if any(not isinstance(entry, dict) for entry in entries):
                raise ValueError('窗口标签格式无效。')
            row[key] = [{k: text(entry, k) for k in fields} for entry in entries]
        rows.append(row)
    warnings = value.get('warnings', [])
    if not isinstance(warnings, list) or len(warnings) > 100 or any(not isinstance(x, str) or len(x) > 4096 for x in warnings):
        raise ValueError('工作现场提示格式无效。')
    return {'version': 1, 'windows': rows, 'warnings': warnings}


def workspace_counts(payload):
    rows = payload['windows']
    return (len(rows), sum(len(w.get('tabs', [])) for w in rows),
            sum(len(w.get('sessions', [])) for w in rows))


def demo_workspace():
    return {'version': 1, 'warnings': [], 'windows': [
        {'kind': 'explorer', 'app': 'explorer.exe', 'title': '项目文件', 'exe': r'C:\Windows\explorer.exe',
         'rect': [100, 100, 1000, 700], 'tabs': [{'path': r'C:\Demo\项目', 'title': '项目'},
         {'path': r'C:\Demo\参考资料', 'title': '参考资料'}, {'path': r'C:\Demo\图片', 'title': '图片'}],
         'detail': '演示：三个文件夹标签页，按原窗口恢复'},
        {'kind': 'terminal', 'app': 'WindowsTerminal.exe', 'title': '项目终端', 'exe': r'C:\Demo\WindowsTerminal.exe',
         'rect': [250, 180, 900, 600], 'sessions': [{'exe': r'C:\Demo\pwsh.exe', 'cwd': r'C:\Demo\项目', 'title': 'PowerShell'}],
         'detail': '演示：恢复进程目录，不接续运行中的命令'},
        {'kind': 'app', 'app': 'notepad.exe', 'title': '工作笔记', 'exe': r'C:\Windows\System32\notepad.exe',
         'rect': [380, 200, 700, 500], 'detail': '演示：只能打开软件，不恢复未保存内容'},
    ]}
