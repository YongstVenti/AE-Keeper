import datetime as dt
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

DEFAULTS = {'enabled': True, 'interval': 60, 'mode': 'auto', 'limit_mb': 100, 'retention': 90,
            'chrome_root': '', 'edge_root': '', 'startup': False, 'include_all_profiles': False}


def safe_url(value):
    try:
        u = urlparse(value)
        return u.scheme.lower() in ('http', 'https') and bool(u.netloc)
    except (ValueError, TypeError):
        return False


def day_key(timestamp=None):
    return dt.datetime.fromtimestamp(time.time() if timestamp is None else timestamp).strftime('%Y-%m-%d')


def complete_tabs(rows, connected):
    return (set(connected) == {'Chrome', 'Edge'}
            and {r.get('browser') for r in rows} == {'Chrome', 'Edge'}
            and all(r.get('url') and r.get('window') and r.get('target') for r in rows))


class Archive:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.folder / 'archive.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY,time REAL,last_seen REAL,day TEXT,fingerprint TEXT,payload TEXT,bytes INTEGER,note TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS snapshot_day ON snapshots(day);
        CREATE TABLE IF NOT EXISTS daily_archives(day TEXT PRIMARY KEY,time REAL,last_seen REAL,fingerprint TEXT,payload TEXT,bytes INTEGER,note TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS visits(day TEXT,browser TEXT,url TEXT,title TEXT,first_seen REAL,last_seen REAL,count INTEGER,note TEXT DEFAULT '',bytes INTEGER,PRIMARY KEY(day,browser,url));
        CREATE INDEX IF NOT EXISTS visit_time ON visits(last_seen);
        CREATE TABLE IF NOT EXISTS bookmarks(url TEXT PRIMARY KEY,title TEXT,paths TEXT,sources TEXT,note TEXT DEFAULT '',bytes INTEGER);
        ''')
        self.db.commit()
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(daily_archives)')}
        for name, spec in [('primary_entry', 'INTEGER'), ('primary_locked', 'INTEGER DEFAULT 0'), ('modified_at', 'REAL DEFAULT 0')]:
            if name not in columns:
                self.db.execute(f'ALTER TABLE daily_archives ADD COLUMN {name} {spec}')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS daily_entries(id INTEGER PRIMARY KEY,day TEXT,time REAL,last_seen REAL,fingerprint TEXT,payload TEXT,bytes INTEGER,note TEXT DEFAULT '',origin TEXT DEFAULT 'manual');
        CREATE INDEX IF NOT EXISTS daily_entry_day ON daily_entries(day);
        CREATE UNIQUE INDEX IF NOT EXISTS daily_auto_day ON daily_entries(day) WHERE origin='auto';
        ''')
        with self.db:
            for day, stamp, fingerprint, payload, note in self.db.execute('SELECT day,last_seen,fingerprint,payload,note FROM daily_archives WHERE primary_entry IS NULL').fetchall():
                eid = self._insert_entry(day, stamp, fingerprint, payload, note, 'auto')
                self._set_primary(day, eid, False, stamp)
        # Old snapshots did not record proof of complete enumeration. Only
        # explicitly verified snapshots may seed daily archives on migration.
        with self.db:
            if not self.meta('daily_initialized', False):
                for row in self.db.execute('SELECT last_seen,fingerprint,payload,note FROM snapshots ORDER BY last_seen').fetchall():
                    data = json.loads(row[2])
                    if data.get('complete') is True:
                        self._upsert_daily(row[0], row[1], row[2], row[3])
                self.set_meta('daily_initialized', True)

    def meta(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def settings(self):
        return {**DEFAULTS, **self.meta('settings', {})}

    def save_settings(self, values):
        s = {**self.settings(), **values}
        s['interval'] = max(10, min(3600, int(s['interval'])))
        s['retention'] = max(1, min(3650, int(s['retention'])))
        s['limit_mb'] = max(10, min(1000, int(s['limit_mb'])))
        if s['mode'] not in ('auto', 'all', 'snapshots'):
            raise ValueError('记录模式无效')
        if self.bytes('bookmarks') > s['limit_mb'] * 1048576 * .15:
            raise ValueError('已有收藏超过新容量，请提高上限。')
        with self.db:
            self.set_meta('settings', s)
            self.cleanup(s)
        return s

    def bytes(self, table):
        if table not in ('visits', 'snapshots', 'bookmarks', 'daily_archives'):
            raise ValueError('未知档案类型')
        total = self.db.execute(f'SELECT COALESCE(SUM(bytes),0) FROM {table}').fetchone()[0]
        if table == 'daily_archives':
            total += self.db.execute('SELECT COALESCE(SUM(bytes),0) FROM daily_entries').fetchone()[0]
        return total

    def record(self, rows, changed_targets):
        s = self.settings()
        if not s['enabled'] or s['mode'] == 'snapshots':
            return
        now, day, used, limit = time.time(), day_key(), self.bytes('visits'), s['limit_mb'] * 1048576 * .35
        with self.db:
            for row in rows:
                url = row.get('url', '')
                if not safe_url(url):
                    continue
                browser, title = row['browser'], row.get('title', url)[:2000]
                previous = self.db.execute('SELECT bytes,count FROM visits WHERE day=? AND browser=? AND url=?', (day, browser, url)).fetchone()
                size = len((url + title).encode('utf-8')) + 128
                old_size = previous[0] if previous else 0
                if s['mode'] == 'auto' and used - old_size + size > limit:
                    self.set_meta('fallback', True)
                    continue
                increment = int((browser, row.get('target')) in changed_targets)
                if not previous:
                    increment = max(1, increment)
                self.db.execute('''INSERT INTO visits(day,browser,url,title,first_seen,last_seen,count,bytes) VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(day,browser,url) DO UPDATE SET title=excluded.title,last_seen=excluded.last_seen,count=visits.count+excluded.count,bytes=excluded.bytes''',
                                (day, browser, url, title, now, now, increment, size))
                used += size - old_size
            if s['mode'] == 'all':
                self.cleanup(s)

    def capture(self, rows, connected, complete=None):
        # Empty connected browsers may have no tabs; failed or missing connections must not erase a prior backup.
        if not rows:
            return False
        now = time.time()
        day = day_key(now)
        clean = sorted([{k: r.get(k, '') for k in ('browser', 'window', 'target', 'title', 'url', 'source')} for r in rows], key=lambda r: (r['browser'], r['window'], r['target']))
        verified = complete_tabs(clean, connected) and complete is not False
        data = {'tabs': clean, 'connected': sorted(connected), 'complete': verified}
        signature = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        payload = json.dumps(data, ensure_ascii=False)
        size = len(payload.encode()) + 128
        s = self.settings()
        if size * (3 if verified else 1) > s['limit_mb'] * 1048576 * .5:
            raise ValueError('这一组标签页超过快照预留容量，请提高上限。')
        with self.db:
            latest = self.db.execute('SELECT id,day,fingerprint FROM snapshots ORDER BY id DESC LIMIT 1').fetchone()
            if latest and latest[1] == day and latest[2] == signature:
                self.db.execute('UPDATE snapshots SET last_seen=? WHERE id=?', (now, latest[0]))
            else:
                self.db.execute('INSERT INTO snapshots(time,last_seen,day,fingerprint,payload,bytes) VALUES(?,?,?,?,?,?)', (now, now, day, signature, payload, size))
            self.set_meta('last_saved', now)
            if verified:
                self._upsert_daily(now, signature, payload)
            self.cleanup(s)
        return True

    def _upsert_daily(self, stamp, fingerprint, payload, note=''):
        data = json.loads(payload)
        if data.get('complete') is not True or not complete_tabs(data['tabs'], data['connected']):
            return False
        day = day_key(stamp)
        previous = self.db.execute("SELECT id,last_seen,note FROM daily_entries WHERE day=? AND origin='auto'", (day,)).fetchone()
        if previous and previous[1] > stamp:
            return False
        if previous:
            note = (previous[2] or note)[:2000]
            eid = previous[0]
            self.db.execute('UPDATE daily_entries SET time=?,last_seen=?,fingerprint=?,payload=?,bytes=?,note=? WHERE id=?',
                            (stamp, stamp, fingerprint, payload, len(payload.encode()) + len(note.encode()) + 128, note, eid))
        else:
            eid = self._insert_entry(day, stamp, fingerprint, payload, note, 'auto')
        root = self.db.execute('SELECT primary_locked FROM daily_archives WHERE day=?', (day,)).fetchone()
        if not root or not root[0]:
            self._set_primary(day, eid, False, stamp)
        if stamp >= self.meta('last_daily_saved', 0):
            self.set_meta('last_daily_saved', stamp)
        return True

    def _insert_entry(self, day, stamp, fingerprint, payload, note='', origin='manual'):
        note = str(note)[:2000]
        size = len(payload.encode()) + len(note.encode()) + 128
        cur = self.db.execute('INSERT INTO daily_entries(day,time,last_seen,fingerprint,payload,bytes,note,origin) VALUES(?,?,?,?,?,?,?,?)',
                              (day, stamp, stamp, fingerprint, payload, size, note, origin))
        return cur.lastrowid

    def _set_primary(self, day, eid, locked=True, modified=None):
        row = self.db.execute('SELECT time,last_seen,fingerprint,payload,bytes,note FROM daily_entries WHERE id=? AND day=?', (eid, day)).fetchone()
        if not row:
            raise ValueError('这个每日快照已不存在。')
        self.db.execute('''INSERT INTO daily_archives(day,time,last_seen,fingerprint,payload,bytes,note,primary_entry,primary_locked,modified_at) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(day) DO UPDATE SET time=excluded.time,last_seen=excluded.last_seen,fingerprint=excluded.fingerprint,payload=excluded.payload,bytes=excluded.bytes,note=excluded.note,primary_entry=excluded.primary_entry,primary_locked=excluded.primary_locked,modified_at=excluded.modified_at''',
            (day, *row, eid, int(locked), time.time() if modified is None else modified))

    @staticmethod
    def _valid_day(day):
        if dt.date.fromisoformat(day).isoformat() != day:
            raise ValueError('请填写 YYYY-MM-DD 格式的日期。')

    def add_daily(self, snapshot_id, day, mode='append'):
        self._valid_day(day)
        if mode not in ('append', 'replace'):
            raise ValueError('每日保存方式无效。')
        row = self.db.execute('SELECT last_seen,fingerprint,payload,note FROM snapshots WHERE id=?', (snapshot_id,)).fetchone()
        if not row:
            raise ValueError('原窗口快照已不存在。')
        with self.db:
            existing = self.db.execute('SELECT id FROM daily_entries WHERE day=? AND last_seen=? AND fingerprint=?', (day, row[0], row[1])).fetchone()
            eid = existing[0] if existing else self._insert_entry(day, *row)
            self.db.execute("UPDATE daily_entries SET origin='manual' WHERE id=?", (eid,))
            root = self.db.execute('SELECT primary_entry,primary_locked FROM daily_archives WHERE day=?', (day,)).fetchone()
            if root:
                self.db.execute("UPDATE daily_entries SET origin='manual' WHERE id=?", (root[0],))
            if mode == 'replace' or not root:
                self._set_primary(day, eid)
            else:
                # Adding a candidate also freezes the displayed primary. An
                # automatic primary becomes a saved manual copy in place.
                self.db.execute("UPDATE daily_entries SET origin='manual' WHERE id=?", (root[0],))
                self._set_primary(day, root[0])
            self._daily_capacity()
        return eid

    def primary_daily(self, eid):
        row = self.db.execute('SELECT day FROM daily_entries WHERE id=?', (eid,)).fetchone()
        if not row:
            raise ValueError('这个每日快照已不存在。')
        with self.db:
            previous = self.db.execute('SELECT primary_entry FROM daily_archives WHERE day=?', (row[0],)).fetchone()
            if previous:
                self.db.execute("UPDATE daily_entries SET origin='manual' WHERE id=?", (previous[0],))
            self.db.execute("UPDATE daily_entries SET origin='manual' WHERE id=?", (eid,))
            self._set_primary(row[0], eid)
            self._daily_capacity()

    def _daily_capacity(self):
        if self.bytes('daily_archives') + self.bytes('snapshots') > self.settings()['limit_mb'] * 1048576 * .5:
            raise ValueError('快照容量不足，本次操作已回滚，请先提高容量上限。')

    def daily_entry(self, eid):
        row = self.db.execute('SELECT id,last_seen,day,payload,note,origin FROM daily_entries WHERE id=?', (eid,)).fetchone()
        return {'id': row[0], 'entry_id': row[0], 'time': row[1], 'day': row[2], **json.loads(row[3]), 'note': row[4], 'origin': row[5], 'is_day': False} if row else None

    def _delete_day(self, day):
        self.db.execute('DELETE FROM daily_entries WHERE day=?', (day,))
        self.db.execute('DELETE FROM daily_archives WHERE day=?', (day,))

    def cleanup(self, s=None):
        s = s or self.settings()
        cutoff = time.time() - s['retention'] * 86400
        self.db.execute('DELETE FROM visits WHERE last_seen<?', (cutoff,))
        self.db.execute('DELETE FROM snapshots WHERE last_seen<?', (cutoff,))
        for (day,) in self.db.execute('SELECT day FROM daily_archives WHERE MAX(last_seen,modified_at)<?', (cutoff,)).fetchall():
            self._delete_day(day)
        for table, fraction, order in [('visits', .35, 'last_seen'), ('snapshots', .5, 'last_seen')]:
            limit = s['limit_mb'] * 1048576 * fraction
            # Daily copies share the original snapshot allocation. Prefer
            # removing old quick snapshots before removing daily archives.
            used = self.bytes(table) + (self.bytes('daily_archives') if table == 'snapshots' else 0)
            for rid, size in self.db.execute(f'SELECT rowid,bytes FROM {table} ORDER BY {order}').fetchall():
                if used <= limit:
                    break
                self.db.execute(f'DELETE FROM {table} WHERE rowid=?', (rid,))
                used -= size
        daily_limit = s['limit_mb'] * 1048576 * .5
        used = self.bytes('daily_archives')
        for day, size in self.db.execute('SELECT day,bytes FROM daily_archives ORDER BY day').fetchall():
            if used <= daily_limit:
                break
            size += self.db.execute('SELECT COALESCE(SUM(bytes),0) FROM daily_entries WHERE day=?', (day,)).fetchone()[0]
            self._delete_day(day)
            used -= size
        if self.bytes('visits') < s['limit_mb'] * 1048576 * .35 * .9:
            self.set_meta('fallback', False)

    def stats(self):
        return {table: {'count': self.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 'bytes': self.bytes(table)} for table in ('visits', 'snapshots', 'bookmarks', 'daily_archives')}

    def listing(self, view, query='', day='', limit=500):
        if view == 'daily':
            result = []
            for rid, folder_day, eid, locked in self.db.execute('SELECT rowid,day,primary_entry,primary_locked FROM daily_archives ORDER BY day DESC').fetchall():
                if day and folder_day != day:
                    continue
                primary = self.daily_entry(eid)
                children = [self.daily_entry(r[0]) for r in self.db.execute('SELECT id FROM daily_entries WHERE day=? AND id!=? ORDER BY last_seen DESC,id DESC', (folder_day, eid))]
                if primary and query.lower() in json.dumps([folder_day, primary, children], ensure_ascii=False).lower():
                    result.append({**primary, 'id': rid, 'entry_id': eid, 'is_day': True, 'primary_locked': bool(locked), 'children': children})
            return result[:limit]
        if view in ('snapshots', 'daily'):
            table = 'daily_archives' if view == 'daily' else 'snapshots'
            rows = self.db.execute(f'SELECT rowid,last_seen,day,payload,note FROM {table} ORDER BY last_seen DESC').fetchall()
            return [{'id': r[0], 'time': r[1], 'day': r[2], **json.loads(r[3]), 'note': r[4]} for r in rows if (not day or r[2] == day) and query.lower() in (r[3] + r[4]).lower()][:limit]
        if view == 'visits':
            rows = self.db.execute('SELECT rowid,day,browser,url,title,last_seen,count,note FROM visits ORDER BY last_seen DESC').fetchall()
            return [{'id': r[0], 'day': r[1], 'browser': r[2], 'url': r[3], 'title': r[4], 'time': r[5], 'count': r[6], 'note': r[7]} for r in rows if (not day or r[1] == day) and query.lower() in ' '.join(map(str, r[2:5] + r[7:])).lower()][:limit]
        if view == 'bookmarks':
            rows = self.db.execute('SELECT rowid,url,title,paths,sources,note FROM bookmarks ORDER BY title').fetchall()
            return [{'id': r[0], 'url': r[1], 'title': r[2], 'paths': json.loads(r[3]), 'sources': json.loads(r[4]), 'note': r[5]} for r in rows if query.lower() in ' '.join(map(str, r[1:])).lower()][:limit]
        return []

    def merge_bookmarks(self, rows):
        added = 0
        with self.db:
            for r in rows:
                url = r.get('url', '')
                if not safe_url(url):
                    continue
                old = self.db.execute('SELECT paths,sources,note FROM bookmarks WHERE url=?', (url,)).fetchone()
                paths = sorted(set(r.get('paths', []) + (json.loads(old[0]) if old else [])))
                sources = sorted(set(r.get('sources', []) + (json.loads(old[1]) if old else [])))
                title, note = r.get('title', url)[:2000], (old[2] if old else r.get('note', ''))[:2000]
                size = len(json.dumps([url, title, paths, sources, note], ensure_ascii=False).encode()) + 128
                self.db.execute('INSERT OR REPLACE INTO bookmarks VALUES(?,?,?,?,?,?)', (url, title, json.dumps(paths, ensure_ascii=False), json.dumps(sources, ensure_ascii=False), note, size))
                added += int(not old)
            if self.bytes('bookmarks') > self.settings()['limit_mb'] * 1048576 * .15:
                raise ValueError('收藏超过预留容量，本次导入已回滚，请提高容量上限。')
        return added

    def note(self, view, rid, value):
        if view == 'daily':
            row = self.db.execute('SELECT primary_entry FROM daily_archives WHERE rowid=?', (rid,)).fetchone()
            if row:
                return self.note('daily_entries', row[0], value)
        if view == 'daily_entry':
            view = 'daily_entries'
        view = 'daily_archives' if view == 'daily' else view
        if view not in ('visits', 'snapshots', 'bookmarks', 'daily_archives', 'daily_entries'):
            return
        with self.db:
            old = self.db.execute(f'SELECT note,bytes FROM {view} WHERE rowid=?', (rid,)).fetchone()
            if old:
                value = value[:2000]
                size = old[1] - len(old[0].encode()) + len(value.encode())
                self.db.execute(f'UPDATE {view} SET note=?,bytes=? WHERE rowid=?', (value, size, rid))
                if view == 'daily_entries':
                    self.db.execute('UPDATE daily_archives SET note=?,bytes=bytes-?+? WHERE primary_entry=?', (value, len(old[0].encode()), len(value.encode()), rid))

    def delete(self, view, rid):
        if view in ('daily', 'daily_entry'):
            with self.db:
                if view == 'daily':
                    row = self.db.execute('SELECT day FROM daily_archives WHERE rowid=?', (rid,)).fetchone()
                    if row:
                        self._delete_day(row[0])
                else:
                    row = self.db.execute('SELECT day FROM daily_entries WHERE id=?', (rid,)).fetchone()
                    if row:
                        primary = self.db.execute('SELECT primary_entry FROM daily_archives WHERE day=?', (row[0],)).fetchone()
                        self.db.execute('DELETE FROM daily_entries WHERE id=?', (rid,))
                        if primary and primary[0] == rid:
                            alternative = self.db.execute('SELECT id FROM daily_entries WHERE day=? ORDER BY last_seen DESC LIMIT 1', (row[0],)).fetchone()
                            if alternative:
                                self.primary_daily(alternative[0])
                            else:
                                self._delete_day(row[0])
            return
        view = 'daily_archives' if view == 'daily' else view
        if view not in ('visits', 'snapshots', 'bookmarks', 'daily_archives'):
            return
        with self.db:
            self.db.execute(f'DELETE FROM {view} WHERE rowid=?', (rid,))
            self.cleanup()

    def export(self, path):
        with self.db:
            result = {'format': 'browse-trace-desktop', 'version': 1, 'exported_at': time.time(), 'settings': self.settings()}
            for table in ('visits', 'snapshots', 'bookmarks', 'daily_archives'):
                cur = self.db.execute(f'SELECT * FROM {table}')
                columns = [c[0] for c in cur.description]
                result[table] = [dict(zip(columns, r)) for r in cur.fetchall()]
            result['daily_folders'] = []
            for root in result['daily_archives']:
                cur = self.db.execute('SELECT * FROM daily_entries WHERE day=?', (root['day'],))
                cols = [c[0] for c in cur.description]
                result['daily_folders'].append({'day': root['day'], 'primary_entry': root['primary_entry'], 'primary_locked': bool(root['primary_locked']), 'modified_at': root['modified_at'], 'entries': [dict(zip(cols, r)) for r in cur.fetchall()]})
        Path(path).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')

    def import_backup(self, path):
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        if value.get('format') != 'browse-trace-desktop' or value.get('version') != 1:
            raise ValueError('这不是浏览留痕桌面版备份。')
        for table in ('visits', 'snapshots', 'bookmarks'):
            if not isinstance(value.get(table), list):
                raise ValueError('备份格式不完整。')
        folders = value.get('daily_folders', [])
        if not isinstance(folders, list):
            raise ValueError('每日文件夹备份格式不完整。')
        folder_days = {folder['day'] for folder in folders}
        # A single transaction makes a rejected import leave the existing archive untouched.
        with self.db:
            for r in value['bookmarks']:
                if not safe_url(r.get('url')):
                    continue
                old = self.db.execute('SELECT paths,sources,note FROM bookmarks WHERE url=?', (r['url'],)).fetchone()
                paths = sorted(set(json.loads(r['paths']) + (json.loads(old[0]) if old else [])))
                sources = sorted(set(json.loads(r['sources']) + (json.loads(old[1]) if old else [])))
                title, note = str(r['title'])[:2000], str(old[2] if old else r.get('note', ''))[:2000]
                size = len(json.dumps([r['url'], title, paths, sources, note], ensure_ascii=False).encode()) + 128
                self.db.execute('INSERT OR REPLACE INTO bookmarks VALUES(?,?,?,?,?,?)', (r['url'], title, json.dumps(paths), json.dumps(sources), note, size))
            for r in value['visits']:
                if not safe_url(r.get('url')) or r.get('browser') not in ('Chrome', 'Edge'):
                    continue
                title, note = str(r['title'])[:2000], str(r.get('note', ''))[:2000]
                size = len((r['url'] + title + note).encode()) + 128
                self.db.execute('''INSERT INTO visits(day,browser,url,title,first_seen,last_seen,count,note,bytes) VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(day,browser,url) DO UPDATE SET count=MAX(visits.count,excluded.count),last_seen=MAX(visits.last_seen,excluded.last_seen),note=CASE WHEN visits.note='' THEN excluded.note ELSE visits.note END''',
                                (r['day'], r['browser'], r['url'], title, float(r['first_seen']), float(r['last_seen']), max(1, int(r['count'])), note, size))
            for r in value['snapshots']:
                payload = json.loads(r['payload'])
                tabs = [{k: str(t.get(k, '') or '')[:50000] for k in ('browser', 'window', 'target', 'title', 'url', 'source')}
                        for t in payload['tabs'] if t.get('browser') in ('Chrome', 'Edge') and (not t.get('url') or safe_url(t.get('url')) or str(t.get('url', '')).startswith(('chrome:', 'edge:', 'about:', 'file:')))]
                if not tabs:
                    continue
                connected = sorted([b for b in payload.get('connected', []) if b in ('Chrome', 'Edge')])
                clean = sorted(tabs, key=lambda t: (t['browser'], t['window'], t['target']))
                verified = payload.get('complete') is True and len(tabs) == len(payload['tabs']) and complete_tabs(clean, connected)
                clean_payload = {'tabs': clean, 'connected': connected, 'complete': verified}
                signature = hashlib.sha256(json.dumps(clean_payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                data = json.dumps(clean_payload, ensure_ascii=False)
                if not self.db.execute('SELECT 1 FROM snapshots WHERE time=? AND fingerprint=?', (float(r['time']), signature)).fetchone():
                    self.db.execute('INSERT INTO snapshots(time,last_seen,day,fingerprint,payload,bytes,note) VALUES(?,?,?,?,?,?,?)',
                                    (float(r['time']), float(r['last_seen']), str(r['day']), signature, data, len(data.encode()) + 128, str(r.get('note', ''))[:2000]))
                if verified and day_key(float(r['last_seen'])) not in folder_days:
                    self._upsert_daily(float(r['last_seen']), signature, data)
            # Optional for compatibility with existing version-1 backups.
            daily = value.get('daily_archives', [])
            if not isinstance(daily, list):
                raise ValueError('每日存档备份格式不完整。')
            for r in daily:
                if r['day'] in folder_days:
                    continue
                data = json.loads(r['payload'])
                if data.get('complete') is True and complete_tabs(data.get('tabs', []), data.get('connected', [])):
                    self._upsert_daily(float(r['last_seen']), r['fingerprint'], r['payload'], str(r.get('note', '')))
            for folder in folders:
                day = folder['day']
                self._valid_day(day)
                existing = self.db.execute('SELECT primary_locked,modified_at FROM daily_archives WHERE day=?', (day,)).fetchone()
                newer = existing and existing[0] and existing[1] > float(folder.get('modified_at', 0))
                mapping = {}
                for entry in folder['entries']:
                    data = json.loads(entry['payload'])
                    if not data.get('tabs') or any(t.get('browser') not in ('Chrome','Edge') or (t.get('url') and not (safe_url(t['url']) or t['url'].startswith(('chrome:','edge:','about:','file:')))) for t in data['tabs']):
                        raise ValueError('每日快照包含无效记录，本次导入已回滚。')
                    if entry['origin'] == 'auto':
                        self._upsert_daily(float(entry['last_seen']), entry['fingerprint'], entry['payload'], entry.get('note',''))
                        found = self.db.execute("SELECT id FROM daily_entries WHERE day=? AND origin='auto'", (day,)).fetchone()
                    else:
                        found = self.db.execute("SELECT id FROM daily_entries WHERE day=? AND last_seen=? AND fingerprint=? AND origin='manual'", (day, float(entry['last_seen']), entry['fingerprint'])).fetchone()
                        if not found:
                            found = (self._insert_entry(day, float(entry['last_seen']), entry['fingerprint'], entry['payload'], entry.get('note','')),)
                    if found:
                        mapping[entry['id']] = found[0]
                if not newer and folder['primary_entry'] in mapping:
                    chosen = mapping[folder['primary_entry']]
                    if folder['primary_locked']:
                        self.db.execute("UPDATE daily_entries SET origin='manual' WHERE id=?", (chosen,))
                    self._set_primary(day, chosen, folder['primary_locked'], float(folder.get('modified_at', 0)))
            s = self.settings()
            for table, fraction in [('visits', .35), ('snapshots', .5), ('bookmarks', .15)]:
                used = self.bytes(table) + (self.bytes('daily_archives') if table == 'snapshots' else 0)
                if used > s['limit_mb'] * 1048576 * fraction:
                    raise ValueError('备份超出容量，本次导入已回滚，请提高上限后重试。')
