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
- ~~**실제 episode 추가 → 시간성 검증**(구 사실 invalid_at 자동 처리)은 이번 세션에서
  재현하지 않았다.~~ → **2026-09-15 재검증 시도, 부분 성공/부분 미확인으로 종료.**
  격리 그룹 `temporal-verify-20260915`(+ 검증용 보조 그룹
  `temporal-verify-20260915-probe`)로 실측, 종료 후 둘 다 `clear_graph`로 정리 완료. **[정정 2026-09-27: 거짓 — `temporal-verify-20260915` 에 3노드가 남아 있었고 2026-09-27 에 GRAPH.DELETE 로 삭제함]**
  절차와 근거는 `.superpowers/sdd/2026-09-15-access-control-and-temporal-validation/task-2-report.md` 참고.

  **확인된 것 (Step 1~4, 7):**
  - `get_status()` → 항상 `{"status":"ok", ...connected to falkordb database"}` — 브리핑대로
    DB ping만 하고 큐 처리 상태는 반영 안 함(실측 재확인).
  - 첫 episode(`temporal-test-1`, "2026년 1월 1일 현재, 김철수는 리앤플리그 법무법인
    소속이다.") 주입 → 약 100초 후 `get_episodes`에 나타남(브리핑의 60초 타임아웃보다
    김, 과거 로그에선 최대 249초까지 걸린 사례 확인 — 60초는 너무 짧은 기본값).
  - `search_memory_facts(query="김철수 소속", ...)` → fact 1건, `valid_at`:
    `"2026-01-01T00:00:00Z"`(본문 명시 날짜에서 정확히 추출됨), `invalid_at`: `null`.
    엣지 uuid `2a859154-57c5-469d-a0d2-0a1d879b117c`.
  - `get_episode_entities`로 노드(김철수, 리앤플리그 법무법인) 및 엣지 uuid 캡처 완료 —
    본문에 날짜를 명시하면 valid_at이 정확히 추출된다는 브리핑의 전제는 **첫 episode에
    한해 확인됨**. 다만 Task 2가 검증하려던 전체 bi-temporal round-trip(모순 주입 시
    기존 fact가 invalid_at으로 무효화되는 것까지)은 아래에 적었듯 확인하지 못했으므로,
    Task 2가 세우려던 목표 중 절반만 실제로 확인된 셈이다.
  - group_id 격리 음성 대조: `search_memory_facts(..., group_ids="_unassigned")` → 0건.
    Task 1의 group_id 분리 주장을 실측으로 뒷받침.

  **끝내 확인 못 한 것 (Step 5/6/8 — invalid_at 자동 무효화 자체):**
  - 모순 fact(`temporal-test-2`, "김철수는 2026년 6월 1일부로... 퇴사했다") 주입 후
    `get_episodes`/`search_memory_facts`를 **6분 이상(360초+)** 폴링했으나 끝내 처리
    완료를 못 봤다 — `invalid_at` == 새 엣지 `valid_at` 판정 자체를 못 함(FAIL/판정불가).
  - 같은 group_id에 뒤이어 넣은 세 번째 episode(`temporal-test-3`, 사무실 소재지 이전)도
    큐가 순차 처리라는 문서대로 뒤에서 막혀 끝내 처리 안 됨.
  - 완전히 새 group_id(`temporal-verify-20260915-probe`)에 넣은 별개 episode도 4분+
    폴링에도 처리 안 됨 — 특정 모순 내용의 문제가 아니라 **이 세션의 파이프라인 자체가
    첫 episode 이후 멈춘 것**으로 보임.
  - 원인 후보를 좁히려 vLLM(`curl .../v1/chat/completions`)을 직접 호출 → 0.78초 응답
    (LLM 자체는 정상), FalkorDB 컨테이너는 재시작 없이 계속 기동 중(`StartedAt` 확인) +
    `PING`→`PONG` 정상 — **인프라(vLLM/FalkorDB)는 정상, `graphiti-legal` 서버 내부
    큐/파이프라인이 두 번째 episode부터 멎은 것으로 보임**(코드 근거 미확인, 이 세션의
    stderr가 파일이 아니라 소켓으로 나가서 스택트레이스를 못 봄 — 아래 항목 참고).
  - **로그 위치 재확인 결과**: 이 세션이 직접 띄운 `graphiti-legal` 프로세스(watchdog 안
    거침)는 stdout/stderr가 파일이 아니라 소켓(`/proc/<pid>/fd/2` → `socket:[...]`)으로
    나간다 — `/home/jl/.hermes/logs/mcp-stderr.log`는 Hermes Agent webui가 watchdog로
    띄운 **별개 인스턴스**들의 로그였다(같은 서버 스크립트, 다른 프로세스). 그 로그에는
    이번 세션의 도구 호출(`CallToolRequest`)이 단 한 줄도 없었다 — 서로 다른 프로세스라
    당연한 결과이지만, "MCP stderr 로그 확인"이 이 세션 자체의 디버깅에는 안 통한다는
    것을 이번에 실측으로 확인함(다음에 같은 문제 디버깅 시 다른 방법 필요).

  **결론: 시간성(invalid_at) 자동 무효화는 이번 세션에서 "검증됨"으로 옮길 수 없다.**
  valid_at 추출과 group_id 격리는 확인됐지만, 모순 감지·invalid_at 채움 자체는 파이프라인이
  멈춰 재현/판정 불가였다. 재검증 시 이 세션 프로세스의 stdout/stderr를 처음부터 파일로
  리다이렉트해 두거나(예: `run-mcp.sh`를 임시로 `2> /tmp/graphiti-debug.log`로 감싸서 실행),
  동시 실행 중인 다른 worktree 세션의 graphiti-legal 프로세스를 먼저 정리한 뒤 단독으로
  재시도할 것.
  계획(`docs/superpowers/plans/2026-09-15-access-control-and-temporal-validation.md`)의
  Global Constraints에 명시된 "실데이터는 Task 1·Task 2가 모두 끝나고 검증이 통과하기
  전까지 넣지 않는다" 제약은 **여전히 유효하다** — Task 1은 통과했지만 Task 2의 핵심
  목표(Step 5/6/8, 실제 invalid_at 무효화)는 통과하지 못했으므로, 위 큐 정체 원인을
  진단하고 무효화가 실제로 검증되기 전까지 의뢰인 실데이터 투입은 계속 막혀 있다.
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

## 2026-09-20 update — session-sync repair, graphiti-core 0.30.2, stale MCP processes

> **Correction 2026-09-26:** the "healthy again" / "drains 25 files per run" statements below are wrong. Every run
> since 2026-06-22 was rejected by the MCP server; see the 2026-09-26 section at the end of this file.

Facts below were measured on 2026-09-20 unless noted.

**State**
- `mcp_server/uv.lock` pins graphiti-core **0.30.2** (commit `57a7680`, 3-line diff: version, sdist, wheel — no other
  package touched). The real venv reports 0.30.2. Verified first in a scratch copy (`uv sync`, import, `main.py --help`),
  then by a live `graphiti-session-sync` run that started a fresh MCP process on the new lock.
- `graphiti-session-sync.timer` (every 5 min) is healthy again since 2026-09-19 14:26 KST. Outage: last success
  2026-09-15 08:34, first failure 08:39, about 1,200 failed runs until the fix. Two stacked causes:
  1. The 2026-09-15 wipe deleted the untracked `run-mcp-local.sh`, but the unit drop-in `10-local-llm.conf` still pointed at
     it (`FileNotFoundError`). The rebuilt `run-mcp.sh` is already the all-local launcher (local vLLM 8012 + KURE 8002 +
     FalkorDB), so the drop-in was obsolete.
  2. After that, `exec uv` inside `run-mcp.sh` failed under systemd (`uv: not found`): the user-unit default PATH has no
     `~/.local/bin`. The launcher worked from Claude Code only because the interactive PATH has it.
- The fix is **host config, not in any repo**: `~/.config/systemd/user/graphiti-session-sync.service.d/` now holds
  `20-path.conf` (new; `Environment=PATH=/home/jl/.local/bin:/usr/local/bin:/usr/bin:/bin`) and
  `10-local-llm.conf.retired-20260919` (renamed, so systemd ignores it; rename back to undo). The active
  `50-dns-wait.conf` (pre-existing, DNS wait before start) is untouched. See ADR 0003.
- Session backlog drains 25 files per run; `once --dry-run` reported `pending=9` at ~14:30 on 2026-09-20.

**Not done / open**
- 8 live Claude Code sessions (parents alive, not orphans) still hold MCP processes started before the 0.30.2 bump
  (Documents 9/16, Documents/89900 9/17, six short-lived `handoff-docs-review` worktree sessions 9/19). They run 0.30.1 from
  memory and pick up 0.30.2 only when the session re-connects (`/mcp`) or restarts. The 0.30.1 -> 0.30.2 gap is a patch
  release and nothing is known to be broken.
- **Idle timeout looks ineffective** (not investigated): `config-local-kure.yaml` sets `idle_timeout_seconds: 600`, yet eight
  pre-bump MCP processes were older than a day (oldest etime 3d23h; the three sampled had only 19–61 CPU-seconds), and at
  least 14 graphiti MCP processes were alive in total. This is
  exactly the trigger the 2026-09-15 note above set ("if the count reaches double digits again, re-investigate"). Whether
  those processes were truly idle or kept alive by their parent sessions was not checked.
- `spark-infra` (`spark-update-manual`) now checks this stack: the MCP graphiti-core pin against PyPI and whether fork
  `main` contains the latest upstream release tag (spark-infra ADR 0056).

**Postmortem (blameless, facts only)**
- *Silent 4-day outage.* The launcher and its config were untracked, so the wipe removed them without trace, and the drop-in
  that referenced them survived. The memory note describing the 08-24 design was never re-verified after the wipe. Whether
  the failure alert fired for this unit was not checked.
- *A fix that only removed the first error.* Retiring the stale drop-in exposed the second failure (`uv: not found`) on the
  very next start. The 08-24 memory note had already recorded this exact PATH trap; the launcher rebuilt on 09-15 lost the
  protection it had accidentally had before (a `PATH` entry in `~/.hermes/.env`). `claude mcp list` showing Connected on
  09-15 proved the launcher works from an interactive shell, not from systemd. Re-run a rebuilt launcher under the
  service's environment before calling it done.
- *An overstated decision request.* The `pins.conf` mismatch was reported as "needs a decision about which side is
  canonical"; a diff of the non-comment rows showed identical pins and only header comments differed. Diff the substance
  before framing a difference as a decision. (Resolved by installing the repo copy on the host.)
- *Search noise.* A case-insensitive search for `ngc` matched `IOSchedulingClass`. Use word boundaries.

**JL 결정 필요**
1. 옛 코드(0.30.1)를 쥔 세션 8개를 언제 재연결할지 — 급하지 않으면 자연 종료를 기다려도 됨.
2. idle timeout 이 안 먹는 것으로 보이는 문제를 재조사할지(위 "Open").
3. `run-mcp.sh` 자체를 PATH 비의존으로 고칠지 — 지금은 소비자 유닛 쪽에서 우회했으므로 다른 유닛이 이 런처를 부르면 같은 실패가 재현된다(ADR 0003).

## 되돌리는 법

문제 생기면: `claude mcp remove graphiti-legal`, `systemctl --user disable --now kure-embed.service`,
`docker rm -f graphiti-falkordb` (볼륨은 `docker volume rm graphiti_falkordb_data`로 별도 삭제해야
지워짐 — 컨테이너만 지우면 데이터 남음).

## 2026-09-26 update — end-to-end review, thinking off, json_schema (ADR 0004)

Facts below were measured on 2026-09-26 unless noted.

**State**
- Temporal invalidation is now verified end to end: a contradicting episode sets `invalid_at` (= new event `valid_at`) and
  `expired_at` on the old edge. Two-episode latency fell from 211 s to about 38 s. Evidence and rejected alternatives: ADR 0004.
- Code: commits `859938a` (`extra_body` in schema/factory/test/config) and `351ee4b` (`json_schema`) on `main`. The running
  `graphiti-mcp.service` was restarted after the second config edit (systemd ActiveEnter 19:39:43 KST), so it runs this content.
- `graphiti-session-sync` (repo `~/coding/graphiti-session-sync`, not modified today) has **never ingested anything**: every row (5,767 when counted, 5,788 on 2026-09-27; the timer keeps adding failures)
  in `~/.local/share/graphiti-session-sync/state.sqlite3` carry `isError` (`add_memory` requires `episode_body`; the sync
  sends `body`), and it does not check `isError`, so it recorded them as ingested. FalkorDB has no `claude-code-sessions` graph.
  Two further reasons a field-name fix alone would still lose data: the sync starts a stdio MCP child and terminates it within
  5 s of `add_memory`, which only queues the episode; and the episode body is metadata only (sha256, sizes, counts).
- FalkorDB graphs: `default_db` and `_unassigned` empty; `temporal-verify-20260915` (3 nodes) and `...-probe` (empty) are old
  test leftovers. The shared MCP had 0 tool calls between 2026-09-24 and today's tests (nobody uses it yet).

**Not done / open**
- `get_episodes` returned 0 while the graph held the episodes (FalkorDB); not investigated. Poll the graph, not this tool.
- The queue drops a failed episode after logging (no retry) although the caller was told "queued".
- Entity types are Person/Organization/Event/Document/Location and no edge types; no legal-domain types yet.
- `graphiti-mcp.service` exits 143 on a normal `systemctl restart`, which triggers `OnFailure=unit-failure-alert@`
  (`SuccessExitStatus=143` missing). Not verified that a mail/reminder was actually produced.
- Old open items from 2026-09-20 (0.30.1 sessions, idle timeout, PATH-independent launcher) are unchanged.
- The untracked `.token-optimizer/` directory in this repo is a plugin artifact and was not committed.

**Postmortem (blameless, facts only)**
- *"Healthy" was read from exit codes.* The 2026-09-19/20 check counted `ingested` log lines and `exit 0`; those lines are printed
  after any reply, including an error reply. A check of the result (rows in the graph) would have caught it.
- *First fix attempt made things worse.* Turning thinking off alone (kept `json_object`) failed 4 of 4 episodes. Changing one
  setting at a time and reading the graph after each change found the cause in one round.
- *Wrong completion signal.* The first test polled `get_episodes`, which stays at 0, and waited 8 minutes for a condition that
  could not trigger. Querying FalkorDB directly showed the state at once.
- *Restart side effects.* A restart of the shared service ended 7 live client sessions and exit code 143 counts as a failure for
  `OnFailure`. Restart it only when the sessions can reconnect.
- *Secrets in the transcript.* A shell variable that held a `docker exec -e REDISCLI_AUTH=<pw>` command line printed the FalkorDB
  password when it failed with "command not found", and reading `~/.hermes/config.yaml` printed the vLLM api_key. Both are
  loopback-only. Use a wrapper script that reads the secret itself and never echoes it.

**JL 결정 필요**
1. `graphiti-session-sync` 처리: A) 타이머 중단(권장 — 페이로드가 메타데이터뿐이라 그래프 가치 없음), B) 오류 검사 + 공유 서버(8765)
   전송만 고쳐 유지, C) 세션 요약을 넣는 새 설계(목적·프라이버시 먼저 결정).
2. 이번 두 커밋을 fork `origin/main` 에 push 할지(머지 검토 후) — 이 문서와 함께 진행됨.
3. `temporal-verify-*` 테스트 그래프 2개 삭제 여부.
4. FalkorDB 비밀번호·vLLM api_key 회전 여부(대화 기록에 노출됨, loopback 전용).
5. `graphiti-mcp.service` 에 `SuccessExitStatus=143` 추가 여부.

## 2026-09-27 handoff — push blocked, session-sync decision (JL)

**Decision (JL, 2026-09-27):** `graphiti-session-sync` becomes **option C — a new pipeline that ingests real summarized
session content — but later**, not now. Options A (stop the timer) and B (repair plumbing only) were not chosen. Nothing was
changed in `graphiti-session-sync`, so its timer (`graphiti-session-sync.timer`, every 5 min) is **still running** and still
records false successes (5,788 rows, all `isError`). Whether to stop it in the meantime was not decided; stopping it loses
nothing (no row ever reached the graph) and is reversible (`systemctl --user disable --now graphiti-session-sync.timer`).

**Prerequisites for C** (decide before writing code; ADR first):
1. Purpose: what should the graph answer that memory files / `list_events` cannot? Without that, extraction has no target.
2. Privacy: session text can carry client data and case identifiers. Decide what may be sent (summary only? anonymised?) and
   the `group_id` scheme; ids allow ASCII alphanumerics, `-`, `_` only. The existing rule "no client real data until access
   control is settled" (ADR 0002) still applies.
3. Transport: use the shared streamable-HTTP server (127.0.0.1:8765), not a stdio child. `add_memory` only queues; a child
   killed 5 s later loses the queue.
4. Result check: treat a reply with `isError`, or a missing episode in the graph, as failure. Never record success from the reply
   alone. Poll FalkorDB, not `get_episodes` (returns 0 there).
5. Throughput: ~13-25 s per episode with thinking off (ADR 0004), one worker per group; size batches accordingly.
6. Failure handling: the queue drops a failed episode after logging (no retry); C needs its own retry/dead-letter list.

**Push status (unresolved):** local `main` = `36a889b` plus the commit that carries this section; `origin/main` = `c9d6cda`.
The local CI gate rejected the push at step `ruff-check` (exit 2): `ruff` cannot create `.ruff_cache` in the read-only checkout
(run `2caffb728acd4ebe8b53fc9d534bbe69`, log `logs/000.log` under `~/.local/state/internal-ci/push-gate-runs/`). Cause is in
`~/coding/internal-ci/profiles/graphiti-lint.json` (no `--no-cache`); the fix needs a new profile digest in
`repository-registry.json`. A request was sent on 2026-09-27 to the internal-ci session (peer `npbhtx27`, session "HANDOFF 문서
검토 및 미해결 항목 정리"); the peer send succeeded, the session-message send was reported undelivered, and there was no reply
yet (that session was idle; idle sessions are not woken by messages). **Next step:** check `git -C ~/coding/internal-ci log`
for a graphiti-lint change; if none, ask JL to nudge that session. When the gate passes: the Opus approval recorded on
2026-09-26 covers `859938a..36a889b` only. This commit changes HANDOFF.md only, but `--begin`/review must be redone for the
new HEAD before `git push origin main`, then compare `git ls-remote origin main` with `git rev-parse HEAD`.

**Other open items (unchanged):** `get_episodes` returns 0 on FalkorDB; queue drops failed episodes; no legal-domain entity or
edge types; `graphiti-mcp.service` exits 143 on a normal restart and fires `OnFailure` (`SuccessExitStatus=143` missing);
`temporal-verify-20260915*` test graphs still present; FalkorDB password and vLLM api_key appeared in a 2026-09-26 transcript
(vLLM listens on 0.0.0.0:8012 behind a Bearer key, so "loopback only" was wrong for it; both keys rotated 2026-09-27, see below).

**JL 결정 필요**
1. 세션 동기화 타이머를 C 착수 전까지 멈춰 둘지(멈춰도 잃는 것 없음, 되돌리기 쉬움).
2. C 의 목적·프라이버시 범위(위 선행조건 1·2) — 설계 착수 시 문과생 브리프로 먼저 정리.
3. 나머지(테스트 그래프 삭제, 키 회전, SuccessExitStatus=143)는 이전 항목 그대로.

## 2026-09-27 execution — remaining decisions carried out (advice: Opus + Sol/Codex, read-only)

Both advisors agreed on every item except the ruff step scope; the measured result (Opus reproduced `format --check` exit 2
in a write-protected clone) was adopted over Sol's "check only".

| Item | Done | Evidence |
|---|---|---|
| Stop `graphiti-session-sync.timer` | `disable --now`; state DB moved to `~/.local/share/graphiti-session-sync/state.sqlite3.poisoned-20260927` (kept as the future backfill list; a live DB would make a revived sync skip every file) | `is-enabled` = disabled, no timer listed |
| Test graphs | `temporal-verify-20260915` and `...-probe` deleted; `default_db` and `_unassigned` kept (configured DB name and default-group sentinel) | `GRAPH.LIST` = 2 graphs, `DBSIZE` 4; pre-delete backup `backups/falkordb-backup-20260927.tgz` (gitignored; note it holds everything except the deleted test graph is not in the 09-15 tarball) |
| FalkorDB password | rotated (alphanumeric, `~/.hermes/.env`, container recreated with same volume via `mcp_server/docker/run-falkordb.sh`), graphiti-mcp restarted | new PONG, old rejected, unauthenticated NOAUTH, graph list intact, `add_memory` smoke wrote 2 entities |
| vLLM api key | rotated in `~/.config/qwen38/api-key`, `~/.hermes/config.yaml` (7 places) and its 2026-09-25 backup; vLLM unit restarted (4 min 18 s), then hermes-gateway, hermes-webui, hermes-standalone-webui, dsh-web, graphiti-mcp | `/v1/models` 200 with new key, 401 without; graphiti `add_memory` smoke through the LLM succeeded; no 401 in consumer logs. Mac: no reference to Spark 8012. Hostinger: no `~/.hermes` for the ssh user and no reference found; not exhaustively verified |
| `graphiti-mcp.service` exit 143 | `SuccessExitStatus=143` and `Restart=always` in `spark-infra/systemd/graphiti-mcp.service` (local commit `363d416`, not pushed: spark-infra `main` is 10 commits ahead of origin with an unrelated foreign-commit block) and installed | `systemctl --user show` reports both; `Restart=always` because 143 as "success" would otherwise leave the service down after a stray SIGTERM to the child |
| CI gate | `internal-ci` change to `profiles/graphiti-lint.json` (`--no-cache` on both ruff steps) + registry digest, run/gate/merge from the trusted checkout, Opus review, then push and trusted rebaseline (status in the final report below) | profile run on graphiti-src `d3cfec4` inside the bwrap sandbox: ruff-check and ruff-format passed |

Still open: option C (content-summarising session sync) is deferred by JL; `spark-infra` inventory row for the sync is
being corrected by that repo's session (peer request sent); `kure-embed.service` has `OnFailure=` in `[Service]` instead
of `[Unit]` (systemd ignores it, so its failure alert never fires) — unfixed, other repo.
