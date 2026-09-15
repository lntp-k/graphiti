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

- ~~Cross-encoder reranker가 로컬 vLLM에서 깨진다~~ → **2026-09-15 같은 세션에서 패치로 해결.**
  `mcp_server/src/services/factories.py`의 `_reranker_for_provider` openai 분기에
  `model=config.model`을 추가해 reranker가 `gpt-4.1-nano` 대신 이미 설정된 LLM 모델
  (`qwen3.8-27b-nvfp4-a767244d`)을 쓰도록 고침. 실측: vLLM이 해당 모델로 200 응답.
  상세: ADR 0001 §3. (MCP 도구를 통한 end-to-end 재랭킹 호출까지는 재현 안 함 — 다음 과제로 유지)
- **실제 episode 추가 → 시간성 검증**(구 사실 invalid_at 자동 처리)은 이번 세션에서
  재현하지 않았다. 이전 `mcp-live-test` 그래프로 했던 검증을 다시 해봐야 신뢰 가능.
- **Dropbox 연동**은 설계만 논의됐고 코드는 없음. dropbox-map(`~/coding/dropbox-map/dropbox.db`,
  SQLite)의 사건별 문서를 episode로 넣는 파이프라인이 필요.
- **의뢰인 실데이터 투입 전 접근 통제** 미검토 — FalkorDB는 로컬 단일 사용자 컨테이너 전제.
- 예전에 있었던 `mcp_server/src/graphiti_mcp_server.py`에 대한 로컬 패치는 **내용을 알 수 없어
  복구 못 함**. 이번 재클론에서는 업스트림 자체에 reranker 폴백 로직이 이미 들어있어서
  당장은 패치 없이도 동작 확인됨(ADR 0001 참고). 추후 유사 에러 나오면 그때 다시 패치할 것.

## stdio idle-timeout 배포 + 구버전 잔존 프로세스 수동 정리 (2026-09-15, 이어서)

`mnsdb-weekly` OOM 사고(다른 세션, `spark-infra` handoff
`20260915-mnsdb-weekly-oom-oomscoreadjust.md` 참고) 조사 중 근본 원인 중 하나로
지목된 문제를 고쳤다: stdio 트랜스포트로 뜬 mcp_server 프로세스는 클라이언트가
stdin을 깔끔히 닫지 않으면 회수될 신호가 전혀 없어 세션이 끝나도 계속 살아남고,
Claude Code/Hermes 세션이 쌓일수록 중복 프로세스가 누적돼 결국 호스트 OOM으로
이어졌다.

- `cbe8fd1`/`c6e1d75`/`1acd845` — `IdleTimeoutWatchdog` 추가: MCP 도구 호출이
  `idle_timeout_seconds`(로컬 kure 스택은 600초) 동안 없으면 프로세스가 스스로
  종료한다. 클라이언트는 다음 도구 호출 시 재기동하는 게 전제. Opus 재검토에서
  두 가지 실제 버그(느린 호출 도중 강제종료, `add_memory` 백그라운드 큐 워커가
  활동으로 안 잡히던 것)를 잡아 `track_activity()` 컨텍스트매니저로 수정.
- **이 수정은 새로 뜨는 프로세스에만 적용된다** — 수정 반영 이전에 이미 떠 있던
  구버전 프로세스는 새 워치독 코드를 갖고 있지 않아 그대로 살아남는다. 실측:
  같은 날 19:5X경 진단 시점 기준 구버전 프로세스 **14개**가 여전히 떠 있었고
  (가장 오래된 건 08:42부터, 약 11시간), 전부 살아있는 부모(다른 Claude Code
  세션 9개 + Hermes 데몬 워치독 3개)를 갖고 있어 "고아 아님"으로 확인됐다 —
  즉 자동으로는 절대 안 죽고 각 부모 세션이 끝나야만 사라지는 상태였다.
- **JL 승인 하에 14개 전부 수동 종료** (`SIGTERM` 먼저 시도 → 대부분 무반응이라
  `SIGKILL`로 마무리). 부모가 살아있는 다른 세션들의 graphiti 도구 호출은
  일시적으로 끊기지만, 설계상 클라이언트가 다음 호출 때 새 코드로 재기동한다.
  종료 전/후 `free -h`: 사용 118Gi→111Gi, 가용 3.0Gi→10Gi로 회복 확인.
- **남은 일**: 이 수동 정리는 1회성이다. 재기동되는 새 프로세스는 idle-timeout이
  적용되므로 이론상 재누적되지 않아야 하는데, 실제로 600초 이상 idle 상태에서도
  스스로 안 죽는 사례가 있는지는 아직 관찰되지 않았다 — 다음에 프로세스 수가
  다시 두 자릿수로 쌓이면 idle-timeout 자체가 안 먹히는 것으로 보고 재조사할 것.

## 되돌리는 법

문제 생기면: `claude mcp remove graphiti-legal`, `systemctl --user disable --now kure-embed.service`,
`docker rm -f graphiti-falkordb` (볼륨은 `docker volume rm graphiti_falkordb_data`로 별도 삭제해야
지워짐 — 컨테이너만 지우면 데이터 남음).
