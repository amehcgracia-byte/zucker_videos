"""Project-owned creative assets and recoverable, cache-only maintenance.

Originals are never cleanup candidates. The optional cross-project catalogue stores
project locations only, not duplicate media. Imports stream directly to the project.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from core.project import atomic_write_json
from core.storage import require_available
from core.trash import move_to_trash

_LOCK = threading.RLock()
EXTENSIONS = {
    '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.webp': 'image',
    '.mp4': 'video', '.mov': 'video', '.webm': 'video', '.m4v': 'video',
    '.wav': 'audio', '.mp3': 'audio', '.m4a': 'audio', '.ogg': 'audio', '.flac': 'audio',
}


@contextmanager
def _locked(folder: Path):
    """Serialize manifest updates across threads and desktop processes on both OSes."""
    with _LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / '.library.lock').open('a+b') as handle:
            handle.seek(0)
            if not handle.read(1):
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ProjectLibrary:
    def __init__(self, project_folder: Path):
        self.project = require_available(Path(project_folder).absolute())
        if not (self.project / 'project.json').is_file():
            raise ValueError('Open a saved project first.')
        self.root = self.project / 'library'
        if self.root.is_symlink():
            raise ValueError('The project library must stay inside the project.')
        self.manifest = self.root / 'manifest.json'

    def _read(self):
        if not self.manifest.exists():
            return {'version': 1, 'assets': {}}
        data = json.loads(self.manifest.read_text(encoding='utf-8'))
        if data.get('version') != 1 or not isinstance(data.get('assets'), dict):
            raise ValueError('Unsupported library manifest; no files were changed.')
        return data

    def _path(self, relative: str) -> Path:
        path = self.root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('Library path escapes the project.')
        return path

    def import_stream(self, stream: BinaryIO, filename: str, *, origin: str = '') -> dict:
        suffix = Path(filename).suffix.lower()
        if suffix not in EXTENSIONS:
            raise ValueError('Choose a supported image, video or audio file.')
        with _locked(self.root):
            data = self._read()
            objects = self._path('assets')
            objects.mkdir(exist_ok=True)
            fd, raw = tempfile.mkstemp(prefix='.import-', suffix='.part', dir=objects)
            temporary = Path(raw)
            try:
                digest = hashlib.sha256()
                size = 0
                with os.fdopen(fd, 'wb') as out:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        out.write(chunk)
                        size += len(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                if not size:
                    raise ValueError('The selected file is empty.')
                key = digest.hexdigest()
                existing = data['assets'].get(key)
                if existing and self._path(existing['path']).is_file():
                    with self._path(existing['path']).open('rb') as saved:
                        valid = hashlib.file_digest(saved, 'sha256').hexdigest() == key
                    if valid:
                        return {**existing, 'id': key, 'reused': True}
                relative = f'assets/{key}{suffix}'
                temporary.replace(self._path(relative))
                record = {'path': relative, 'bytes': size, 'kind': EXTENSIONS[suffix],
                          'name': Path(filename).name, 'origin': origin, 'sha256': key}
                data['assets'][key] = record
                atomic_write_json(self.manifest, data)
                return {**record, 'id': key, 'reused': False}
            finally:
                temporary.unlink(missing_ok=True)

    def inventory(self) -> dict:
        data = self._read()
        assets = [{**a, 'id': key, 'available': self._path(a['path']).is_file()}
                  for key, a in data['assets'].items()]
        # Only this component's disposable preview directory is considered.
        # Never walk inputs, exports, arbitrary caches or original asset objects.
        previews = self._path('previews')
        candidates = []
        if previews.is_dir() and not previews.is_symlink():
            for p in previews.iterdir():
                if p.is_file() and not p.is_symlink():
                    candidates.append({'path': str(p.relative_to(self.root)), 'bytes': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns})
        candidates.sort(key=lambda a: a['path'])
        token = hashlib.sha256(json.dumps(candidates, sort_keys=True).encode()).hexdigest()
        return {'folder': str(self.root), 'assets': assets,
                'asset_bytes': sum(a['bytes'] for a in assets if a['available']),
                'cleanup': candidates, 'cleanup_bytes': sum(a['bytes'] for a in candidates),
                'cleanup_token': token, 'policy': 'Original assets are protected; only disposable library previews can be cleaned.'}

    def clean_previews(self, token: str) -> dict:
        with _locked(self.root):
            report = self.inventory()
            if token != report['cleanup_token']:
                raise ValueError('Storage changed. Review the cleanup preview again.')
            moved = []
            for item in report['cleanup']:
                destination = move_to_trash(self._path(item['path']))
                if destination:
                    moved.append({**item, 'trash_path': str(destination)})
            return {'moved': moved, 'moved_bytes': sum(a['bytes'] for a in moved),
                    'note': 'Moved to Trash on the same disk. Disk space is reclaimed only when that Trash is emptied.'}


def remember_project(index_path: Path, project_folder: Path):
    """A small list of project locations; never copies the asset library globally."""
    with _locked(index_path.parent):
        data = json.loads(index_path.read_text()) if index_path.exists() else {'projects': []}
        folder = str(project_folder.absolute())
        data['projects'] = list(dict.fromkeys([folder, *data['projects']]))
        atomic_write_json(index_path, data)


def catalogue(index_path: Path) -> list[dict]:
    if not index_path.is_file():
        return []
    results = []
    for folder in json.loads(index_path.read_text()).get('projects', []):
        try:
            report = ProjectLibrary(Path(folder)).inventory()
            results.extend({**a, 'project': folder} for a in report['assets'] if a['available'])
        except (OSError, ValueError, KeyError):
            continue  # Offline projects remain indexed, without relocating their files.
    return results


def reuse_asset(target: ProjectLibrary, index_path: Path, source_project: str, asset_id: str) -> dict:
    if not any(a['project'] == source_project and a['id'] == asset_id for a in catalogue(index_path)):
        raise ValueError('The source project is unavailable or not in the catalogue.')
    source = ProjectLibrary(Path(source_project))
    record = source._read()['assets'][asset_id]
    with source._path(record['path']).open('rb') as stream:
        # Validate identity before publishing a copy, not by rounded file timestamps.
        if hashlib.file_digest(stream, 'sha256').hexdigest() != asset_id:
            raise ValueError('The source asset changed; import the new file explicitly.')
        stream.seek(0)
        return target.import_stream(stream, record['name'], origin=record.get('origin', ''))
