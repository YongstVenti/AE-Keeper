"""Synthetic samples only. Run from the repository root; prints no user data."""
from io import BytesIO
import json
from pathlib import Path
import time
import zlib
import zstandard as zstd
from PIL import Image, ImageDraw, ImageFont


def main():
    picture = Image.new('RGB', (1920, 1080), '#f4f8f6')
    draw = ImageDraw.Draw(picture)
    font = ImageFont.truetype(str(Path(__file__).resolve().parents[1] / 'assets/SmileySans-Oblique.ttf'), 24)
    for row in range(30):
        draw.rectangle((12, row * 34 + 20, 1900, row * 34 + 50), fill='#e7f2ee' if row % 2 else '#ffffff')
        draw.text((30, row * 34 + 20), f'项目文件  /  工作记录  /  python  --version  /  AE Keeper  #{row:02}', font=font, fill='#203d36')
    results = []
    for fmt, options in [('PNG', dict(optimize=True)), ('WEBP', dict(lossless=True, quality=85, method=4)),
                         ('WEBP', dict(quality=85, method=4)), ('AVIF', dict(quality=80, speed=8, subsampling='4:4:4', max_threads=2))]:
        output = BytesIO(); start = time.perf_counter()
        try:
            picture.save(output, format=fmt, **options)
        except (OSError, ValueError):
            results.append(dict(format=fmt, unavailable=True)); continue
        raw = output.getvalue(); elapsed = time.perf_counter() - start
        decoded = Image.open(BytesIO(raw)).convert('RGB')
        results.append(dict(format=fmt, lossless=options.get('lossless', fmt == 'PNG'),
                            bytes=len(raw), seconds=round(elapsed, 3), exact=decoded.tobytes() == picture.tobytes()))
    payload = json.dumps({'tabs': [dict(browser='Chrome' if i % 2 else 'Edge', title=f'示例网页 {i}',
        url=f'https://example.com/project/{i}', window=str(i // 15), target=str(i), source='实时连接') for i in range(131)]}, ensure_ascii=False).encode()
    print(json.dumps(dict(image=results, json_bytes=len(payload), zlib_bytes=len(zlib.compress(payload, 9)),
                          zstd_bytes=len(zstd.ZstdCompressor(level=9).compress(payload))), ensure_ascii=False))


if __name__ == '__main__':
    main()
