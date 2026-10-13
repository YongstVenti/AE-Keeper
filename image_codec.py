"""Bounded image encoders; never enlarge a captured source."""
from io import BytesIO
from PIL import Image

MAX_FULL_BYTES = 8 * 1024 * 1024


def encode_full(picture, edge=1920, mode='lossless'):
    picture = picture.convert('RGB')
    picture.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    output = BytesIO()
    picture.save(output, format='WEBP', lossless=True, quality=85, method=4)
    candidates = [output.getvalue()]
    if mode == 'compact':
        output = BytesIO()
        picture.save(output, format='WEBP', quality=85, method=4)
        candidates.append(output.getvalue())
        try:
            output = BytesIO()
            picture.save(output, format='AVIF', quality=80, speed=8, subsampling='4:4:4', max_threads=2)
            candidates.append(output.getvalue())
        except (OSError, ValueError):
            pass  # WebP remains portable on builds without AVIF.
    raw = min(candidates, key=len)
    if len(raw) > MAX_FULL_BYTES:
        raise ValueError('高清图超过 8 MB，请降低分辨率或使用节省空间模式。')
    return raw


def dimensions(raw):
    with Image.open(BytesIO(raw)) as picture:
        return picture.size
