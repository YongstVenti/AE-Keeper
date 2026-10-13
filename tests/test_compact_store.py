import base64
import copy
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from PIL import Image
from storage import Archive
from image_codec import encode_full
from workspace_model import clean_workspace, demo_workspace
from compact_store import read_image


class CompactArchiveTests(unittest.TestCase):
    def test_hd_lossless_keeps_pixels_and_never_upscales(self):
        source = Image.new('RGB', (900, 600), '#0d766d')
        source.paste('#eedd88', (12, 14, 333, 389))
        raw = encode_full(source, 1920, 'lossless')
        with Image.open(BytesIO(raw)) as restored:
            self.assertEqual(restored.size, source.size)
            self.assertEqual(restored.convert('RGB').tobytes(), source.tobytes())
        with Image.open(BytesIO(encode_full(Image.new('RGB', (3000, 2000)), 1920))) as small:
            self.assertEqual(small.size, (1920, 1280))

    def test_shared_images_and_browser_payloads_gc_only_unreferenced(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Archive(root)
            try:
                data = clean_workspace(demo_workspace())
                full = encode_full(Image.new('RGB', (1920, 1080), '#123456'))
                data['windows'][0]['preview']['full_image'] = base64.b64encode(full).decode()
                for _ in range(5):
                    archive.capture_workspace(data, manual=True)
                self.assertEqual(archive.db.execute("SELECT COUNT(*) FROM archive_objects WHERE kind='image'").fetchone()[0], 4)
                self.assertEqual(archive.db.execute("SELECT COUNT(*) FROM archive_objects WHERE kind='workspace'").fetchone()[0], 1)
                self.assertEqual(archive.listing('workspaces')[0]['payload'], data)
                rows = [dict(browser=b, window=str(i), target=str(i), title='示例', url='https://example.com/' + str(i)) for i, b in enumerate(('Edge', 'Chrome'))]
                archive.capture(rows, ['Edge', 'Chrome'])
                self.assertEqual(archive.db.execute("SELECT COUNT(*) FROM archive_objects WHERE kind='browser'").fetchone()[0], 1)
                rid = archive.listing('snapshots')[0]['id']
                archive.delete('snapshots', rid)
                self.assertEqual(len(archive.listing('daily')), 1)
                self.assertEqual(archive.db.execute("SELECT COUNT(*) FROM archive_objects WHERE kind='browser'").fetchone()[0], 1)
                for row in archive.listing('workspaces'):
                    archive.delete('workspaces', row['id'])
                self.assertEqual(archive.db.execute("SELECT COUNT(*) FROM archive_objects WHERE kind IN ('image','workspace')").fetchone()[0], 0)
            finally:
                archive.db.close()

    def test_legacy_migration_keeps_logical_records_and_verified_backup(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Archive(root)
            payload = clean_workspace(demo_workspace())
            archive.capture_workspace(payload, manual=True)
            original = archive.listing('workspaces')
            archive.db.close()
            # Construct a 0.4-style row without its newer write triggers.
            db = sqlite3.connect(Path(root) / 'archive.sqlite3')
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
                db.execute(f'DROP TRIGGER {name}')
            db.execute('UPDATE workspaces SET payload=?', (json.dumps(payload, ensure_ascii=False),))
            db.execute('DELETE FROM archive_objects'); db.execute('DELETE FROM archive_links')
            db.commit(); db.close()
            archive = Archive(root)
            try:
                self.assertEqual(archive.listing('workspaces'), original)
                self.assertEqual(archive.db.execute('SELECT typeof(payload) FROM workspaces').fetchone()[0], 'blob')
                backup = next(Path(root).glob('archive-before-compact-*.sqlite3'))
                db = sqlite3.connect(backup)
                try:
                    self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                    self.assertEqual(db.execute('SELECT typeof(payload) FROM workspaces').fetchone()[0], 'text')
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'compact_%'").fetchone()[0], 0)
                finally:
                    db.close()
                backup_path = Path(root) / 'portable.json'; archive.export(backup_path)
                second = Archive(Path(root) / 'second')
                try:
                    second.import_backup(backup_path)
                    actual = second.listing('workspaces')[0]['payload']['windows'][0]['preview']
                    self.assertEqual(actual, payload['windows'][0]['preview'])
                finally:
                    second.db.close()
            finally:
                archive.db.close()

    def test_invalid_hd_import_is_atomic_and_does_not_leave_objects(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Archive(root)
            try:
                data = demo_workspace(); archive.capture_workspace(data, manual=True)
                path = Path(root) / 'portable.json'; archive.export(path)
                before = archive.db.execute('SELECT COUNT(*) FROM archive_objects').fetchone()[0]
                value = json.loads(path.read_text(encoding='utf8'))
                image = json.loads(value['workspaces'][0]['payload'])
                image['windows'][0]['preview']['full_image'] = base64.b64encode(b'invalid').decode()
                value['workspaces'][0]['payload'] = json.dumps(image)
                path.write_text(json.dumps(value), encoding='utf8')
                with self.assertRaises(ValueError):
                    archive.import_backup(path)
                self.assertEqual(archive.db.execute('SELECT COUNT(*) FROM archive_objects').fetchone()[0], before)
            finally:
                archive.db.close()

    def test_hd_roundtrip_and_options(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Archive(root)
            try:
                archive.save_settings(dict(workspace_hd=True, workspace_hd_edge=2560, workspace_thumb_edge=480, workspace_image_mode='compact'))
                self.assertTrue(archive.settings()['workspace_hd'])
                data = demo_workspace()
                data['windows'][0]['preview']['full_image'] = base64.b64encode(encode_full(Image.new('RGB', (1920, 1080), '#abcdef'), mode='compact')).decode()
                archive.capture_workspace(data, manual=True)
                thin = archive.listing('workspaces', thumbnails_only=True)[0]['payload']['windows'][0]['preview']
                self.assertNotIn('full_image', thin)
                self.assertEqual(read_image(root, thin['full_image_ref']), base64.b64decode(data['windows'][0]['preview']['full_image']))
                path = Path(root) / 'portable.json'; archive.export(path)
                other = Archive(Path(root) / 'other')
                try:
                    other.import_backup(path)
                    self.assertEqual(other.listing('workspaces')[0]['payload']['windows'][0]['preview']['full_image'], data['windows'][0]['preview']['full_image'])
                finally:
                    other.db.close()
            finally:
                archive.db.close()
