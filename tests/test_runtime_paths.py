import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime_paths import resolve_paths, prepare_runtime, launcher_text, startup_command


class RuntimePathTests(unittest.TestCase):
    def test_saved_relative_paths_follow_application(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / 'ae-keeper-paths.json').write_text(json.dumps({'data_dir': 'data', 'temp_dir': 'temp'}))
            paths = resolve_paths(executable=base / 'app.exe', environ={'LOCALAPPDATA': 'unused'})
            self.assertEqual(paths['data'], base.resolve() / 'data')
            self.assertEqual(paths['temp'], base.resolve() / 'temp')

    def test_explicit_path_and_environment_override_saved_path(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / 'ae-keeper-paths.json').write_text(json.dumps({'data_dir': 'old', 'temp_dir': 'old-temp'}))
            env = {'AE_KEEPER_DATA_DIR': str(base / 'env')}
            paths = resolve_paths(base / 'explicit', executable=base / 'app.exe', environ=env)
            self.assertEqual(paths['data'], base.resolve() / 'explicit')
            self.assertEqual(paths['temp'], base.resolve() / 'temp')
            self.assertEqual(resolve_paths(executable=base / 'app.exe', environ=env)['data'], base.resolve() / 'env')

    def test_invalid_config_does_not_fall_back_to_legacy(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / 'ae-keeper-paths.json').write_text('{broken')
            with self.assertRaises(ValueError):
                resolve_paths(executable=base / 'app.exe', environ={'LOCALAPPDATA': str(base / 'legacy')})
            self.assertFalse((base / 'legacy').exists())

    def test_unwritable_target_does_not_create_another_archive(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            blocked = base / 'not-a-directory'
            blocked.write_text('preserve')
            with self.assertRaises(OSError):
                prepare_runtime({'data': blocked / 'data', 'temp': base / 'temp', 'exports': base / 'exports'})
            self.assertEqual(blocked.read_text(), 'preserve')
            self.assertFalse((base / 'exports').exists())

    def test_all_child_temporary_files_use_selected_disk(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            paths = {key: base / key for key in ('data', 'temp', 'exports')}
            old_temp = tempfile.tempdir
            try:
                with patch.dict(os.environ):
                    prepare_runtime(paths)
                    with tempfile.TemporaryDirectory() as generated:
                        self.assertEqual(Path(generated).parent, paths['temp'])
                    self.assertEqual(os.environ['TEMP'], str(paths['temp']))
                    self.assertEqual(os.environ['TMP'], str(paths['temp']))
            finally:
                tempfile.tempdir = old_temp

    def test_startup_uses_pre_extraction_launcher_if_present(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'BrowseTrace.exe'
            self.assertEqual(startup_command(exe), f'"{exe.resolve()}" --tray')
            (exe.parent / 'launch.vbs').write_text(launcher_text())
            self.assertIn('wscript.exe //B', startup_command(exe))
            text = launcher_text()
            self.assertLess(text.index('env("TEMP")'), text.index('sh.Run'))
            self.assertIn('"--tray"', text)


if __name__ == '__main__':
    unittest.main()
