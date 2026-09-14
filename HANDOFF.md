# HANDOFF — graphiti-src (2026-09-15 재구성)

## 무엇을 했나

2026-09-15, JL 지시로 이전 graphiti-src/graphiti-eval 로컬 구성을 백업 없이 전부 삭제하고
처음부터 재구성했다(사용자 명시적 승인, 백업 불필요 판단).

1. `~/coding/graphiti-src` — `git clone https://github.com/getzep/graphiti` 새로 수행.
2. `mcp_server/config/config-local-kure.yaml` — LLM=로컬 vLLM, embedder=KURE-v1(local shim),
   DB=FalkorDB 신규 작성.
3. `mcp_server/run-mcp.sh` — 신규 작성. `~/.hermes/.env`(FALKORDB_PASSWORD)와
   `~/.hermes/config.yaml`(vLLM api_key)에서 실행 시점에 키를 끌어옴, 레포엔 평문 없음.
4. `~/coding/graphiti-eval` — `kure_server.py`(OpenAI 호환 `/v1/embeddings`, KURE-v1) 새로 작성,
   `pyproject.toml`로 uv 관리.
5. `kure-embed.service` — 처음으로 실제 systemd user 유닛 생성·enable·기동
   (이전엔 문서에만 "enabled"라고 적혀 있고 실제 유닛은 없었던 버그를 이번에 바로잡음).
6. FalkorDB 컨테이너 재생성 — 볼륨(`graphiti_falkordb_data`) 영속화 + 비밀번호 설정
   (이전엔 볼륨 미마운트 상태였던 것도 바로잡음).
7. MCP 재등록(`claude mcp add graphiti-legal ...`) — `claude mcp list`로 연결 확인 완료.

## 검증됨

- `curl http://127.0.0.1:8002/v1/embeddings` — 실제 벡터 반환 확인.
- `docker exec graphiti-falkordb redis-cli -a <pw> ping` — PONG.
- `claude mcp list` → `graphiti-legal: ... ✔ Connected`.
- `systemctl --user status kure-embed.service` → active (running), linger=yes로 재부팅 후에도
  자동 기동됨(로그인 없이).

## 검증 안 됨 / 다음 세션 과제

- **⚠️ Cross-encoder reranker가 로컬 vLLM에서 깨진다(잠복, 검토 발견 2026-09-15).**
  `llm.provider: openai`라서 `CrossEncoderFactory`가 `OpenAIRerankerClient`를 고르고
  BGE 폴백까지 가지 않는데, 이 클라이언트의 기본 모델(`gpt-4.1-nano`)을 vLLM이 모른다
  (실측: `404 The model 'gpt-4.1-nano' does not exist`). 지금까지 쓴 검색 경로는
  cross-encoder를 안 불러서 겉으론 멀쩡하지만, reranking을 쓰는 검색으로 넘어가면 즉시 에러.
  해결 전엔 cross-encoder 필요한 기능을 쓰지 말 것. 상세: ADR 0001 §3.
- **실제 episode 추가 → 시간성 검증**(구 사실 invalid_at 자동 처리)은 이번 세션에서
  재현하지 않았다. 이전 `mcp-live-test` 그래프로 했던 검증을 다시 해봐야 신뢰 가능.
- **Dropbox 연동**은 설계만 논의됐고 코드는 없음. dropbox-map(`~/coding/dropbox-map/dropbox.db`,
  SQLite)의 사건별 문서를 episode로 넣는 파이프라인이 필요.
- **의뢰인 실데이터 투입 전 접근 통제** 미검토 — FalkorDB는 로컬 단일 사용자 컨테이너 전제.
- 예전에 있었던 `mcp_server/src/graphiti_mcp_server.py`에 대한 로컬 패치는 **내용을 알 수 없어
  복구 못 함**. 이번 재클론에서는 업스트림 자체에 reranker 폴백 로직이 이미 들어있어서
  당장은 패치 없이도 동작 확인됨(ADR 0001 참고). 추후 유사 에러 나오면 그때 다시 패치할 것.

## 되돌리는 법

문제 생기면: `claude mcp remove graphiti-legal`, `systemctl --user disable --now kure-embed.service`,
`docker rm -f graphiti-falkordb` (볼륨은 `docker volume rm graphiti_falkordb_data`로 별도 삭제해야
지워짐 — 컨테이너만 지우면 데이터 남음).
