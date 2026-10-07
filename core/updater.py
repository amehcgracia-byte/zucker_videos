"""Verified GitHub-release updates, staged before the running app exits."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from urllib.parse import urlparse
from core.build_info import build_info
from core.storage import data_root

REPOSITORY = 'amehcgracia-byte/zucker_videos'
RELEASE_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'


def version_tuple(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', version)
    if not match:
        raise ValueError('Invalid release version')
    return tuple(map(int, match.groups()))


def release_asset(release: dict, system: str, current: str) -> dict | None:
    tag = str(release.get('tag_name') or '')
    if release.get('draft') or release.get('prerelease') or version_tuple(tag) <= version_tuple(current):
        return None
    extension = '.dmg' if system == 'darwin' else '-windows.zip' if system == 'win32' else None
    if not extension:
        return None
    assets = [item for item in release.get('assets', []) if item.get('state') == 'uploaded'
              and str(item.get('name', '')).lower().endswith(extension)]
    if len(assets) != 1:
        raise ValueError('The release does not have a unique installer for this computer')
    asset = assets[0]
    parsed = urlparse(asset.get('browser_download_url', ''))
    if parsed.scheme != 'https' or parsed.netloc != 'github.com' or not parsed.path.startswith(f'/{REPOSITORY}/releases/download/{tag}/'):
        raise ValueError('Unexpected installer URL')
    digest = str(asset.get('digest') or '')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('The release installer has no verified SHA-256 digest')
    return {**asset, 'version': tag.lstrip('v')}


def download_asset(asset: dict, destination: Path, progress) -> None:
    digest = hashlib.sha256()
    received = 0
    expected = int(asset['size'])
    request = urllib.request.Request(asset['browser_download_url'], headers={'User-Agent': 'Zucker-Editor-Updater'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response, destination.open('xb') as output:
            while block := response.read(1024 * 1024):
                received += len(block)
                if received > expected:
                    raise ValueError('Installer download exceeded its published size')
                output.write(block); digest.update(block)
                progress(min(99, int(received / max(1, expected) * 100)))
        if received != expected or digest.hexdigest() != asset['digest'].split(':')[1]:
            raise ValueError('Installer verification failed; the installed app has not been changed')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def extract_windows(archive: Path, destination: Path, version: str) -> Path:
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            name = item.filename.replace('\\', '/')
            path = Path(name)
            if path.is_absolute() or '..' in path.parts or ':' in name or ((item.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('Unsafe installer archive')
        package.extractall(destination)
    executables = [path for path in (destination / 'Zucker Editor.exe', destination / f'Zucker Editor {version}.exe') if path.is_file()]
    if len(executables) != 1: raise ValueError('Windows installer executable is missing or ambiguous')
    exe = executables[0]
    metadata = destination / '_internal' / 'build_info.json'
    if not exe.is_file() or not metadata.is_file() or json.loads(metadata.read_text(encoding='utf-8-sig'))['version'] != version:
        raise ValueError('Windows installer version or resources are incomplete')
    return exe


def mac_helper(pid: int, installed: Path, staged: Path, backup: Path) -> str:
    import shlex
    q = shlex.quote
    return f'''#!/bin/bash
set -eu
for attempt in {{1..60}}; do
  if ! kill -0 {pid} 2>/dev/null; then break; fi
  sleep 1
done
if kill -0 {pid} 2>/dev/null; then exit 1; fi
mv {q(str(installed))} {q(str(backup))}
if ! mv {q(str(staged))} {q(str(installed))}; then mv {q(str(backup))} {q(str(installed))}; exit 1; fi
if ! /usr/bin/open -a {q(str(installed))}; then
  mv {q(str(installed))} {q(str(staged))}
  mv {q(str(backup))} {q(str(installed))}
  /usr/bin/open -a {q(str(installed))}
  exit 1
fi
'''


def windows_helper(pid: int, installed: Path, staged: Path, backup: Path, executable: str) -> str:
    def quote(value): return "'" + str(value).replace("'", "''") + "'"
    return f'''$ErrorActionPreference = 'Stop'
$process = Get-Process -Id {pid} -ErrorAction SilentlyContinue
if ($process) {{ if (-not $process.WaitForExit(60000)) {{ throw 'Application did not close' }} }}
Move-Item -LiteralPath {quote(installed)} -Destination {quote(backup)}
try {{
 Move-Item -LiteralPath {quote(staged)} -Destination {quote(installed)}
 Start-Process -FilePath {quote(installed / executable)}
}} catch {{
 if (Test-Path -LiteralPath {quote(installed)}) {{ Move-Item -LiteralPath {quote(installed)} -Destination {quote(staged)} }}
 Move-Item -LiteralPath {quote(backup)} -Destination {quote(installed)}
 throw
}}
'''


class UpdateManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.state = {'status': 'idle', 'percent': None, 'current_version': build_info()['version']}
        self.token = secrets.token_urlsafe(32)
        self.asset = None
        self.prepared = None

    def snapshot(self):
        with self.lock: return {**self.state, 'token': self.token}

    def set_state(self, **values):
        with self.lock: self.state.update(values)

    def check(self):
        with self.lock:
            if self.state['status'] != 'idle': return self.snapshot()
            self.state['status'] = 'checking'
        threading.Thread(target=self._check, daemon=True, name='zucker-update-check').start()
        return self.snapshot()

    def _check(self):
        try:
            self.cleanup_completed_update()
            request = urllib.request.Request(RELEASE_API, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'Zucker-Editor-Updater'})
            with urllib.request.urlopen(request, timeout=8) as response:
                release = json.load(response)
            asset = release_asset(release, sys.platform, build_info()['version'])
            self.asset = asset
            self.set_state(status='available' if asset else 'up_to_date', version=asset['version'] if asset else None)
        except Exception as exc:
            self.set_state(status='unavailable', error=str(exc))

    def download(self):
        with self.lock:
            if self.state['status'] not in {'available', 'failed'} or not self.asset:
                raise ValueError('No update is available')
            if not getattr(sys, 'frozen', False):
                raise ValueError('Automatic installation is available in the installed desktop app')
            self.state.update(status='downloading', percent=0)
        threading.Thread(target=self._prepare, daemon=True, name='zucker-update-download').start()

    def _prepare(self):
        staged = None
        folder = None
        try:
            root = data_root() / 'Cache' / 'Updates'; root.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix='update-', dir=root))
            asset = self.asset
            download = folder / ('installer.dmg' if sys.platform == 'darwin' else 'installer.zip')
            download_asset(asset, download, lambda percent: self.set_state(percent=percent))
            self.set_state(status='preparing', percent=None)
            installed = Path(sys.executable).resolve().parents[2] if sys.platform == 'darwin' else Path(sys.executable).resolve().parent
            if (sys.platform == 'darwin' and installed.suffix != '.app') or not os.access(installed.parent, os.W_OK):
                raise ValueError('The application folder is not writable. Move Zucker Editor to a folder you can update.')
            staged = installed.with_name('.Zucker-update-' + secrets.token_hex(8) + ('.app' if sys.platform == 'darwin' else ''))
            backup = installed.with_name('.Zucker-backup-' + secrets.token_hex(8) + ('.app' if sys.platform == 'darwin' else ''))
            if sys.platform == 'darwin':
                mount = folder / 'mount'; mount.mkdir()
                subprocess.run(['hdiutil','attach',str(download),'-readonly','-nobrowse','-mountpoint',str(mount)],check=True,capture_output=True,timeout=120)
                try:
                    app = mount / 'Zucker Editor.app'
                    info = json.loads((app/'Contents/Resources/build_info.json').read_text())
                    if info['version'] != asset['version']: raise ValueError('Installer version mismatch')
                    subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True,capture_output=True,timeout=120)
                    subprocess.run(['ditto',str(app),str(staged)],check=True,capture_output=True,timeout=180)
                finally:
                    subprocess.run(['hdiutil','detach',str(mount)],check=True,capture_output=True,timeout=60)
                helper = folder/'apply.sh'
                helper.write_text(mac_helper(os.getpid(),installed,staged,backup))
                command = ['/bin/bash',str(helper)]
            else:
                staged.mkdir()
                exe = extract_windows(download,staged,asset['version'])
                # Keep existing Windows shortcuts valid across updates.
                launch_name = Path(sys.executable).name
                if exe.name != launch_name:
                    exe = exe.rename(staged / launch_name)
                helper = folder/'apply.ps1'; helper.write_text(windows_helper(os.getpid(),installed,staged,backup,exe.name),encoding='utf-8-sig')
                command = ['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(helper)]
            receipt = {'version':asset['version'], 'installed':str(installed),'staged':str(staged),'backup':str(backup),'folder':str(folder)}
            (root/'pending.json').write_text(json.dumps(receipt))
            self.prepared = (command, folder)
            self.set_state(status='ready',percent=100)
        except Exception as exc:
            if staged and staged.exists(): shutil.rmtree(staged)
            if folder and folder.exists(): shutil.rmtree(folder)
            self.set_state(status='failed',error=str(exc),percent=None)

    def apply(self):
        with self.lock:
            if self.state['status'] != 'ready' or not self.prepared: raise ValueError('The verified installer is not ready')
            command, folder = self.prepared
            with (folder/'install.log').open('ab') as log:
                subprocess.Popen(command,stdout=log,stderr=log,stdin=subprocess.DEVNULL,start_new_session=True)
            self.state['status'] = 'installing'
        # Exit only after the external helper has been launched successfully.
        threading.Timer(1.5, lambda: os._exit(0)).start()

    def cleanup_completed_update(self):
        root = data_root()/'Cache'/'Updates'; marker = root/'pending.json'
        if not marker.is_file(): return
        receipt = json.loads(marker.read_text())
        if receipt['version'] != build_info()['version']: return
        installed = Path(sys.executable).resolve().parents[2] if sys.platform == 'darwin' else Path(sys.executable).resolve().parent
        if str(installed) != receipt['installed']: return
        for key, prefix in [('backup','.Zucker-backup-'),('staged','.Zucker-update-')]:
            path = Path(receipt[key])
            if path.parent != installed.parent or not path.name.startswith(prefix) or path.is_symlink(): raise ValueError('Invalid update receipt')
            if path.exists(): shutil.rmtree(path)
        folder = Path(receipt['folder'])
        if folder.parent == root and folder.name.startswith('update-') and not folder.is_symlink(): shutil.rmtree(folder,ignore_errors=True)
        marker.unlink()
