"""No-DB tests for opt-in runtime capture and preserved ASGI semantics."""

import asyncio
import json
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import uvicorn

from services import runtime_diagnostics as runtime


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [True, False])
async def test_server_dispatch_uses_configured_capture(monkeypatch, enabled):
    import graphiti_mcp_server as server
    from config.schema import ServerConfig

    config = ServerConfig(diagnostics_dir='/private' if enabled else None)
    monkeypatch.setattr(server, 'initialize_server', AsyncMock(return_value=config))
    capture = AsyncMock()
    normal = AsyncMock()
    monkeypatch.setattr(server, 'run_http_server', capture)
    monkeypatch.setattr(server.mcp, 'run_streamable_http_async', normal)
    await server.run_mcp_server()
    capture.assert_awaited_once_with(server.mcp, config.host, config.port, config.diagnostics_dir)
    normal.assert_not_awaited()


class Records:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


@pytest.mark.asyncio
async def test_partial_post_records_wait_and_never_contents():
    writer = Records()
    diagnostics = runtime.RuntimeDiagnostics(writer)
    receiving = asyncio.Event()
    parked = asyncio.Event()
    calls = 0
    secret = 'SENSITIVE_SENTINEL_9723'

    async def receive():
        nonlocal calls
        calls += 1
        if calls == 1:
            return {'type': 'http.request', 'body': secret.encode(), 'more_body': True}
        receiving.set()
        await parked.wait()
        return {'type': 'http.disconnect'}

    async def app(scope, receive, send):
        local_secret = secret
        assert local_secret
        await receive()
        await receive()

    task = asyncio.create_task(
        diagnostics.wrap(app)(
            {
                'type': 'http',
                'method': 'POST',
                'path': '/' + secret,
                'headers': [(b'authorization', secret.encode()), (b'content-length', b'100')],
            },
            receive,
            AsyncMock(),
        ),
        name=secret,
    )
    await asyncio.wait_for(receiving.wait(), timeout=1)
    diagnostics.snapshot('shutdown_entry')
    record = writer.records[-1]
    item = record['requests'][0]
    assert item['path'] == '<other>'
    assert item['bytes_received'] == len(secret)
    assert item['content_length'] == 100
    assert item['body_complete'] is False
    assert item['response_started'] is False
    assert item['age_seconds'] >= 0
    serialized = json.dumps(record)
    assert secret not in serialized
    assert any(
        frame['function'] == 'receive' for item in record['tasks'] for frame in item['await_chain']
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not diagnostics.requests


@pytest.mark.asyncio
async def test_normal_request_and_lifespan_preserve_messages():
    diagnostics = runtime.RuntimeDiagnostics(Records())
    receive = AsyncMock(return_value={'type': 'http.request', 'body': b'hello'})
    send = AsyncMock()

    async def app(scope, receive, send):
        message = await receive()
        assert message['body'] == b'hello'
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'world'})
        item = next(iter(diagnostics.requests.values()))
        assert item['body_complete'] and item['response_complete']
        assert item['response_started']

    await diagnostics.wrap(app)({'type': 'http', 'path': '/mcp', 'method': 'POST'}, receive, send)
    assert not diagnostics.requests
    assert send.await_args_list[-1].args[0]['body'] == b'world'
    lifespan = AsyncMock()
    await diagnostics.wrap(lifespan)({'type': 'lifespan'}, receive, send)
    lifespan.assert_awaited_once_with({'type': 'lifespan'}, receive, send)


@pytest.mark.asyncio
async def test_request_exception_propagates_and_failed_writer_is_harmless():
    class Broken:
        def write(self, record):
            raise OSError('failure with private content')

    diagnostics = runtime.RuntimeDiagnostics(Broken())
    diagnostics.snapshot('shutdown_entry')

    async def app(scope, receive, send):
        raise ValueError('real application error')

    with pytest.raises(ValueError, match='real application error'):
        await diagnostics.wrap(app)({'type': 'http'}, AsyncMock(), AsyncMock())
    assert not diagnostics.requests


def test_private_writer_permissions_and_rotation(tmp_path):
    directory = tmp_path / 'private'
    writer = runtime.PrivateRotatingWriter(str(directory), max_bytes=80, backups=2)
    for index in range(30):
        writer.write({'event': 'test', 'index': index})
    assert not writer.failed
    assert directory.stat().st_mode & 0o777 == 0o700
    paths = list(directory.iterdir())
    assert len(paths) == 3
    for path in paths:
        assert path.stat().st_size <= 80
        assert path.stat().st_mode & 0o777 == 0o600
        for line in path.read_text().splitlines():
            assert json.loads(line)['event'] == 'test'


def test_private_writer_rejects_symlink_and_public_dir(tmp_path):
    target = tmp_path / 'target'
    target.mkdir(mode=0o700)
    link = tmp_path / 'link'
    link.symlink_to(target)
    writer = runtime.PrivateRotatingWriter(str(link))
    writer.write({'event': 'test'})
    assert writer.failed and not list(target.iterdir())
    public = tmp_path / 'public'
    public.mkdir(mode=0o755)
    public.chmod(0o755)
    writer = runtime.PrivateRotatingWriter(str(public))
    writer.write({'event': 'test'})
    assert writer.failed and not list(public.iterdir())


@pytest.mark.asyncio
async def test_signal_delegation_and_periodic_shutdown(monkeypatch):
    writer = Records()
    diagnostics = runtime.RuntimeDiagnostics(writer)
    server = runtime.ObservedServer(uvicorn.Config(AsyncMock()), diagnostics, 0.001)
    delegated = []
    monkeypatch.setattr(
        uvicorn.Server, 'handle_exit', lambda self, sig, frame: delegated.append(sig)
    )
    server.handle_exit(signal.SIGTERM, None)
    assert delegated == [signal.SIGTERM]
    assert writer.records[-1]['signal'] == signal.SIGTERM
    waiting = asyncio.Event()

    async def shutdown(self, sockets=None):
        await asyncio.wait_for(waiting.wait(), timeout=1)

    monkeypatch.setattr(uvicorn.Server, 'shutdown', shutdown)
    task = asyncio.create_task(server.shutdown())
    for _ in range(100):
        if any(record['event'] == 'shutdown_wait' for record in writer.records):
            break
        await asyncio.sleep(0.001)
    else:
        pytest.fail('no periodic snapshot')
    waiting.set()
    await task
    assert writer.records[-1]['event'] == 'shutdown_exit'


@pytest.mark.asyncio
async def test_sdk_defaults_and_graceful_timeout_unchanged(monkeypatch):
    mcp = SimpleNamespace(settings=SimpleNamespace(log_level='INFO'))
    mcp.streamable_http_app = lambda **kwargs: kwargs
    mcp.session_manager = SimpleNamespace(app=SimpleNamespace(get_request_handler=lambda _: None))
    captured = {}

    class Server:
        def __init__(self, config, diagnostics, **kwargs):
            captured['config'] = config
            captured['diagnostics'] = diagnostics

        async def serve(self):
            captured['served'] = True

    monkeypatch.setattr(runtime, 'ObservedServer', Server)
    await runtime.run_with_diagnostics(mcp, '127.0.0.1', 9999, '/unused')
    config = captured['config']
    assert config.host == '127.0.0.1' and config.port == 9999
    assert config.timeout_graceful_shutdown is None
    assert captured['served']


@pytest.mark.asyncio
async def test_request_and_task_record_bounds():
    records = Records()
    diagnostics = runtime.RuntimeDiagnostics(records, max_requests=1, max_tasks=1)
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def app(scope, receive, send):
        entered.set()
        await gate.wait()

    tasks = []
    for _ in range(3):
        tasks.append(
            asyncio.create_task(diagnostics.wrap(app)({'type': 'http'}, AsyncMock(), AsyncMock()))
        )
        await asyncio.sleep(0)
    await entered.wait()
    diagnostics.snapshot('shutdown_entry')
    assert len(records.records[-1]['requests']) == 1
    assert len(records.records[-1]['tasks']) <= 1
    gate.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_shutdown_cancellation_cleans_monitor_and_propagates(monkeypatch):
    records = Records()
    diagnostics = runtime.RuntimeDiagnostics(records)
    server = runtime.ObservedServer(uvicorn.Config(AsyncMock()), diagnostics, 0.001)
    entered = asyncio.Event()

    async def shutdown(self, sockets=None):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(uvicorn.Server, 'shutdown', shutdown)
    task = asyncio.create_task(server.shutdown())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert records.records[-1]['event'] == 'shutdown_exit'
    assert not any(
        candidate.get_coro().__qualname__.endswith('shutdown.<locals>.monitor')
        for candidate in asyncio.all_tasks()
    )


@pytest.mark.asyncio
async def test_startup_records_runtime_metadata(monkeypatch):
    records = Records()
    diagnostics = runtime.RuntimeDiagnostics(records)
    server = runtime.ObservedServer(uvicorn.Config(AsyncMock()), diagnostics)
    startup = AsyncMock()
    monkeypatch.setattr(uvicorn.Server, 'startup', startup)
    await server.startup()
    startup.assert_awaited_once_with(sockets=None)
    record = records.records[-1]
    assert record['event'] == 'startup'
    assert record['pid'] > 0 and record['ppid'] > 0
    assert record['packages']['mcp']
    assert record['python']
