"""Remove download-zone metadata from this bundle's managed UI assemblies.

.NET Framework refuses marked assemblies after Explorer extracts a downloaded
ZIP. Scope this to bundled pythonnet/webview DLLs, before any CLR import.
Never scan user documents or change Windows security settings.
"""
from pathlib import Path
import sys


def prepare_managed_assemblies():
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return
    root = Path(sys._MEIPASS).resolve()
    for package in ('pythonnet', 'webview'):
        directory = root / package
        for dll in directory.rglob('*.dll'):
            if not dll.resolve().is_relative_to(root):
                continue
            try:
                Path(str(dll) + ':Zone.Identifier').unlink(missing_ok=True)
            except OSError as exc:
                raise RuntimeError(f'Could not unblock bundled UI assembly {dll}. '
                                   'Unblock the downloaded ZIP in Properties and extract it again.') from exc


prepare_managed_assemblies()
