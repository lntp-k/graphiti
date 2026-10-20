"""Isolated real HTTP transport with database initialization forbidden."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

import graphiti_mcp_server as server  # noqa: E402
from config.schema import ServerConfig  # noqa: E402
from services import runtime_diagnostics  # noqa: E402


async def forbidden_database_initialization(*args, **kwargs):
    raise AssertionError('The subprocess transport fixture must never initialize a database')


server.GraphitiService.initialize = forbidden_database_initialization
original_startup = runtime_diagnostics.ObservedServer.startup


async def report_endpoint(self, sockets=None):
    await original_startup(self, sockets=sockets)
    port = self.servers[0].sockets[0].getsockname()[1]
    (Path(sys.argv[1]).parent / 'endpoint').write_text(str(port))


runtime_diagnostics.ObservedServer.startup = report_endpoint
original_wrap = runtime_diagnostics.RuntimeDiagnostics.wrap
observed_diagnostics = None


def capture_diagnostics(self, app):
    global observed_diagnostics
    observed_diagnostics = self
    return original_wrap(self, app)


runtime_diagnostics.RuntimeDiagnostics.wrap = capture_diagnostics


async def initialize_without_database():
    if len(sys.argv) > 2:
        # Test-only readiness oracle; exits once the real middleware has observed
        # the partial body. It neither consumes a request nor queries a graph.
        async def report_body_observed():
            while True:
                if observed_diagnostics and any(
                    item['bytes_received'] > 0 for item in observed_diagnostics.requests.values()
                ):
                    Path(sys.argv[2]).write_text('partial body observed\n')
                    return
                await asyncio.sleep(0.01)

        asyncio.create_task(report_body_observed())
    return ServerConfig(transport='http', host='127.0.0.1', port=0, diagnostics_dir=sys.argv[1])


server.initialize_server = initialize_without_database
asyncio.run(server.run_mcp_server())
