"""Keep modern MCP subscription sockets open across the real SIGTERM path."""

import http.client
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / 'fixtures' / 'subscription_shutdown_server.py'


def read_sse_json(response):
    """HTTPResponse.readline decodes HTTP chunk framing before parsing SSE."""
    data = []
    while True:
        line = response.readline()
        if not line:
            raise AssertionError('Subscription ended without the expected SSE frame')
        line = line.rstrip(b'\r\n')
        if not line:
            if data:
                return json.loads(b'\n'.join(data))
            continue
        if line.startswith(b'data:'):
            data.append(line[5:].lstrip(b' '))


@pytest.mark.parametrize('diagnostics_enabled', [False, True], ids=['plain', 'diagnostics'])
@pytest.mark.parametrize('client_count', [1, 20], ids=['one-client', 'twenty-clients'])
def test_subscriptions_finish_before_process_exit(tmp_path, diagnostics_enabled, client_count):
    clients = []
    responses = []
    env = {
        'PATH': os.defpath,
        'HOME': str(tmp_path),
        'PYTHON_DOTENV_DISABLED': 'true',
        'GRAPHITI_TELEMETRY_ENABLED': 'false',
        'DO_NOT_TRACK': '1',
    }
    with (tmp_path / 'server.log').open('w') as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                '-I',
                str(FIXTURE),
                str(tmp_path),
                'enabled' if diagnostics_enabled else 'disabled',
            ],
            cwd=tmp_path,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            endpoint = tmp_path / 'endpoint'
            deadline = time.monotonic() + 15
            while not endpoint.exists():
                assert proc.poll() is None, (tmp_path / 'server.log').read_text()
                assert time.monotonic() < deadline, 'Isolated HTTP startup timed out'
                time.sleep(0.02)
            port = int(endpoint.read_text())
            for index in range(client_count):
                client = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                clients.append(client)
                body = json.dumps(
                    {
                        'jsonrpc': '2.0',
                        'id': index,
                        'method': 'subscriptions/listen',
                        'params': {
                            'notifications': {'toolsListChanged': True},
                            '_meta': {
                                'io.modelcontextprotocol/protocolVersion': '2026-07-28',
                                'io.modelcontextprotocol/clientCapabilities': {},
                            },
                        },
                    }
                )
                client.request(
                    'POST',
                    '/mcp',
                    body,
                    {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json, text/event-stream',
                        'MCP-Protocol-Version': '2026-07-28',
                        'Mcp-Method': 'subscriptions/listen',
                    },
                )
                response = client.getresponse()
                responses.append(response)
                assert response.status == 200
                assert 'text/event-stream' in response.getheader('content-type', '')
                acknowledgement = read_sse_json(response)
                assert acknowledgement['method'] == 'notifications/subscriptions/acknowledged'
                assert (
                    acknowledgement['params']['_meta']['io.modelcontextprotocol/subscriptionId']
                    == index
                )

            started = time.monotonic()
            proc.send_signal(signal.SIGTERM)
            # Every client socket stays open until the server has exited. Client
            # disconnection must never be what unblocks this regression.
            proc.wait(timeout=5)
            elapsed = time.monotonic() - started
            assert proc.returncode in (0, -signal.SIGTERM)
            for index, response in enumerate(responses):
                final = read_sse_json(response)
                assert final['jsonrpc'] == '2.0'
                assert final['id'] == index
                assert isinstance(final['result'], dict)
                assert final['result']['_meta']['io.modelcontextprotocol/subscriptionId'] == index
                assert response.read() == b''
            server_log = (tmp_path / 'server.log').read_text()
            assert 'Cancel ' not in server_log
            assert 'timeout graceful shutdown' not in server_log
            (tmp_path / 'result.json').write_text(
                json.dumps(
                    {
                        'clients': client_count,
                        'diagnostics': diagnostics_enabled,
                        'seconds': elapsed,
                    }
                )
                + '\n'
            )
        finally:
            for response in responses:
                response.close()
            for client in clients:
                client.close()
            if proc.poll() is None:
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
