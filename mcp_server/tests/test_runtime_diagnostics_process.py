"""Real installed MCP/Uvicorn/SSE transport, without databases or production services."""

import contextlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

SERVER = Path(__file__).parent / 'fixtures' / 'runtime_diagnostics_server.py'
SENTINEL = 'PRIVATE_TEST_CONTENT_91d3a6'


def poll(predicate, process, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        if process.poll() is not None:
            pytest.fail(f'fixture exited before condition: {process.returncode}')
        time.sleep(0.01)
    pytest.fail('timed out waiting for isolated transport condition')


def records(directory):
    path = directory / 'runtime.jsonl'
    if not path.exists():
        return []
    # A concurrent append may leave an incomplete final line temporarily.
    return [json.loads(line) for line in path.read_text().splitlines() if line.endswith('}')]


@contextlib.contextmanager
def running_server(tmp_path, partial=False):
    diagnostics = tmp_path / 'diagnostics'
    marker = tmp_path / 'partial-ready'
    logfile = tmp_path / 'subprocess.log'
    command = [sys.executable, '-I', str(SERVER), str(diagnostics)]
    if partial:
        command.append(str(marker))
    # Do not inherit credentials, proxy settings, or graph connection variables.
    environment = {
        'PATH': os.defpath,
        'GRAPHITI_TELEMETRY_ENABLED': 'false',
        'PYTHON_DOTENV_DISABLED': '1',
        'BROWSER': '0',
    }
    with logfile.open('wb') as output:
        process = subprocess.Popen(
            command, cwd=SERVER.parents[2], env=environment, stdout=output, stderr=output
        )
        try:
            poll(lambda: any(item['event'] == 'startup' for item in records(diagnostics)), process)
            endpoint = tmp_path / 'endpoint'
            poll(endpoint.exists, process)
            yield process, int(endpoint.read_text()), diagnostics, marker
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


def assert_finished(process, directory):
    process.wait(timeout=8)
    # Uvicorn re-raises the received SIGTERM after graceful cleanup on POSIX.
    assert process.returncode in (0, -signal.SIGTERM)
    logged = records(directory)
    events = [item['event'] for item in logged]
    assert 'signal' in events
    assert 'shutdown_entry' in events
    assert events[-1] == 'shutdown_exit'
    assert SENTINEL not in json.dumps(logged)
    return logged


def test_idle_sigterm_finishes_through_real_http_branch(tmp_path):
    with running_server(tmp_path) as (process, port, directory, marker):
        process.send_signal(signal.SIGTERM)
        logged = assert_finished(process, directory)
        assert not any(item['requests'] for item in logged)


def test_initialized_mcp_sse_automatically_drains_on_sigterm(tmp_path):
    with running_server(tmp_path) as (process, port, directory, marker):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        stream = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            headers = {
                'Accept': 'application/json, text/event-stream',
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + SENTINEL,
            }
            initialization = {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {
                    'protocolVersion': '2025-11-25',
                    'capabilities': {},
                    'clientInfo': {'name': 'synthetic-test', 'version': '1'},
                },
            }
            connection.request('POST', '/mcp', json.dumps(initialization), headers)
            response = connection.getresponse()
            assert response.status == 200
            session = response.getheader('mcp-session-id')
            payload = response.read().decode()
            result = next(
                json.loads(line[5:].strip())
                for line in payload.splitlines()
                if line.startswith('data:')
            )
            assert result['result']['protocolVersion'] == '2025-11-25'
            assert session
            session_headers = {
                **headers,
                'Mcp-Session-Id': session,
                'Mcp-Protocol-Version': '2025-11-25',
            }
            connection.request(
                'POST',
                '/mcp',
                json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}),
                session_headers,
            )
            response = connection.getresponse()
            assert response.status == 202
            response.read()
            stream.request('GET', '/mcp?' + SENTINEL, headers=session_headers)
            sse = stream.getresponse()
            assert sse.status == 200
            assert sse.getheader('content-type').startswith('text/event-stream')
            process.send_signal(signal.SIGTERM)
            logged = assert_finished(process, directory)
            assert any(
                request['method'] == 'GET'
                and request['path'] == '/mcp'
                and request['response_started']
                and not request['response_complete']
                for item in logged
                for request in item['requests']
            )
        finally:
            stream.close()
            connection.close()


def test_partial_post_wait_is_captured_then_disconnect_releases_shutdown(tmp_path):
    with running_server(tmp_path, partial=True) as (process, port, directory, marker):
        client = socket.create_connection(('127.0.0.1', port), timeout=5)
        try:
            body = SENTINEL.encode()
            content_length = len(body) + 1000
            client.sendall(
                (
                    f'POST /mcp?{SENTINEL} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                    'Accept: application/json, text/event-stream\r\n'
                    'Content-Type: application/json\r\n'
                    f'Authorization: Bearer {SENTINEL}\r\n'
                    f'Content-Length: {content_length}\r\n\r\n'
                ).encode()
                + body
            )
            poll(marker.exists, process)
            process.send_signal(signal.SIGTERM)
            logged = poll(
                lambda: [item for item in records(directory) if item['event'] == 'shutdown_wait'],
                process,
            )
            assert process.poll() is None
            request = next(item for item in logged[-1]['requests'] if item['method'] == 'POST')
            assert request['body_complete'] is False
            assert request['bytes_received'] == len(body) < request['content_length']
            task = next(
                item for item in logged[-1]['tasks'] if item['task_id'] == request['task_id']
            )
            assert any(
                frame['file'] == 'streamable_http_manager.py' for frame in task['await_chain']
            )
            # MCP 2.2 buffers the body in RequestBodyLimitMiddleware before
            # dispatching to StreamableHTTPServerTransport._handle_post_request.
            assert any(frame['file'] == 'transport_security.py' for frame in task['await_chain'])
            assert any(
                frame['function'] in ('stream', 'body', 'receive') for frame in task['await_chain']
            )
            assert SENTINEL not in json.dumps(records(directory))
            client.close()
            assert_finished(process, directory)
        finally:
            client.close()
