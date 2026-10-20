"""Exercise Graphiti's real HTTP entry point without constructing a database client."""

import asyncio
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

import graphiti_mcp_server as server  # noqa: E402
from config.schema import ServerConfig  # noqa: E402

output = Path(sys.argv[1])
diagnostics_enabled = sys.argv[2] == 'enabled'
original_startup = uvicorn.Server.startup


async def report_endpoint(self, sockets=None):
    await original_startup(self, sockets=sockets)
    port = self.servers[0].sockets[0].getsockname()[1]
    (output / 'endpoint').write_text(str(port))


async def forbidden_initialize(*args, **kwargs):
    raise AssertionError('A shutdown regression must never initialize GraphitiService')


async def initialize_without_database():
    return ServerConfig(
        transport='http',
        host='127.0.0.1',
        port=0,
        diagnostics_dir=str(output / 'diagnostics') if diagnostics_enabled else None,
    )


uvicorn.Server.startup = report_endpoint
server.GraphitiService.initialize = forbidden_initialize
server.initialize_server = initialize_without_database
asyncio.run(server.run_mcp_server())
