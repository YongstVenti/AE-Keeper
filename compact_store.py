"""Content-addressed, losslessly compressed local archive objects.

SQL rows retain searchable dates/notes. Portable exports expand references back
to the original JSON, so no external files are needed to move an archive.
"""
import base64
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
import zstandard as zstd

PREFIX = b'AEK5'
TABLES = ('snapshots', 'daily_archives', 'daily_entries', 'workspaces')
MAX_JSON = 512 * 1024 * 1024


class CompactStore:
    def __init__(self, db, folder):
        self.db = db
        legacy = sum(db.execute(f"SELECT COUNT(*) FROM {t} WHERE typeof(payload)='text'").fetchone()[0] for t in TABLES)
        if legacy:
            # Back up before adding compression triggers, so the prior binary
            # can also open and write this recovery copy.
            path = Path(folder) / f'archive-before-compact-{time.time_ns()}.sqlite3'
            with closing(sqlite3.connect(path)) as backup:
                db.backup(backup)
                if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('升级前的档案备份校验失败。')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS archive_objects(hash TEXT PRIMARY KEY,kind TEXT NOT NULL,data BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS archive_links(owner TEXT,child TEXT,PRIMARY KEY(owner,child));
        ''')
        db.create_function('ae_pack', 1, self.pack)
        for table in TABLES:
            for event in ('INSERT', 'UPDATE OF payload'):
                name = 'insert' if event == 'INSERT' else 'update'
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS compact_{table}_{name}
                    AFTER {event} ON {table} WHEN typeof(NEW.payload)='text'
                    BEGIN UPDATE {table} SET payload=ae_pack(NEW.payload),
                      bytes=128+length(CAST(NEW.note AS BLOB)) WHERE rowid=NEW.rowid; END''')
        db.commit()
        if legacy:
            with db:
                for table in TABLES:
                    db.execute(f"UPDATE {table} SET payload=payload WHERE typeof(payload)='text'")
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        if legacy or db.execute('PRAGMA auto_vacuum').fetchone()[0] != 2:
            db.execute('PRAGMA auto_vacuum=INCREMENTAL')
            db.execute('VACUUM')
        db.row_factory = self.row

    def put(self, kind, raw):
        key = hashlib.sha256(kind.encode() + b'\0' + raw).hexdigest()
        self.db.execute('INSERT OR IGNORE INTO archive_objects VALUES(?,?,?)', (key, kind, raw))
        return key

    def pack(self, text):
        value = json.loads(text)
        kind = 'workspace' if 'windows' in value else 'browser'
        children = set()
        for window in value.get('windows', []):
            preview = window.get('preview', {})
            for field in ('image', 'full_image'):
                if preview.get(field):
                    key = self.put('image', base64.b64decode(preview[field], validate=True))
                    preview[field] = {'object': key}
                    children.add(key)
        raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
        packed = zstd.ZstdCompressor(level=9).compress(raw)
        key = self.put(kind, packed)
        for child in children:
            self.db.execute('INSERT OR IGNORE INTO archive_links VALUES(?,?)', (key, child))
        return PREFIX + bytes.fromhex(key)

    def unpack(self, reference, include_full=True):
        if not isinstance(reference, bytes) or len(reference) != 36 or not reference.startswith(PREFIX):
            raise ValueError('压缩档案引用无效。')
        key = reference[4:].hex()
        found = self.db.execute('SELECT data FROM archive_objects WHERE hash=?', (key,)).fetchone()
        if not found:
            raise ValueError('档案内容缺失，请从备份恢复。')
        value = json.loads(zstd.ZstdDecompressor().decompress(found[0], max_output_size=MAX_JSON))
        for window in value.get('windows', []):
            preview = window.get('preview', {})
            for field in ('image', 'full_image'):
                if isinstance(preview.get(field), dict):
                    if field == 'full_image' and not include_full:
                        preview['full_image_ref'] = preview.pop(field)['object']
                        continue
                    image = self.db.execute('SELECT data FROM archive_objects WHERE hash=? AND kind=?',
                                            (preview[field]['object'], 'image')).fetchone()
                    if not image:
                        raise ValueError('档案图片缺失，请从备份恢复。')
                    preview[field] = base64.b64encode(image[0]).decode('ascii')
        return json.dumps(value, ensure_ascii=False)

    def row(self, cursor, row):
        return tuple(self.unpack(v) if cursor.description[i][0] == 'payload' and isinstance(v, bytes) else v
                     for i, v in enumerate(row))

    def collect(self):
        live = set()
        for table in TABLES:
            for (value,) in self.db.execute(f'SELECT CAST(payload AS BLOB) AS reference FROM {table}'):
                if value.startswith(PREFIX):
                    live.add(value[4:].hex())
        for owner, child in self.db.execute('SELECT owner,child FROM archive_links'):
            if owner in live:
                live.add(child)
        dead = [(key,) for (key,) in self.db.execute('SELECT hash FROM archive_objects') if key not in live]
        self.db.executemany('DELETE FROM archive_objects WHERE hash=?', dead)
        self.db.execute('DELETE FROM archive_links WHERE owner NOT IN (SELECT hash FROM archive_objects)')
        self.db.execute('PRAGMA incremental_vacuum(128)')

    def size(self, kind):
        if kind == 'workspace':
            return self.db.execute("SELECT COALESCE(SUM(length(data)),0) FROM archive_objects WHERE kind IN ('workspace','image')").fetchone()[0]
        return self.db.execute('SELECT COALESCE(SUM(length(data)),0) FROM archive_objects WHERE kind=?', (kind,)).fetchone()[0]


def read_image(folder, key):
    """A short-lived read-only connection; never share the worker connection."""
    if not isinstance(key, str) or len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('图片引用无效。')
    path = (Path(folder) / 'archive.sqlite3').resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute("SELECT data FROM archive_objects WHERE hash=? AND kind='image'", (key,)).fetchone()
    if not row or len(row[0]) > 8 * 1024 * 1024:
        raise ValueError('图片已被清理或不可读取。')
    if hashlib.sha256(b'image\0' + row[0]).hexdigest() != key:
        raise ValueError('图片内容校验失败。')
    return row[0]
