"""Bounded, versioned desktop metadata, independent of Windows APIs."""
import math
import base64
from io import BytesIO

KINDS = {'explorer', 'terminal', 'console', 'app', 'browser'}


def clean_workspace(value, imported=False):
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('windows'), list):
        raise ValueError('工作现场格式无效。')
    if len(value['windows']) > 200:
        raise ValueError('工作现场超过 200 个窗口。')
    total_text = 0
    def text(item, key, limit=4096):
        nonlocal total_text
        val = item.get(key, '')
        if not isinstance(val, str) or '\x00' in val or len(val) > limit:
            raise ValueError('工作现场包含无效文本。')
        total_text += len(val.encode('utf-8'))
        if total_text > 16 * 1024 * 1024:
            raise ValueError('工作现场文字信息超过 16 MB。')
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
        row.update(rect=rect, maximized=bool(item.get('maximized')), minimized=bool(item.get('minimized')),
                   hwnd=int(item.get('hwnd', 0)), pid=int(item.get('pid', 0)),
                   created=float(item.get('created', 0)))
        if not math.isfinite(row['created']):
            raise ValueError('窗口时间格式无效。')
        if imported:
            row.update(hwnd=0, pid=0, created=0)
        if item.get('preview'):
            preview = item['preview']
            if not isinstance(preview, dict) or preview.get('status') not in ('captured', 'cached', 'unavailable', 'disabled'):
                raise ValueError('窗口预览格式无效。')
            image = preview.get('image', '')
            stamp = float(preview.get('time', 0))
            if not isinstance(image, str) or len(image) > 250000 or not math.isfinite(stamp) or stamp < 0:
                raise ValueError('窗口预览大小或时间无效。')
            if image:
                from PIL import Image
                try:
                    raw = base64.b64decode(image, validate=True)
                    with Image.open(BytesIO(raw)) as picture:
                        if picture.format not in ('WEBP', 'PNG', 'JPEG') or max(picture.size) > 1024 or picture.width * picture.height > 700000:
                            raise ValueError('窗口预览图超出大小限制。')
                        picture.verify()
                except Exception as error:
                    raise ValueError('窗口预览图无效。') from error
            row['preview'] = {'image': image, 'time': stamp, 'status': preview['status'],
                              'method': text(preview, 'method', 100), 'reason': text(preview, 'reason', 500)}
            full = preview.get('full_image', '')
            if full:
                from PIL import Image
                if not isinstance(full, str) or len(full) > 12 * 1024 * 1024:
                    raise ValueError('高清图大小无效。')
                try:
                    raw = base64.b64decode(full, validate=True)
                    if len(raw) > 8 * 1024 * 1024:
                        raise ValueError('高清图超过 8 MB。')
                    with Image.open(BytesIO(raw)) as picture:
                        if picture.format not in ('WEBP', 'PNG', 'AVIF') or max(picture.size) > 3840 or picture.width * picture.height > 12000000:
                            raise ValueError('高清图超过分辨率限制。')
                        picture.verify()
                except Exception as error:
                    raise ValueError('高清窗口画面无效。') from error
                row['preview']['full_image'] = full
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
    value = {'version': 1, 'warnings': [], 'windows': [
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
    # Public demo screenshots are generated from these synthetic windows only.
    from PIL import Image, ImageDraw, ImageFont
    from pathlib import Path
    font_path = Path(__file__).parent / 'assets/SmileySans-Oblique.ttf'
    font = ImageFont.truetype(str(font_path), 22)
    small = ImageFont.truetype(str(font_path), 16)
    for index, row in enumerate(value['windows']):
        row['minimized'] = index < 2
        picture = Image.new('RGB', (600, 350), '#f3f8f6' if index != 1 else '#18262e')
        draw = ImageDraw.Draw(picture)
        draw.rectangle((0, 0, 600, 42), fill='#0d766d')
        draw.text((15, 8), row['title'], fill='white', font=font)
        if index == 0:
            draw.rectangle((0, 45, 155, 350), fill='#e7f1ed')
            draw.text((15, 65), '项目\n参考资料\n图片', fill='#25453f', font=small, spacing=18)
            for n in range(6):
                x, y = 185 + (n % 3) * 130, 90 + (n // 3) * 100
                draw.rounded_rectangle((x, y, x + 94, y + 60), radius=8, fill='#d6b561')
                draw.text((x, y + 63), ['设计稿', '工作记录', '参考素材'][n % 3], fill='#25453f', font=small)
        elif index == 1:
            draw.text((20, 65), 'PS C:\\Demo\\项目>\nAE Keeper demo workspace\n\nReady for the next task.', fill='#bddbd2', font=small, spacing=15)
        else:
            draw.text((25, 72), '工作计划\n\n整理项目资料\n继续昨天的设计\n保留这时的工作现场', fill='#25453f', font=font, spacing=12)
        output = BytesIO(); picture.save(output, format='WEBP', quality=75)
        row['preview'] = {'status': 'captured', 'image': base64.b64encode(output.getvalue()).decode('ascii'),
                          'time': 1, 'method': 'demo', 'reason': '演示图'}
    return value
