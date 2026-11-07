"""Explicit data locations; a configured disk never falls back silently."""
import json
import os
from pathlib import Path
import sys
import tempfile

CONFIG_NAME = 'ae-keeper-paths.json'


def resolve_paths(data_dir=None, *, executable=None, environ=None):
    env = os.environ if environ is None else environ
    exe = Path(executable or sys.executable).resolve()
    config_file = exe.parent / CONFIG_NAME
    config = {}
    if config_file.is_file():
        config = json.loads(config_file.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('保存位置配置必须是一个对象。')
    value = data_dir or env.get('AE_KEEPER_DATA_DIR') or config.get('data_dir')
    if value:
        if not isinstance(value, (str, Path)):
            raise ValueError('档案目录无效。')
        folder = Path(value).expanduser()
        if not folder.is_absolute():
            base = Path.cwd() if data_dir or env.get('AE_KEEPER_DATA_DIR') else exe.parent
            folder = base / folder
    else:
        folder = Path(env.get('LOCALAPPDATA', str(Path.home()))) / 'BrowseTraceDesktop/data'
    folder = folder.resolve()
    temp_value = config.get('temp_dir') if not data_dir and not env.get('AE_KEEPER_DATA_DIR') else None
    if temp_value:
        if not isinstance(temp_value, str):
            raise ValueError('临时目录无效。')
        temp = Path(temp_value).expanduser()
        if not temp.is_absolute():
            temp = exe.parent / temp
    else:
        temp = folder.parent / 'temp'
    return {'data': folder, 'temp': temp.resolve(), 'exports': folder.parent / 'exports'}


def prepare_runtime(paths):
    for key in ('data', 'temp', 'exports'):
        paths[key].mkdir(parents=True, exist_ok=True)
    for key in ('data', 'temp'):
        with tempfile.TemporaryFile(dir=paths[key]) as probe:
            probe.write(b'AE Keeper')
            probe.flush()
    for key in ('TEMP', 'TMP', 'TMPDIR'):
        os.environ[key] = str(paths['temp'])
    tempfile.tempdir = str(paths['temp'])


def launcher_text():
    """Set TEMP before the one-file bootloader extracts its assets."""
    return '''Option Explicit
Dim sh, fs, base, env, command, arg
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
base = fs.GetParentFolderName(WScript.ScriptFullName)
If Not fs.FolderExists(base & "\\temp") Then fs.CreateFolder(base & "\\temp")
Set env = sh.Environment("PROCESS")
env("TEMP") = base & "\\temp"
env("TMP") = base & "\\temp"
command = Chr(34) & base & "\\BrowseTrace.exe" & Chr(34)
For Each arg In WScript.Arguments
  If arg = "--tray" Or arg = "--workspaces" Then command = command & " " & arg
Next
sh.CurrentDirectory = base
sh.Run command, 1, False
'''


def startup_command(executable=None):
    exe = Path(executable or sys.executable).resolve()
    launcher = exe.parent / 'launch.vbs'
    if launcher.is_file():
        return f'wscript.exe //B "{launcher}" --tray'
    return f'"{exe}" --tray'
