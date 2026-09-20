# ADR 0003: session-sync supplies PATH in its own unit; the launcher is left alone

Status: accepted (2026-09-20). Does not replace an earlier ADR.

## Context (measured 2026-09-19/20)

`graphiti-session-sync.service` failed every 5 minutes from 2026-09-15 08:39 (about 1,200 runs). Two stacked causes:

1. Its drop-in `10-local-llm.conf` pointed at `mcp_server/run-mcp-local.sh`, which the 2026-09-15 wipe deleted (never tracked
   in git — no branch history contains it). The rebuilt `mcp_server/run-mcp.sh` is already the all-local launcher, so the
   premise of the drop-in ("run-mcp.sh needs OPENROUTER_API_KEY") no longer holds.
2. With the drop-in out of the way, `run-mcp.sh` died with `exec: uv: not found`: it calls `uv` by name and the systemd user
   unit's default PATH has no `~/.local/bin`.

## Decision

- Rename `10-local-llm.conf` to `10-local-llm.conf.retired-20260919` (systemd only reads `*.conf`), so the base unit's
  `run-mcp.sh` ExecStart applies.
- Add `20-path.conf` with `Environment=PATH=/home/jl/.local/bin:/usr/local/bin:/usr/bin:/bin` to that unit only.
- Do not edit `run-mcp.sh`.

## Rejected

- **Make `run-mcp.sh` PATH-independent** (absolute `uv` path). Arguably the better long-term fix: it would protect every
  bare-PATH caller. Rejected for now because it edits a tracked file of the fork (merge-gate review) to fix one consumer,
  while the drop-in is scoped and reversible. Left as an open decision in HANDOFF.
- **Restore `run-mcp-local.sh`.** Never tracked, contents unknown; the rebuilt `run-mcp.sh` already does the job.
- **Delete the stale drop-in.** Renaming keeps the old content for reference at no cost.

## Consequences

- Verified: manual `systemctl --user start` succeeded twice (25 files ingested each), then 245 runs succeeded through 2026-09-20 11:10 (the two manual
  starts included), with one failure (the pre-fix `uv: not found` run at 14:26).
- The fix lives in host config outside any repository, so it is not versioned. Any other unit or script that calls
  `run-mcp.sh` under a bare PATH will hit the same `uv: not found`.
