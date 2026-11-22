"""#380 native Engine project transactions; explicitly opt-in, fail closed.

Not a public Owner authentication boundary: external Owner gateway MUST
authenticate session/CSRF and validate private paths before forwarding.
All enabled project mutation paths must use this lock; never enable on a
worker with uninstrumented mutation routes. No migration of legacy media.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path

from core.project import ProjectError, load_project, create_project, utc_now


class ProjectConflict380(ProjectError):
    pass


def title_key(value: str) -> str:
    if type(value) is not str:
        raise ValueError("Project title must be text")
    display=" ".join(value.split())
    if not display or len(display)>120 or any(ord(c)<32 for c in value):
        raise ValueError("Project title must have 1-120 printable characters")
    return unicodedata.normalize("NFKC", display).casefold()


def verified_project_folder(root: Path, path: str) -> Path:
    """Existing, non-symlinked immediate child of current project root."""
    if type(path) is not str or "\x00" in path or not path or len(path)>4096:
        raise ProjectError("Invalid project reference")
    root=Path(root).resolve(strict=True)
    supplied=Path(path).expanduser()
    if not supplied.is_absolute() or ".." in supplied.parts or "." in supplied.parts:
        raise ProjectError("Project path must be canonical and absolute")
    if (supplied.parent!=root or supplied.suffix!=".zuckervid" or
            supplied.is_symlink() or not supplied.is_dir() or
            (supplied/"project.json").is_symlink() or
            not (supplied/"project.json").is_file()):
        raise ProjectError("Project is not a direct native project in this root")
    if supplied.resolve(strict=True)!=supplied:
        raise ProjectError("Linked native project is forbidden")
    return supplied


@contextmanager
def project_root_guard(root: Path):
    """One filesystem-wide Engine lock for create/save/rename/copy.

    Root is selected by trusted Engine configuration, never request data.
    Serialize metadata and file copies. This conservative single lock is
    safe, though potentially slower; later per-project optimization needs
    proof that title reservations remain globally serialized.
    """
    root=Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ProjectError("Trusted native project root unavailable")
    lock=root/".montara-canonical-380.lock"
    flags=os.O_CREAT|os.O_RDWR
    if hasattr(os,"O_NOFOLLOW"):
        flags|=os.O_NOFOLLOW
    fd=os.open(lock,flags,0o600)
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or
                stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1):
            raise ProjectError("Native project lock is not private")
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:
        try:fcntl.flock(fd,fcntl.LOCK_UN)
        finally:os.close(fd)


def _sha256_file(path: Path) -> bytes:
    digest=hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda:src.read(1024*1024),b""):
            digest.update(block)
    return digest.digest()


def project_revision(folder: Path) -> str:
    path=Path(folder)/"project.json"
    if path.is_symlink() or not path.is_file():
        raise ProjectError("Native project.json unavailable")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_expected_revision(folder: Path, expected: str):
    if type(expected) is not str or not re.fullmatch(r"[0-9a-f]{64}",expected):
        raise ProjectConflict380("Reload this project: missing revision token")
    latest=project_revision(folder)
    if latest!=expected:
        raise ProjectConflict380("Another tab saved a newer revision; reload")
    return latest


def ensure_title_available(root: Path, title: str, *, exclude: Path | None = None):
    """Check real native projects, including old duplicates (never merge)."""
    wanted=title_key(title)
    for path in root.glob("*.zuckervid"):
        if path.is_symlink() or not path.is_dir() or not (path/"project.json").is_file():
            raise ProjectConflict380("Unverified native project requires review")
        if exclude is not None and path==exclude:
            continue
        doc=json.loads((path/"project.json").read_text(encoding="utf-8"))
        if title_key(str(doc.get("name") or path.stem))==wanted:
            raise ProjectConflict380("A saved project already has this title")


def create_unique_project(root: Path, title: str):
    ensure_title_available(root,title)
    # Folder is an immutable identity, not editable title. Fresh UUID also
    # prevents ambiguous names or accidental reuse of historical media.
    path=root/(uuid.uuid4().hex+".zuckervid")
    project=create_project(title,str(path))
    return project


def rename_existing(root: Path, *, folder: str, title: str,
                    expected_revision: str):
    root=Path(root).resolve(strict=True)
    with project_root_guard(root):
        path=verified_project_folder(root,folder)
        require_expected_revision(path,expected_revision)
        ensure_title_available(root,title,exclude=path)
        project=load_project(str(path))
        if any((stage or {}).get("status")=="running"
               for stage in (project.data.get("stages") or {}).values()):
            raise ProjectConflict380("Cannot rename a rendering project")
        project.data["name"]=" ".join(title.split())
        project.save()
        return {"path":str(path),"name":project.data["name"],
                "project_revision":project_revision(path)}


def _reject_links_and_count_bytes(src: Path):
    count=0
    total=0
    for path in src.rglob("*"):
        if path.is_symlink():
            raise ProjectConflict380("Linked files cannot be cloned safely")
        if not (path.is_file() or path.is_dir()):
            raise ProjectConflict380("Unsupported file inside project")
        if path.is_file():
            count+=1
            total+=path.stat().st_size
    return count,total


def _retarget_internal_paths(value, old: Path, new: Path):
    if isinstance(value,str) and (value==str(old) or value.startswith(str(old)+os.sep)):
        return str(new)+value[len(str(old)):]
    if isinstance(value,list):
        return [_retarget_internal_paths(v,old,new) for v in value]
    if isinstance(value,dict):
        return {k:_retarget_internal_paths(v,old,new) for k,v in value.items()}
    return value


def duplicate_existing(root: Path, *, folder: str, title: str,
                       expected_revision: str):
    """Copy complete source data to distinct verified native directory.

    Conservative full copy (including media/exports) uses significant disk
    and time for 360; keep this endpoint disabled until async/capacity
    benchmarking and status/progress have been validated. Never symlink or
    hard-link the original; copy into hidden staging dir and publish last.
    """
    root=Path(root).resolve(strict=True)
    with project_root_guard(root):
        path=verified_project_folder(root,folder)
        require_expected_revision(path,expected_revision)
        ensure_title_available(root,title)
        project=load_project(str(path))
        if any((s or {}).get("status")=="running"
               for s in (project.data.get("stages") or {}).values()):
            raise ProjectConflict380("Cannot duplicate a rendering project")
        count,total=_reject_links_and_count_bytes(path)
        # Large 360/4K clones need an async status/progress workflow. Do not
        # block the Owner HTTP worker while copying tens of gigabytes.
        if total > 2 * 1024**3:
            raise ProjectConflict380("Large project copy requires an async job")
        available=shutil.disk_usage(root).free
        if available<total+2*1024**3:
            raise ProjectConflict380("Not enough free disk to copy project safely")
        uid=uuid.uuid4().hex
        destination=root/(uid+".zuckervid")
        staging=root/(".copying-"+uid)
        if staging.exists() or destination.exists():
            raise ProjectConflict380("Native project destination already exists")
        try:
            shutil.copytree(path,staging,symlinks=False)
            # Verify the complete copied snapshot, not just project.json.
            # Reject failures before exposing any new .zuckervid project.
            for original in path.rglob("*"):
                if original.is_file():
                    rel=original.relative_to(path)
                    copy=staging/rel
                    if (not copy.is_file() or
                            _sha256_file(original) != _sha256_file(copy)):
                        raise ProjectConflict380("Independent copy integrity failed")
                    fd=os.open(str(copy),os.O_RDONLY)
                    try:os.fsync(fd)
                    finally:os.close(fd)
            # Ensure source edits were not changed by any noncompliant writer.
            require_expected_revision(path,expected_revision)
            clone=load_project(str(staging))
            clone.data=_retarget_internal_paths(clone.data,path,destination)
            clone.data["name"]=" ".join(title.split())
            clone.data["created_at"]=utc_now()
            clone.data["modified_at"]=clone.data["created_at"]
            clone.save()
            # Every cloned regular file must be independent bytes, not an alias.
            for fp in staging.rglob("*"):
                if fp.is_symlink():
                    raise ProjectConflict380("Copy contains linked content")
            os.replace(staging,destination)
            dir_fd=os.open(str(root),os.O_RDONLY)
            try:os.fsync(dir_fd)
            finally:os.close(dir_fd)
        except Exception:
            if staging.exists():shutil.rmtree(staging)
            raise
        return {"path":str(destination),"name":clone.data["name"],
                "project_revision":project_revision(destination),
                "source_project":str(path),"files_copied":count,"bytes_copied":total}
