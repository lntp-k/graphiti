# ADR 0004: local Qwen3.8 runs with thinking off and `json_schema` structured output

Status: accepted (2026-09-26). Does not replace an earlier ADR. Amends the `structured_output_mode: "json_object"` choice
made in `config-local-kure.yaml` on 2026-09-15 (ADR 0001 era).

## Context (measured 2026-09-26, shared MCP on 127.0.0.1:8765, local vLLM 8012)

Two contradictory episodes ("Kim is at firm A since 2026-01-01" then "Kim left A for firm B on 2026-06-01") were sent to an
isolated group through `add_memory`, then the graph was read directly from FalkorDB.

| Config | Result |
|---|---|
| thinking on (server default) + `json_object` | first episode lost, second took 211 s; 6 LLM calls |
| thinking off + `json_object` | 4 of 4 episodes failed `ExtractedEntities` validation (model echoed the schema, `$defs`) |
| thinking off + `json_schema` | 12.6 s and 25 s; both stored; old `WORKS_AT` edge got `invalid_at`=2026-06-01 and `expired_at` |

Direct vLLM probes: a 96-token prompt took 7 s with 155 completion tokens (thinking on) versus 2 s with 48 tokens (off);
`response_format: json_schema` is enforced by vLLM 8012 with thinking both on and off. The queue (`services/queue_service.py`)
logs a failed episode and moves on: no retry, and `add_memory` has already answered "queued".

## Decision

- Add `llm.providers.openai.extra_body` (`config/schema.py`, `OpenAIProviderConfig`). `services/factories.py`
  (`_client_with_extra_body`) wraps the SDK client's `chat.completions.create` so the dict is merged into every request of the
  generic client. `config-local-kure.yaml` sets `chat_template_kwargs.enable_thinking: false`.
- Switch `structured_output_mode` to `json_schema` in the same file.
- Test: `tests/test_factories.py::test_extra_body_is_sent_with_every_chat_completion` (+ default-client test).

## Rejected

- **Turn thinking off on the vLLM server** (`--default-chat-template-kwargs`). vLLM 8012 is shared with Hermes; the switch is
  per request, so only Graphiti pays for it.
- **A small proxy that injects `chat_template_kwargs`.** Works, but adds a service, a port and an OnFailure unit for three lines
  of client code.
- **Patch `graphiti_core/` in this clone.** The MCP server runs the PyPI wheel pinned in `mcp_server/uv.lock` (0.30.2), not
  this directory, so such a patch would have no effect.
- **Keep thinking on.** About 3.5 minutes per episode makes any real backlog impractical.
- **Keep `json_object` with thinking off.** Fails every episode (table above).

## Consequences

- `extra_body` is a fork-local addition to two upstream files; a future upstream merge may conflict there.
- Extraction quality with thinking off was compared on one example only (entities and facts were correct); a legal-domain
  sample has not been run. Re-check before real client data goes in.
- A failed episode is still dropped silently by the queue. That is unchanged and is the next thing to fix (HANDOFF).
