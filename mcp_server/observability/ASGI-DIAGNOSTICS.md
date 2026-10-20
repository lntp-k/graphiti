> Document version v1 — 2026-10-21

# HTTP shutdown diagnostics

Set `server.diagnostics_dir` in the MCP YAML configuration (or
`SERVER__DIAGNOSTICS_DIR` in its environment). The local KURE configuration uses
`/home/jl/coding/graphiti-src/session-work/runtime-observability/asgi`.
The next server process loads the setting. An already running process is not
instrumented by editing its source or configuration.

Capture includes startup package versions, wall-clock nanoseconds, monotonic
time, PID/PPID, termination signals, pending HTTP progress and asyncio coroutine
await locations. During shutdown, snapshots repeat every two seconds until
shutdown completes. Incomplete request bodies can therefore be distinguished
from long-running tool/background work without logging their contents.

The directory must be process-owned mode 0700; output files are mode 0600.
`runtime.jsonl` rotates at 1 MiB with two backups (at most 3 MiB total).
Requests are capped at 128, task entries at 64, and await chains at 16 frames.
Untracked excess requests still execute normally. Missing entries are not proof
that no request existed. No request bodies, query strings, authentication header
values, task names, source lines or frame locals are captured. Only the numeric
Content-Length is retained from headers. A write failure disables capture and
prints `ASGI diagnostics disabled: private log write failed` once to stderr.

The wrapper preserves SDK HTTP defaults, SSE signal delegation and Uvicorn's
existing unlimited graceful wait. It observes a shutdown stall; it does not fix
one or force cancellation. A SIGKILL cannot produce a final snapshot. Code
blocking the asyncio event loop can also prevent periodic sampling.

Validate without DB/LLM initialization or production connections:

```sh
cd /home/jl/coding/graphiti-src/mcp_server
GRAPHITI_TELEMETRY_ENABLED=false .venv/bin/python -m pytest tests/test_runtime_diagnostics.py tests/test_runtime_diagnostics_process.py --basetemp=../session-work/asgi-diagnostics-test
```

After normal service activation, verify a `startup` record belongs to the current
Python child PID and inspect the service journal for the fixed write-failure
warning. Do not issue graph-writing calls to validate this collector. A service
restart runs the existing Graphiti database initialization; it is not a no-DB
fixture. Preserve any in-flight ingestion and the user's graph restrictions when
choosing the activation time. The local service and spark-infra files do not need
editing. To disable, set this one option to null before the next normal start.

For the next incident, preserve all three JSONL files and the service journal's
matching time window under a new private session-work directory. Correlate
numeric request task_id with task await_chain, and compare timestamp/PID/PPID
with the separately activated kernel installation-write audit. Preserve the
originals privately and publish only the metadata required for attribution.
