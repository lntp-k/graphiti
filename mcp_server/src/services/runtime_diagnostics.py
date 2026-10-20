"""Opt-in, bounded shutdown diagnostics without request contents or frame locals."""

import asyncio
import json
import os
import sys
import time
from contextlib import suppress
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import uvicorn


class PrivateRotatingWriter:
    """Best-effort JSONL output; a failed writer never prevents server operation."""

    def __init__(self, directory: str, max_bytes: int = 1024 * 1024, backups: int = 2):
        self.directory = Path(directory)
        self.max_bytes = max_bytes
        self.backups = backups
        self.failed = False

    def write(self, record: dict) -> None:
        if self.failed:
            return
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.directory.is_symlink():
                raise OSError('diagnostic directory must not be a symlink')
            stat = self.directory.stat()
            if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
                raise OSError('diagnostic directory must be private and owned by the process')
            data = (json.dumps(record, ensure_ascii=True, separators=(',', ':')) + '\n').encode()
            if len(data) > self.max_bytes:
                return
            path = self.directory / 'runtime.jsonl'
            if path.exists() and path.stat().st_size + len(data) > self.max_bytes:
                for index in range(self.backups, 0, -1):
                    source = path if index == 1 else path.with_name(f'runtime.jsonl.{index - 1}')
                    target = path.with_name(f'runtime.jsonl.{index}')
                    if source.exists():
                        source.replace(target)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, data)
            finally:
                os.close(fd)
        except Exception:
            # Never include exception text: it may contain sensitive filesystem paths.
            self.failed = True
            print('ASGI diagnostics disabled: private log write failed', file=sys.stderr)


def _await_chain(coroutine, limit: int = 16) -> list[dict]:
    frames = []
    seen = set()
    while coroutine is not None and len(frames) < limit and id(coroutine) not in seen:
        seen.add(id(coroutine))
        frame = getattr(coroutine, 'cr_frame', None) or getattr(coroutine, 'gi_frame', None)
        if frame is not None:
            frames.append(
                {
                    'file': os.path.basename(frame.f_code.co_filename),
                    'function': frame.f_code.co_name,
                    'line': frame.f_lineno,
                }
            )
        coroutine = getattr(coroutine, 'cr_await', None) or getattr(coroutine, 'gi_yieldfrom', None)
    return frames


class RuntimeDiagnostics:
    def __init__(self, writer, max_requests: int = 128, max_tasks: int = 64):
        self.writer = writer
        self.max_requests = max_requests
        self.max_tasks = max_tasks
        self.requests = {}
        self.sequence = 0

    def snapshot(self, event: str, signum: int | None = None) -> None:
        try:
            tasks = asyncio.all_tasks()
            request_tasks = {item['task_id'] for item in self.requests.values()}
            ordered_tasks = sorted(tasks, key=lambda task: id(task) not in request_tasks)
            record = {
                'event': event,
                'wall_time_ns': time.time_ns(),
                'monotonic': time.monotonic(),
                'pid': os.getpid(),
                'ppid': os.getppid(),
                'signal': signum,
                'requests': [
                    {**item, 'age_seconds': round(time.monotonic() - item['started'], 3)}
                    for item in list(self.requests.values())[: self.max_requests]
                ],
                'tasks_total': len(tasks),
                'tasks': [
                    {'task_id': id(task), 'await_chain': _await_chain(task.get_coro())}
                    for task in ordered_tasks[: self.max_tasks]
                    if not task.done()
                ],
            }
            if event == 'startup':
                record['python'] = '.'.join(map(str, sys.version_info[:3]))
                record['packages'] = {}
                for name in ('uvicorn', 'mcp', 'sse-starlette', 'graphiti-core'):
                    try:
                        record['packages'][name] = version(name)
                    except PackageNotFoundError:
                        record['packages'][name] = '<unavailable>'
            self.writer.write(record)
        except Exception:
            pass

    def wrap(self, app):
        async def observed(scope, receive, send):
            if scope['type'] != 'http' or len(self.requests) >= self.max_requests:
                return await app(scope, receive, send)
            self.sequence += 1
            request_id = self.sequence
            method = scope.get('method', '')
            item = {
                'id': request_id,
                'task_id': id(asyncio.current_task()),
                'method': method
                if method in ('GET', 'POST', 'DELETE', 'HEAD', 'OPTIONS', 'PUT', 'PATCH')
                else '<other>',
                'path': scope.get('path')
                if scope.get('path') in ('/mcp', '/mcp/', '/health')
                else '<other>',
                'bytes_received': 0,
                'body_complete': False,
                'response_started': False,
                'response_complete': False,
                'started': time.monotonic(),
            }
            for key, value in scope.get('headers', ()):
                if key == b'content-length' and len(value) <= 12 and value.isdigit():
                    item['content_length'] = int(value)
                    break
            self.requests[request_id] = item

            async def observed_receive():
                message = await receive()
                if message['type'] == 'http.request':
                    item['bytes_received'] += len(message.get('body', b''))
                    item['body_complete'] = not message.get('more_body', False)
                return message

            async def observed_send(message):
                await send(message)
                if message['type'] == 'http.response.start':
                    item['response_started'] = True
                elif message['type'] == 'http.response.body' and not message.get(
                    'more_body', False
                ):
                    item['response_complete'] = True

            try:
                return await app(scope, observed_receive, observed_send)
            finally:
                self.requests.pop(request_id, None)

        return observed


class ObservedServer(uvicorn.Server):
    def __init__(self, config, diagnostics: RuntimeDiagnostics, snapshot_interval: float = 2.0):
        super().__init__(config)
        self.diagnostics = diagnostics
        self.snapshot_interval = snapshot_interval

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        self.diagnostics.snapshot('startup')

    def handle_exit(self, sig: int, frame):
        # sse-starlette patches uvicorn.Server.handle_exit to initiate SSE drain.
        # Delegating to the current superclass retains that behavior and double-signal handling.
        try:
            super().handle_exit(sig, frame)
        finally:
            self.diagnostics.snapshot('signal', int(sig))

    async def shutdown(self, sockets=None):
        self.diagnostics.snapshot('shutdown_entry')

        async def monitor():
            while True:
                await asyncio.sleep(self.snapshot_interval)
                self.diagnostics.snapshot('shutdown_wait')

        watcher = asyncio.create_task(monitor())
        try:
            await super().shutdown(sockets=sockets)
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
            self.diagnostics.snapshot('shutdown_exit')


async def run_with_diagnostics(mcp, host: str, port: int, output_dir: str) -> None:
    """Use SDK defaults; only wrap request bookkeeping and uvicorn diagnostics."""
    diagnostics = RuntimeDiagnostics(PrivateRotatingWriter(output_dir))
    app = diagnostics.wrap(mcp.streamable_http_app(host=host))
    config = uvicorn.Config(app, host=host, port=port, log_level=mcp.settings.log_level.lower())
    await ObservedServer(config, diagnostics).serve()
