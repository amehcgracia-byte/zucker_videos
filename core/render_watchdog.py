"""Segment-local inactivity detection. CPU/output activity prevents false timeouts."""
from __future__ import annotations
from contextvars import ContextVar
from functools import wraps
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from core.ffmpeg import FFmpegError
from core.project import atomic_write_json
IDLE_TIMEOUT_SEC = 120.0
POLL_SEC = 2.0
_SCOPE = ContextVar('segment_watchdog_scope', default=None)
_EVENT_LOCK = threading.Lock()

class SegmentInactivityError(FFmpegError):

    def __init__(self, event):
        self.event = event
        super().__init__(f"Segment process {event['pid']} inactive for {event['timeout_sec']:g}s; diagnostic captured")

def process_cpu_seconds(pid):
    """Cumulative child CPU, not smoothed percent; unknown means do not time out."""
    try:
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(4096, False, pid)
            if not handle:
                return None
            try:
                creation, exit_, system, user = (wintypes.FILETIME() for _ in range(4))
                if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_), ctypes.byref(system), ctypes.byref(user)):
                    return None
                return sum(((x.dwHighDateTime << 32) + x.dwLowDateTime for x in (system, user))) / 10000000
            finally:
                kernel.CloseHandle(handle)
        if sys.platform.startswith('linux'):
            fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            return (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')
        text = subprocess.check_output(['ps', '-p', str(pid), '-o', 'time='], text=True, timeout=3).strip()
        total = 0.0
        for value in text.split(':'):
            total = total * 60 + float(value)
        return total
    except (OSError, ValueError, subprocess.SubprocessError):
        return None

def stack_sample(pid):
    """Bounded, read-only diagnostics of this owned child only."""
    try:
        if sys.platform == 'darwin':
            result = subprocess.run(['/usr/bin/sample', str(pid), '1', '1'], capture_output=True, text=True, timeout=6)
            return {'kind': 'macOS sample', 'text': (result.stdout or result.stderr)[:65536], 'returncode': result.returncode}
        if sys.platform.startswith('linux'):
            tasks = Path(f'/proc/{pid}/task')
            rows = []
            for task in list(tasks.iterdir())[:64]:
                try:
                    rows.append({'tid': task.name, 'wchan': (task / 'wchan').read_text(), 'stack': (task / 'stack').read_text()})
                except OSError:
                    rows.append({'tid': task.name, 'unavailable': 'kernel stack access denied'})
            return {'kind': 'Linux thread wait channels/stacks', 'threads': rows}
        return {'kind': 'unavailable', 'reason': 'No built-in Windows stack sampler; CPU times still measured'}
    except (OSError, subprocess.SubprocessError) as exc:
        return {'kind': 'unavailable', 'reason': str(exc)}

class IdleState:

    def __init__(self, now):
        self.last = now
        self.cpu = None
        self.token = None

    def expired(self, now, cpu, token, timeout):
        if cpu is None:
            self.last = now
            self.cpu = None
            self.token = token
            return False
        if self.cpu is None or cpu > self.cpu or token != self.token:
            self.last = now
        self.cpu = cpu
        self.token = token
        return now - self.last >= timeout

class ProcessWatchdog:

    def __init__(self, process, output=None, heartbeat=None, peers=(), blocked_process=None):
        self.process = process
        self.output = Path(output) if output else None
        self.heartbeat = heartbeat
        self.peers = tuple(peers)
        self.blocked_process = blocked_process
        self.scope = _SCOPE.get()
        self.stop_event = threading.Event()
        self.error = None
        self.activity = 0
        self.positions = {}
        self.state = IdleState(time.monotonic())
        self.thread = None

    @property
    def enabled(self):
        return self.scope is not None

    def touch(self):
        self.activity += 1

    def start(self):
        if self.enabled:
            self.thread = threading.Thread(target=self._monitor, daemon=True)
            self.thread.start()
        return self

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def _monitor(self):
        while not self.stop_event.wait(POLL_SEC):
            if all((p.poll() is not None for p in (self.process, *self.peers))):
                return
            try:
                if self.heartbeat:
                    self.heartbeat()
                children = [self.process, *self.peers]
                cpus = [process_cpu_seconds(p.pid) for p in children if p.poll() is None]
                cpu = None if not cpus or any((x is None for x in cpus)) else sum(cpus)
                token = (self.activity,)
                if self.output:
                    try:
                        stat = self.output.stat()
                        token += (stat.st_size, stat.st_mtime_ns)
                    except OSError:
                        pass
                if not self.state.expired(time.monotonic(), cpu, token, IDLE_TIMEOUT_SEC):
                    continue
                victim = self.blocked_process() if self.blocked_process else self.process
                if victim is None:
                    self.state.last = time.monotonic()
                    continue
                if victim.poll() is not None:
                    return
                event = {
                    'pid': victim.pid,
                    'timeout_sec': IDLE_TIMEOUT_SEC,
                    'cpu_seconds': cpu,
                    'segment': self.scope['index'],
                    'attempt': self.scope['attempt'],
                    'source': self.scope['source'],
                    'output': self.scope['output'],
                    'command': victim.args,
                    'stack_sample': stack_sample(victim.pid),
                    'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                }
                self.error = SegmentInactivityError(event)
                # Persistence must not delay terminating the inactive child.
                if victim.poll() is None:
                    victim.kill()
                self.scope['record'](event)
                return
            except BaseException as exc:
                if self.error is None:
                    self.error = exc
                if self.process.poll() is None:
                    self.process.kill()
                return

    def raise_if_failed(self):
        if self.error:
            raise self.error

def guard_segment_render(render):

    @wraps(render)
    def guarded(*args, **kwargs):
        project = args[0] if args else kwargs['project']
        segment = args[1] if len(args) > 1 else kwargs['segment']
        output = Path(args[3] if len(args) > 3 else kwargs['output_path'])
        index_match = re.search('segment-(\\d+)', output.name)
        index = int(index_match.group(1)) if index_match else None
        key = str(output.resolve())
        with _EVENT_LOCK:
            if not hasattr(project, '_watchdog_retry_keys'):
                project._watchdog_retry_keys = set()

        def record(event):
            with _EVENT_LOCK:
                events = project.data.setdefault('_export_watchdog_events', [])
                events.append(event)
                atomic_write_json(project.artifacts_dir / 'export_watchdog_manifest.json', {'stage': 'export', 'events': events})
        for attempt in range(2):
            token = _SCOPE.set({'index': index, 'attempt': attempt, 'source': segment.get('source_path') or segment.get('clip_path'), 'output': str(output), 'record': record})
            try:
                call_kwargs = dict(kwargs)
                if attempt:
                    call_kwargs['force_cpu'] = True
                result = render(*args, **call_kwargs)
                if attempt:
                    with _EVENT_LOCK:
                        for event in project.data.get('_export_watchdog_events', []):
                            if event.get('output') == str(output):
                                event['cpu_retry_succeeded'] = True
                        atomic_write_json(project.artifacts_dir / 'export_watchdog_manifest.json', {'stage': 'export', 'events': project.data.get('_export_watchdog_events', [])})
                return result
            except SegmentInactivityError:
                with _EVENT_LOCK:
                    if attempt or key in project._watchdog_retry_keys:
                        raise
                    project._watchdog_retry_keys.add(key)
                output.unlink(missing_ok=True)
                callback = kwargs.get('progress_callback') or (args[8] if len(args) > 8 else None)
                if callback:
                    callback(0, 'Segment stopped responding; retrying once on CPU')
            finally:
                _SCOPE.reset(token)
    return guarded
