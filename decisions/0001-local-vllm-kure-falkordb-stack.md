# ADR 0001: 로컬 vLLM + KURE-v1 + FalkorDB 스택으로 Graphiti MCP 구성

## 상태
Accepted (2026-09-15)

## 배경
2026-09-15, JL 지시로 graphiti-src(이 저장소)와 graphiti-eval(자매 저장소)의 이전 로컬 구성을
전부 삭제하고 처음부터 재구성했다. 이전 구성에는 `mcp_server/src/graphiti_mcp_server.py`에 대한
로컬 패치(reranker 관련으로 추정)와 커밋되지 않은 config/런처 파일들이 있었으나, 백업 없이
삭제하기로 JL이 명시적으로 승인했다 — 그 패치의 정확한 diff 내용은 복구 불가능하다.

## 결정
1. **LLM**: 로컬 vLLM(`http://127.0.0.1:8012/v1`, 모델 `qwen3.8-27b-nvfp4-a767244d`)을
   openai 호환 provider로 사용. API 키는 `~/.hermes/config.yaml`의 것을 그대로 재사용
   (레포에 평문 커밋 안 함, `run-mcp.sh`가 실행 시점에 추출).
2. **Embedder**: KURE-v1(`nlpai-lab/KURE-v1`, 1024dim, 한국어 특화)을
   `~/coding/graphiti-eval/kure_server.py`가 OpenAI 호환 `/v1/embeddings`로 서빙(포트 8002).
   graphiti-core 자체는 sentence-transformers provider를 config 스키마로 노출하지 않아서
   이 shim이 여전히 필요하다(업스트림에 없는 이유를 확인함, `config/config.yaml` 실측).
3. **Reranker**: 패치 없이 업스트림 그대로 사용. `CrossEncoderFactory`는 LLM→embedder
   provider 순으로 reranker를 찾고 없으면 로컬 BGE로 폴백한다(`mcp_server/src/services/factories.py`
   실측). 이 설정은 `llm.provider: openai`라서 **첫 순회에서 `OpenAIRerankerClient`가
   선택되고 BGE 폴백까지 가지 않는다.** `OpenAIRerankerClient`는 model을 안 주면
   `gpt-4.1-nano`를 기본값으로 호출하는데, 실측 결과 로컬 vLLM은 이 모델을 모른다
   (`404 The model 'gpt-4.1-nano' does not exist`). 다만 MCP의 검색 경로(`NODE_HYBRID_SEARCH_RRF`,
   `EDGE_HYBRID_SEARCH_RRF` 등)는 지금까지 cross-encoder를 호출하지 않아 **당장은 안 터진다** —
   cross-encoder 재랭킹을 쓰는 검색 모드로 넘어가는 순간 터질 잠복 문제다(미해결, HANDOFF 참고).
   `uv sync --extra providers`로 sentence-transformers는 설치해 뒀으니, 이 경로를 강제로
   BGE 폴백시키려면 `llm`/`embedder`에서 reranker를 못 찾게 하거나 코드를 직접 손봐야 한다.
4. **DB**: FalkorDB 도커, 비밀번호는 `~/.hermes/.env`의 `FALKORDB_PASSWORD`를 재사용(다른
   서비스와 공유), 볼륨 `graphiti_falkordb_data`로 영속화.

## 왜 upstream(getzep/graphiti)에 직접 push 안 하는가
`origin`을 `lntp-k/graphiti`(fork, push 가능)로 두고, `upstream`(getzep/graphiti 원본)은
push를 `DISABLED`로 등록해 pull-only로만 쓴다. 로컬 커스터마이징(config, 런처)은
`origin`에만 push한다 ([[feedback_git_fork_workflow]] 원칙).

## 검증
`claude mcp list` 실측: `graphiti-legal: ... ✔ Connected` (2026-09-15).
