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
- ~~**의뢰인 실데이터 투입 전 접근 통제** 미검토~~ → **2026-09-15 완료.** 계획:
  `docs/superpowers/plans/2026-09-15-access-control-and-temporal-validation.md`,
  ADR: `decisions/0002-falkordb-network-exposure-and-group-id-isolation.md`.
  재구성 전 실측: `docker port`가 6379/3001을 `0.0.0.0`에 바인딩, `ufw` inactive,
  `graphiti-mcp`(포트 8000)도 인증 없이 `0.0.0.0` 노출 — 재발견된 위험은 ADR 0002 참고.
  재구성 후 실측(2026-09-15):
  - `docker port graphiti-falkordb` → `127.0.0.1:6379`만(0.0.0.0/[::] 없음). 3001 매핑 자체 제거.
  - `ss -tlnp` → `127.0.0.1:6379` LISTEN 한 줄만.
  - `bash -c 'cat < /dev/null > /dev/tcp/192.168.219.100/6379'` → `Connection refused`.
  - `bash -c 'cat < /dev/null > /dev/tcp/100.69.187.66/6379'`(tailnet) → `Connection refused`.
  - 인증: 무인증 `ping` → `NOAUTH Authentication required.`, `REDISCLI_AUTH` 사용 시 → `PONG`.
  - AOF: `CONFIG GET appendonly` → `yes`.
  - 데이터 보존: 재생성 전후 `DBSIZE` 유지 확인(재생성 후 `2`), 재생성 전 `BGSAVE` +
    볼륨 tar 백업(`~/coding/graphiti-src/backups/falkordb-backup-20260915.tgz`)도 별도로 확보.
  - MCP 재연결: `get_status()` → `{"status":"ok", ...connected to falkordb database"}`.
  남은 위험(ADR 0002의 "미해결"): 같은 bridge 네트워크의 다른 컨테이너(mnsdb 등)는 컨테이너 IP로
  여전히 접속 가능, `tailscale serve`가 이 포트에 추가되면 재노출됨, 비밀번호 회전 미실시,
  호스트 전체 포트 점검은 범위 밖 — 이 항목들은 "완료"가 아니라 "인지하고 넘어감"임을 명시.
  group_id 분리(사건별 강제)는 `config-local-kure.yaml`의 `group_id: "_unassigned"` 센티널 +
  `graphiti_mcp_server.py`의 5줄 경고 가드로 처리(코드 실측 확인, ADR 0002 참고) — 이건 접근
  통제가 아니라 "실수로 섞임" 방지 장치임을 구분해서 이해할 것.
- 예전에 있었던 `mcp_server/src/graphiti_mcp_server.py`에 대한 로컬 패치는 **내용을 알 수 없어
  복구 못 함**. 이번 재클론에서는 업스트림 자체에 reranker 폴백 로직이 이미 들어있어서
  당장은 패치 없이도 동작 확인됨(ADR 0001 참고). 추후 유사 에러 나오면 그때 다시 패치할 것.

## 되돌리는 법

문제 생기면: `claude mcp remove graphiti-legal`, `systemctl --user disable --now kure-embed.service`,
`docker rm -f graphiti-falkordb` (볼륨은 `docker volume rm graphiti_falkordb_data`로 별도 삭제해야
지워짐 — 컨테이너만 지우면 데이터 남음).
