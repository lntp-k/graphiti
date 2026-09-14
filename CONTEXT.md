# CONTEXT — 이 폴더에서 알아야 할 것

2026-09-15, 이전 구성을 전부 삭제하고 처음부터 재구성했다(JL 지시). 설계 결정 근거는
[`decisions/0001-local-vllm-kure-falkordb-stack.md`](decisions/0001-local-vllm-kure-falkordb-stack.md),
현재 상태는 [`HANDOFF.md`](HANDOFF.md)를 볼 것.

## 이 폴더 vs `~/coding/graphiti-eval`

| | 담당 |
|---|---|
| `~/coding/graphiti-src` (여기) | getzep/graphiti 클론 + `mcp_server/`. 우리 설정(`config/config-local-kure.yaml`)·런처(`run-mcp.sh`) |
| `~/coding/graphiti-eval` | KURE-v1 임베딩 서버(`kure_server.py`, systemd `kure-embed.service`, 8002) |

## origin / 저장소 관계

- `origin` = `https://github.com/lntp-k/graphiti.git` (내 fork, push 가능).
- `upstream` = `https://github.com/getzep/graphiti` (원본, **push DISABLED**로 등록 — pull-only).
- 로컬 커스터마이징(`mcp_server/config/`, `mcp_server/run-mcp.sh`, `decisions/`, 이 파일)은
  `origin`(fork)에만 push한다. upstream에는 절대 push하지 않는다
  ([[feedback_git_fork_workflow]]).

## 스택 (2026-09-15 재구성)

- LLM: 로컬 vLLM(8012, `qwen3.8-27b-nvfp4-a767244d`), openai 호환.
- Embedder: KURE-v1 (graphiti-eval 의 shim, 8002).
- DB: FalkorDB 도커(`graphiti-falkordb`, 볼륨 `graphiti_falkordb_data` 영속, 비밀번호는
  `~/.hermes/.env`의 `FALKORDB_PASSWORD` 공유).
- MCP 등록명 `graphiti-legal` (user scope, `~/.claude.json`), stdio transport.

## 실데이터 취급

지금 FalkorDB는 완전히 빈 상태(2026-09-15 재구성 직후)다. **의뢰인 실데이터를 넣기 전에
접근 통제를 먼저 검토할 것** — 지금은 로컬 컨테이너 단일 사용자 전제로만 설정돼 있다.
