"""Chosen media storage and project locations; never fall back from an offline disk."""
from __future__ import annotations
import json
import os
from pathlib import Path


def preference_path() -> Path:
    return Path.home() / '.config' / 'ZuckerEditor' / 'storage.json'


def require_available(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        raise OSError('Choose an absolute storage location.')
    parts = path.parts
    if len(parts) > 2 and parts[1] == 'Volumes' and not Path('/Volumes', parts[2]).is_mount():
        raise OSError(f'Connect the storage disk: {parts[2]}')
    if path.is_symlink() and not path.exists():
        raise OSError('Connect the storage disk or choose another location.')
    return path


def data_root() -> Path:
    pref = preference_path()
    if os.environ.get("ZUCKER_DATA_ROOT"):
        root = Path(os.environ["ZUCKER_DATA_ROOT"])
    elif pref.exists():
        root = Path(json.loads(pref.read_text(encoding='utf-8'))['data_root'])
    else:
        root = Path.home() / 'ZuckerVideos'
    return require_available(root)


def storage_ready() -> bool:
    try:
        return preference_path().is_file() and data_root().is_dir()
    except (OSError, ValueError, KeyError):
        return False


def save_data_root(path: str) -> Path:
    root = require_available(Path(path))
    root.mkdir(parents=True, exist_ok=True)
    if not os.access(root, os.W_OK):
        raise OSError('The selected storage location is not writable.')
    pref = preference_path()
    pref.parent.mkdir(parents=True, exist_ok=True)
    temporary = pref.with_suffix('.tmp')
    try:
        previous = json.loads(pref.read_text(encoding='utf-8')) if pref.is_file() else {}
    except (OSError, ValueError):
        previous = {}
    history = [*previous.get('previous_roots', []), previous.get('data_root'), str(Path.home()/'ZuckerVideos')]
    history = list(dict.fromkeys(p for p in history if p and p != str(root)))
    temporary.write_text(json.dumps({'data_root': str(root), 'previous_roots': history}, indent=2)+'\n', encoding='utf-8')
    temporary.replace(pref)
    return root


def project_locations() -> list[Path]:
    root = data_root()
    pref = preference_path()
    settings = json.loads(pref.read_text(encoding='utf-8')) if pref.is_file() else {}
    paths = [root / 'Projects']
    for candidate in [root, *[Path(p) for p in settings.get('previous_roots', [])]]:
        try:
            candidate = require_available(candidate)
        except OSError:
            continue
        config_path = candidate / 'config.json'
        config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.is_file() else {}
        paths.append(candidate / 'Projects')
        paths.extend(Path(p) for p in config.get('project_roots', []) if isinstance(p, str))
        if config.get('project_root'):
            paths.append(Path(config['project_root']))
    return list(dict.fromkeys(paths))


def _verified_migration_record(path: Path) -> dict | None:
    """Keep verified media cache identities across an exFAT relocation.

    exFAT rounds modification times. Trust a migration record only while the
    copied file still has its verified size and destination modification time.
    """
    root = data_root().resolve()
    resolved = path.resolve()
    try:
        relative = str(resolved.relative_to(root))
    except ValueError:
        relative = ''
    manifest = root / 'migration-signatures.json'
    if not manifest.is_file():
        return None
    records = _migration_records(str(manifest), manifest.stat().st_mtime_ns)
    record = records.get(str(resolved)) or records.get(relative)
    if not record:
        return None
    stat = resolved.stat()
    if stat.st_size != record['bytes'] or stat.st_mtime_ns != record['destination_mtime_ns']:
        return None
    return record


def migrated_identity(path: Path) -> tuple[str, float] | None:
    record = _verified_migration_record(path)
    return (record['identity_path'], record['source_mtime']) if record else None


def cache_file_signature(path: Path) -> dict:
    identity = migrated_identity(path)
    return {'path': identity[0] if identity else str(path.resolve()), **media_signature(path)}


def cache_mtime_ns(path: Path) -> int:
    record = _verified_migration_record(path)
    return record.get('source_mtime_ns', path.stat().st_mtime_ns) if record else path.stat().st_mtime_ns


def media_signature(path: Path) -> dict:
    stat = path.stat()
    identity = migrated_identity(path)
    return {'size': stat.st_size, 'mtime': identity[1] if identity else stat.st_mtime}


from functools import lru_cache

@lru_cache(maxsize=2)
def _migration_records(path: str, modified: int) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def configure_working_storage() -> Path:
    """Keep library caches and media scratch files on the selected disk."""
    import tempfile
    root = data_root() / 'Cache'
    temporary = root / 'temporary' / str(os.getpid())
    temporary.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(temporary)
    for key, value in {'TMPDIR': temporary, 'TMP': temporary, 'TEMP': temporary,
                       'HF_HOME': root/'huggingface', 'NUMBA_CACHE_DIR': root/'numba',
                       'MPLCONFIGDIR': root/'matplotlib', 'XDG_CACHE_HOME': root/'libraries'}.items():
        os.environ[key] = str(value)
    return temporary


def whisper_model_path(name: str) -> str:
    if Path(name).is_dir():
        return name
    from faster_whisper.utils import download_model
    folder = data_root() / 'Cache' / 'WhisperModels' / name.replace('/', '--')
    if all((folder / file).is_file() for file in ('model.bin','config.json','tokenizer.json')) and any(folder.glob('vocabulary.*')):
        return str(folder)
    return download_model(name, output_dir=str(folder), cache_dir=str(data_root()/'Cache'/'huggingface'/'hub'))
