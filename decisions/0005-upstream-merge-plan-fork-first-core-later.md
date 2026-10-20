# ADR 0005: merge upstream into the fork first, update graphiti-core later

> Document version v3 — 2026-10-20

Status: accepted (JL, 2026-10-20: "ADR 0005 순서대로 병합 진행해, watchdog는 제거"). Steps 1-2 executed on
2026-10-20; see "Execution". Step 3 validation and the core lock update are recorded below.
Source publication and runtime verification are separate milestones.

## Context (measured 2026-10-20)

- The unattended update night of 2026-10-20 02:00 blocked two candidates: the fork merge (local tip at that time `8f3169a` onto upstream
  `5961293`) and graphiti-core 0.30.2 -> 0.31.1 in `mcp_server/uv.lock`. Evidence:
  `~/coding/session-work/20261019-spark-update-night-20261020/` (`core-execution/CORE-RESULT-v1.md`,
  `db-review/graphiti-merge-tree.txt`).
- Production (`graphiti-mcp.service`, streamable HTTP on 127.0.0.1:8765) runs from this checkout's `mcp_server/` on the
  **PyPI** graphiti-core pinned in `mcp_server/uv.lock` (0.30.2). The in-tree `graphiti_core/` is not what production runs.
- `delete_episode` raised `TypeError` from 2026-09-28 (41bb660) to 2026-10-20: the tool passed `group_id=` to the PyPI
  `remove_episode(episode_uuid)`. Fixed in `aa4cccd` with upstream's `_client_for_group` pattern, deployed and checked on a
  throwaway group (3 nodes + 3 edges -> 0, graph then dropped).
- At `aa4cccd` (measured 2026-10-20 evening), upstream/main is `3cc723c`, 55 commits past the merge base `c035afb79`;
  the fork has 31 local commits.
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
- **Default model changes** (#1999: gpt-4.1-nano defaults replaced). The fork's reranker patch (`mcp_server/src/services/factories.py`,
  `model=config.model`) auto-merges, but the result must be read: the reranker must still use the configured local model.
- **Core, unreleased**: #2025 (`c38d7c1`, in no release tag on 2026-10-20) changes `remove_episode` to keep edges that other
  episodes restate. The installed 0.30.2 deletes every edge whose *first* episode is the removed one, even if later episodes
  restated it.

## Fork protections to keep through the merge

1. `extra_body` injection and Qwen thinking off + `json_schema` structured output (ADR 0004; `mcp_server/src/config/schema.py`,
   `mcp_server/src/services/factories.py`, `mcp_server/config/config-local-kure.yaml`).
2. Reranker uses the configured LLM model (ADR 0001 §3; `mcp_server/src/services/factories.py`).
3. Loopback-only binding and FalkorDB auth/AOF (ADR 0002; `mcp_server/docker/run-falkordb.sh`, `mcp_server/docker/docker-compose-falkordb.yml`).
4. Secrets read at start from `~/.config/graphiti/falkordb.env` and `~/.config/qwen38/api-key` (`mcp_server/run-mcp.sh`), none in the repo.
5. `coerce_group_ids` (scalar `group_ids` accepted) and the group_id safety net from a7b4847.
6. Queue logging changes in `mcp_server/src/services/queue_service.py`.
7. The stdio idle watchdog (`mcp_server/src/utils/idle_watchdog.py`). It wraps the private `mcp._tool_manager.call_tool`
   (`mcp_server/src/graphiti_mcp_server.py` lines 1321-1327 at `aa4cccd`). Whether `MCPServer` in SDK 2.x still has `_tool_manager` is not checked.
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

## Execution (2026-10-20)

- Intake gate: Opus reviewed upstream `3cc723c` (55 commits) before `git merge` (PyPI-only lock sources with sha256, no
  telemetry/exec, `--destroy-graph` still opt-in, Jev/Decisions clients opt-in).
- Conflicts resolved by taking upstream for all 5 files, then re-applying fork code:
  - `graphiti_core/graphiti.py` and `tests/test_request_scope_concurrency.py`: upstream (41bb660's core change dropped).
  - `mcp_server/src/graphiti_mcp_server.py`: upstream (SDK 2.x, group routing for every tool, incl. `delete_episode`),
    plus the fork's `add_memory` warning for a missing / `_unassigned` group_id.
  - `mcp_server/tests/test_live_falkordb_int.py`: upstream, plus the fork's "episode absent after delete" assertion.
  - `mcp_server/docker/docker-compose-falkordb.yml`: upstream (all ports on 127.0.0.1).
  - `mcp_server/uv.lock`: regenerated; mcp 2.2.0, graphiti-core pinned to 0.30.2 (`>=0.30.2,<0.31` in
    `mcp_server/pyproject.toml`). The 0.31.1 floor could be relaxed: upstream's server imports nothing 0.31-only that the
    unit tests or the live sweep below reached.
- Idle watchdog removed (JL): `mcp_server/src/utils/idle_watchdog.py`, its three test files, `idle_timeout_seconds` in
  `mcp_server/src/config/schema.py` and `mcp_server/config/config-local-kure.yaml`, the queue hook in
  `mcp_server/src/services/queue_service.py` (now identical to upstream).
- Kept and checked by diff: `extra_body` injection and the reranker model patch (`mcp_server/src/services/factories.py`),
  `run-mcp.sh`, `docker/run-falkordb.sh`, `config-local-kure.yaml`.
- Unit tests (`mcp_server/tests`, excluding the live file): 94 passed; the same 15 failures and 3 collection errors occur on
  pure upstream `3cc723c` (environment-dependent integration tests, missing optional extras), so none comes from the merge.
  `ruff check` and `ruff format --check` pass.
- FalkorDB snapshot before any live call: `BGSAVE` then a copy of `/var/lib/falkordb/data` (rdb + AOF) to
  `/home/jl/Documents/graphiti-falkordb-snapshots/20261020-pre-merge/` (sha256 list next to it). Outside git: case data.
- Live sweep: merged server on 127.0.0.1:8766 against the production FalkorDB, driven by an mcp 1.30 client (the version
  `graphiti-session-sync-v2` uses). Real groups read-only (`get_episodes`, `search_nodes`, `search_memory_facts`); writes only
  to throwaway group `merge-sweep-20261020` (ADR 0004 contradiction pair, saga, `get_episode_entities`, `summarize_saga`,
  `delete_episode`, `clear_graph`). 18/18 passed; the old fact got `invalid_at` 2026-06-01; both episodes processed in
  40 s. Node/edge/episode counts of `infra-memory`, `infra-wiki`, `coding-wiki` were identical before and after. The empty
  throwaway graph was dropped with `GRAPH.DELETE`.


## Step 3 validation and dependency promotion (2026-10-20, Codex continuation)

The lock now selects PyPI graphiti-core **0.31.1**, constrained to `>=0.31.1,<0.32`.
Only that package changed; MCP remains 2.2.0. The installed production environment
was kept on 0.30.2 throughout candidate validation. Deployment follows the independent
latest-Sol gate, protected exact-SHA CI/push, `uv sync --locked`, and a service restart.
The continuation's live closeout is retained under
`session-work/20261020-core-followup/CORE-FOLLOWUP-v2.md` (local only).

### Newly measured evidence

- Original human approval was re-read in Claude session `0680ca13-9d13-4a07-bcbe-dc2797118ae3`,
  user event `2026-10-20T14:06:29.519Z`: "ADR 0005 순서대로 병합 진행해, watchdog는 제거".
- Fork production/main and remote main matched `ce046f065998ce57797c49fb88e57c7a04d818f8` at intake.
  The service was healthy. Post-deployment v2 timer runs, including 23:41:18 KST,
  ended successfully with `build_errors=0` and `dead_letter_count_this_run=0`.
  These idle runs alone do not prove an ingestion occurred.
- The actual `GraphitiHttpClient` from graphiti-session-sync's existing MCP 1.30
  environment called production's MCP 2.2 server with a synthetic episode in a new
  test group. Persistence, explicit-group provenance, nonempty node/fact searches,
  edge get/delete, episode deletion, and clear all passed. No consumer code change
  was necessary. Callers of the three UUID tools must pass the actual `group_id`.
- A new completed `BGSAVE` RDB was copied to
  `/home/jl/Documents/graphiti-falkordb-snapshots/20261020-pre-core-2345/`.
  `dump.rdb` is 7,320,346 bytes, SHA256
  `9b9f0758bce5c0fb5bb75b11497f42b6489bdad562e4fe7ca99ad6cb86194616`.
  `SHA256SUMS` verified. This is a consistent RDB checkpoint, not a stopped-container
  filesystem/AOF copy; the directory suffix is an identifier, not the capture time.
- Restore drill: a disposable FalkorDB using the exact production image digest
  `sha256:0d793d4b249a9cf0837faa9f30fea1b86fb50086fc8aa21e9447078a07f995bc`
  loaded that read-only RDB on loopback port 6381. It used `--entrypoint redis-server`
  explicitly: the image's normal run.sh ignores positional Redis options.
  Candidate MCP ran on 8766 with a separate environment using the PyPI 0.31.1 wheel.
- Range/fulltext index creation passed on the **copy** of `infra-memory`, `infra-wiki`,
  `coding-wiki`, `default_db`, and `_unassigned`. Node/edge/episode counts stayed equal
  to production. The three populated groups respectively had 248/705/20,
  218/497/45, and 96/158/18; the sentinels were empty.
- Fixed read queries (`get_episodes`, `search_nodes`, `search_memory_facts`, ten
  results each) across the three populated groups returned identical sorted UUID-set
  hashes on production 0.30.2 and the copy with 0.31.1: nine comparisons passed.
  Only hashes/counts were saved in the repository's local work area, not real content.
- Candidate tests against its **installed wheel**, from `mcp_server/`: 52 tests for
  parity/routing/delete/config plus 36 factory tests passed (88 total). A first test
  invocation from the repository root imported in-tree core and stopped on missing
  optional google-genai; it is not candidate evidence. The optional Gemini reranker
  collection was not claimed as passing. Factory tests cover local extra_body wiring;
  no provider extras were installed into production.
- Synthetic CRUD/search with the real v2 consumer passed on the candidate, including
  nonempty searches and explicit-group edge deletion. The ADR 0004 contradiction pair
  passed 19/19 on both current 0.30.2 and candidate 0.31.1 when all stored synthetic
  edges were inspected directly: the old affiliation had invalid_at 2026-06-01.
  The earlier candidate check through ranked search returned 18/19 (old invalidated
  fact absent from the result). That run remains a failed search-based assertion;
  the direct-edge rerun does not prove the cause of that earlier omission.
  Both direct-edge sweeps took about 44 s under shared LLM load; no speedup claim.
- Only local vLLM 8012 and KURE 8002 served model requests in the candidate log.
  Factories still select the local OpenAI-compatible clients and no `llm_runtime` is
  supplied, so Jev/OpenAI Decisions routing remains opt-in and inactive.
- Git changes are limited to the dependency constraint/lock and these two documents.
  No spark-infra files or existing worktrees were changed/removed. All test writes and
  deletes were confined to this continuation's newly-created synthetic groups.

### Remaining limitation

As verified from GitHub releases and PyPI, 0.31.1 is the latest release. Commit
`c38d7c1185ed5d6b699a8e2ba0856bc6a2cf0f1e` (#2025) is **after** v0.31.1.
The wheel still deletes an edge whose first provenance episode is removed, even if
another episode restated it. This update does not fix that pre-existing defect.
Do not delete real episodes to test it. Re-run the restated-fact check on a fresh
synthetic group when a release containing #2025 is available; do not substitute
in-tree code for the pinned PyPI artifact.

### Restore / rollback runbook

1. Prefer application rollback if service/tool checks regress: preserve logs and stop
   new ingestion, revert this dependency promotion through the normal independent
   review and push gates, run `uv sync --locked` in `mcp_server/`, then restart
   `graphiti-mcp.service`. Check health, explicit-group reads and v2 consumer behavior.
   The backed-up pre-change pyproject/lock and `ce046f0` identify the 0.30.2 state.
2. Keep production data in place for an application-only rollback. No data-format
   migration is part of this change. A snapshot restore discards writes made after
   its checkpoint and requires a separate, explicit recovery decision.
3. Before any data recovery, verify SHA256SUMS and preserve the then-current volume
   plus AOF; never overwrite the only copy. Restore the RDB into a **new** isolated
   volume using the image digest above, appendonly disabled, an explicit redis-server
   entrypoint, and loopback binding. Do not combine the old RDB with newer AOF files:
   Redis may prefer AOF and silently bypass the intended checkpoint.
4. Check GRAPH.LIST and the five groups' node/edge/episode counts against the values
   above; build indices and run read comparisons there first. This drill was executed
   on the disposable instance. Switching production to that volume is a separate
   recovery operation; it was not performed by this validation.

### Work units and reuse

| Unit | Inputs and dependencies | Status and evidence | Reuse / rerun reason |
|---|---|---|---|
| Intake and post-merge consumer | ce046f0, original approval, live units, existing MCP 1.30 consumer | Newly checked; timer, production-probe-v2.log | Historical health and idle timer success were insufficient |
| Backup, restore, indices and reads | New RDB hash, pinned FalkorDB image, production 0.30.2 and isolated 0.31.1 | Newly checked; snapshot.log, inventory.log, index-copy.log, compare-reads.log | Backup was restored and counted, not assumed usable |
| Candidate behavior and dependency promotion | PyPI wheel, unchanged local model config, new dependency lock | 88 unit checks; candidate-probe.log; candidate-temporal-v2.log 19/19 | Existing merge sweep was adapted; all affected runtime checks rerun |
| Publish and activate | Committed promotion SHA, post-lock direct-child Sol APPROVE, exact-SHA CI | Required after this source milestone; live closeout retained separately | Old Opus approval for ce046f0 cannot approve a new SHA |

Independent reads and environment setup were batched; shared-file changes and review,
push and activation stay sequential. No prior test result was reused as a pass for
0.31.1. Timings under shared GPU load do not establish an old/new performance difference.
