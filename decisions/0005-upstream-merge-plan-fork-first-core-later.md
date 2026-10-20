# ADR 0005: merge upstream into the fork first, update graphiti-core later

> Document version v1 — 2026-10-20

Status: proposed (2026-10-20). Waiting for JL's go-ahead before the merge itself is executed. Nothing in this ADR has
been run yet except the measurements quoted below.

## Context (measured 2026-10-20)

- The unattended update night of 2026-10-20 02:00 blocked two candidates: the fork merge (local `8f3169a` onto upstream
  `5961293`) and graphiti-core 0.30.2 -> 0.31.1 in `mcp_server/uv.lock`. Evidence:
  `~/coding/session-work/20261019-spark-update-night-20261020/` (`core-execution/CORE-RESULT-v1.md`,
  `db-review/graphiti-merge-tree.txt`).
- Production (`graphiti-mcp.service`, streamable HTTP on 127.0.0.1:8765) runs from this checkout's `mcp_server/` on the
  **PyPI** graphiti-core pinned in `mcp_server/uv.lock` (0.30.2). The in-tree `graphiti_core/` is not what production runs.
- `delete_episode` raised `TypeError` from 2026-09-28 (41bb660) to 2026-10-20: the tool passed `group_id=` to the PyPI
  `remove_episode(episode_uuid)`. Fixed in `aa4cccd` with upstream's `_client_for_group` pattern, deployed and checked on a
  throwaway group (3 nodes + 3 edges -> 0, graph then dropped).
- upstream/main is now `3cc723c`, 55 commits past the merge base `c035afb79`; the fork has 31 local commits.
  `git merge-tree --write-tree HEAD upstream/main` reports 5 conflicting files (the night run against `5961293` saw 2):

| File | Fork side | Upstream side | Resolution |
|---|---|---|---|
| `mcp_server/src/graphiti_mcp_server.py` | idle watchdog wiring, `extra_body`/reranker hooks, `delete_episode` fix (aa4cccd), `coerce_group_ids` | MCP SDK 2.x (`MCPServer`, #1921), group routing for every tool (#1926), localhost default (#2032) | Take upstream, then re-apply the fork protections listed below |
| `mcp_server/tests/test_live_falkordb_int.py` | reads the named graph after delete (1cf66bd) | rewritten for 2.x / group routing | Take upstream; keep the fork's "episode absent after delete" assertion if upstream lacks it |
| `graphiti_core/graphiti.py` | `remove_episode(..., group_id=None)` (41bb660) | original signature + restated-fact fix (#2025) | Take upstream; drop 41bb660's core change and its tests in `tests/test_request_scope_concurrency.py` |
| `mcp_server/docker/docker-compose-falkordb.yml` | ports bound to 127.0.0.1 (a7b4847) | also bound to 127.0.0.1 (#2032) | Take upstream; confirm every port is still loopback-only |
| `mcp_server/uv.lock` | graphiti-core 0.30.2, mcp 1.30.0 | graphiti-core 0.31.1, mcp 2.2.0 | Regenerate with `uv lock` after the merge; pin graphiti-core back to 0.30.2 (see "Order") |

## Upstream changes that matter here

- **MCP SDK 1.30 -> 2.x** (#1921, `mcp>=2,<3`, `FastMCP` -> `MCPServer` from `mcp.server.mcpserver`). This is the bulk of
  the merge work.
- **Group routing for all tools** (#1926: `_driver_for_group` / `_client_for_group`). It also fixes defects the fork still
  has: `get_episodes`, `clear_graph` and `summarize_saga` read or write the default graph through `client.driver`, so for a
  non-default group they miss the group's graph (`get_episodes` returned nothing for a stored episode on 2026-10-20).
- **Localhost-only defaults** (#2032) overlap the fork's ADR 0002 loopback binding.
- **Default model changes** (#1999: gpt-4.1-nano defaults replaced). The fork's reranker patch (`factories.py`,
  `model=config.model`) auto-merges, but the result must be read: the reranker must still use the configured local model.
- **Core, unreleased**: #2025 (`c38d7c1`, in no release tag on 2026-10-20) changes `remove_episode` to keep edges that other
  episodes restate. The installed 0.30.2 deletes every edge whose *first* episode is the removed one, even if later episodes
  restated it.

## Fork protections to keep through the merge

1. `extra_body` injection and Qwen thinking off + `json_schema` structured output (ADR 0004; `config/schema.py`,
   `services/factories.py`, `config-local-kure.yaml`).
2. Reranker uses the configured LLM model (ADR 0001 §3).
3. Loopback-only binding and FalkorDB auth/AOF (ADR 0002; `docker/run-falkordb.sh`, compose file).
4. Secrets read at start from `~/.config/graphiti/falkordb.env` and `~/.config/qwen38/api-key` (`run-mcp.sh`), none in the repo.
5. `coerce_group_ids` (scalar `group_ids` accepted) and the group_id safety net from a7b4847.
6. Queue logging changes in `services/queue_service.py`.
7. The stdio idle watchdog (`utils/idle_watchdog.py`). It wraps the private `mcp._tool_manager.call_tool`
   (`graphiti_mcp_server.py` around line 1326). Whether `MCPServer` in SDK 2.x still has `_tool_manager` is not checked.
   It only runs on stdio; production is HTTP, Claude Code's `graphiti-legal` is HTTP, and `graphiti-session-sync-v2` is HTTP.
   The only stdio user, `graphiti-session-sync.service` (v1), is disabled. Options: port it to a public 2.x hook, or drop it
   and record that stdio has no idle timeout. Decide at merge time from what 2.x exposes.

## Order

1. **Fork merge first, core stays at 0.30.2.** Merge upstream/main (or the newest commit at that time) on a branch, resolve
   as above, regenerate `uv.lock` with mcp 2.x but graphiti-core still 0.30.2 if upstream's `>=0.31.1` floor can be relaxed
   locally; if it cannot, the merge and the core update become one step and the core checks below apply to it.
2. Checks before the gate: `ruff check`, `ruff format --check` (internal-ci profile `graphiti-lint`), mcp_server unit tests,
   then the live tool sweep against a throwaway group on a second port (add_memory, get_episodes, search_nodes,
   search_memory_facts, delete_episode, clear_graph, summarize_saga), and a `graphiti-session-sync-v2` run against it
   (its 1.x client talking to a 2.x server is not checked).
3. **graphiti-core update later**, as its own change, preferably to the first release that contains #2025.

## What a later graphiti-core update must verify

- A cold FalkorDB snapshot taken before the switch (container stopped or `BGSAVE` completed and copied), with a written
  restore procedure; no migration runs against the only copy.
- Index/DDL changes on FalkorDB 6.x (#1963 fulltext DDL form) on a copy of the real data: the server starts, indices build,
  no errors in the journal.
- CRUD and search on real groups, read-only first: `get_episodes`, `search_nodes`, `search_memory_facts` return the same
  results before and after for a fixed set of queries per group; then add/delete on a throwaway group.
- Extraction prompt default change (#1997 "C2" combined extraction prompt, #1964 extraction/dedup/temporal changes): run the
  ADR 0004 contradiction pair on a throwaway group with the local Qwen (thinking off, `json_schema`) and compare
  entity/edge counts, `invalid_at` handling and latency with the 0.30.2 numbers in ADR 0004.
- Dedupe clients (#1984 Jev/OpenAI Decisions) stay off unless configured; confirm no new paid API call is made.
- `remove_episode` on a group where a fact is restated by two episodes keeps the fact (#2025), if the new release has it.

## Consequences

- Until the merge, the fork's `get_episodes`, `clear_graph` and `summarize_saga` remain wrong for non-default FalkorDB
  groups. Until a core with #2025 ships, `delete_episode` on real data can remove facts that later episodes restated.
- This ADR is a plan. Executing the merge needs JL's approval.
