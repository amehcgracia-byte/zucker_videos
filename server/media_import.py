"""Atomic, content-deduplicated imports into the selected media storage."""
from __future__ import annotations
import hashlib
import json
import re
import threading
import uuid
from pathlib import Path

_LOCK = threading.Lock()


def save_media_upload(storage, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / ('.incoming-' + uuid.uuid4().hex + '.part')
    try:
        digest = hashlib.sha256()
        with temporary.open('xb') as output:
            while chunk := storage.stream.read(4*1024*1024):
                digest.update(chunk)
                output.write(chunk)
        key = digest.hexdigest()
        with _LOCK:
            index_path = root / 'upload-index.json'
            index = json.loads(index_path.read_text()) if index_path.is_file() else {}
            item = index.get(key)
            if item:
                existing = root / item['name']
                if existing.is_file() and existing.parent == root and existing.stat().st_size == item['bytes'] and existing.stat().st_mtime_ns == item['mtime_ns']:
                    return existing
            name = Path((storage.filename or 'upload.bin').replace('\\','/')).name
            name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', name).strip(' .')[:180] or 'upload.bin'
            destination = root / name
            counter = 0
            while destination.exists():
                h = hashlib.sha256()
                if not destination.is_symlink():
                    with destination.open('rb') as src:
                        while chunk := src.read(4*1024*1024): h.update(chunk)
                    if h.hexdigest() == key:
                        break
                counter += 1
                destination = root / (Path(name).stem + '-' + str(counter) + Path(name).suffix)
            else:
                temporary.replace(destination)
            stat = destination.stat()
            index[key] = {'name': destination.name, 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
            pending = index_path.with_suffix('.tmp')
            pending.write_text(json.dumps(index, indent=2)+'\n')
            pending.replace(index_path)
            return destination
    finally:
        temporary.unlink(missing_ok=True)
