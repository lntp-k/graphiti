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
3. **Reranker**: 패치 없이 업스트림 그대로 사용. 이번에 새로 clone한 버전의
   `CrossEncoderFactory`는 LLM/embedder provider에서 reranker를 자동으로 고르고
   없으면 로컬 BGE로 폴백하는 로직을 **이미 내장**하고 있었다(`mcp_server/src/services/factories.py`
   실측) — 예전에 필요했던 "reranker 패치"가 지금은 upstream에 흡수된 것으로 보인다.
   `uv sync --extra providers`로 sentence-transformers만 추가 설치하면 패치 없이 동작 확인됨.
4. **DB**: FalkorDB 도커, 비밀번호는 `~/.hermes/.env`의 `FALKORDB_PASSWORD`를 재사용(다른
   서비스와 공유), 볼륨 `graphiti_falkordb_data`로 영속화.

## 왜 upstream(getzep/graphiti)에 직접 push 안 하는가
이 저장소의 origin은 fork가 아니라 upstream 그 자체다. 로컬 커스터마이징(config, 런처)은
`lntp-k/graphiti`(fork)에만 push하고 upstream은 pull-only로 유지한다
([[feedback_git_fork_workflow]] 원칙).

## 검증
`claude mcp list` 실측: `graphiti-legal: ... ✔ Connected` (2026-09-15).
