# FalkorDB 접근 통제 + 시간성 검증 재현 Implementation Plan (rev.2 — Opus/Fable 검토 반영)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** graphiti-legal(FalkorDB 기반) 그래프에 실제 의뢰인 사건 데이터를 넣기 전에, (1) 컨테이너 네트워크 노출을 없애면서 재생성 과정에서 인증/영속성을 잃지 않도록 하고, 사건별 group_id 분리를 사람 규칙+최소한의 코드 가드로 강제하며, (2) bi-temporal 무효화(invalid_at) 동작을 대기·판정 기준이 명확한 방식으로 실측 재현한다.

**Architecture:** 운영 설정(포트 바인딩, `REDIS_ARGS`) 변경 + 문서(CONTEXT.md/HANDOFF.md/ADR) + `graphiti_mcp_server.py`에 대한 **5줄 이내의 로컬 패치 1건**(group_id 누락 시 경고 로그 남김 — 이 저장소엔 이미 reranker 패치 전례가 있어 원칙에 어긋나지 않는다). 시간성 검증은 별도 테스트 group_id로 폴링 기반 대기를 넣어 graphiti-legal MCP 도구를 실제 호출해 결과를 기록한다.

**Tech Stack:** Docker/FalkorDB, redis-cli(컨테이너 내부), graphiti-legal MCP tools(add_memory / search_memory_facts / search_nodes / get_episodes / get_episode_entities / clear_graph / get_status), bash, git.

**Spec:** 이 문서 자체가 spec. 아래 "배경 조사(실측)"는 이번 rev.2 작성 시점에 직접 확인한 사실이며, 괄호로 확인 방법을 남긴다. 확인하지 못한 항목은 `[추정]`으로 표시한다.

## Global Constraints

- `FALKORDB_PASSWORD`(`~/.hermes/.env`)는 다른 서비스와 공유되는 값이다 — 이 작업에서 값 자체를 바꾸지 않는다. 회전은 별도 작업.
- 레포에 평문 비밀번호를 커밋하지 않는다.
- 이 문서 검토(Opus/Fable)는 CLAUDE.md "Merge Gate" 절차와 별개의 사전 검토다. 실제 커밋을 origin에 push할 때는 그 절차(커밋→`--begin`→검토→`--verdict-received`→push)를 그대로 따른다.
- **사건 실데이터는 Task 1·Task 2가 모두 끝나고 Step 검증이 통과하기 전까지 넣지 않는다.**
- group_id 기본값을 빈 문자열(`""`)로 두지 않는다 — `add_memory`는 빈 문자열이 그대로 큐에 들어가고, `search_nodes`/`search_memory_facts`는 반대로 그룹 필터가 없는 **전역 검색**으로 뒤집힌다(코드 실측, 아래 참조). 두 경로가 빈 값에 대해 서로 다르게, 그리고 둘 다 위험하게 반응한다.
- 커밋 메시지에는 이 세션의 attribution 규칙(`Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`)을 넣는다.

## 배경 조사 (실측)

**네트워크 노출**
- `docker port graphiti-falkordb` → `6379`, `3000/tcp -> 0.0.0.0:3001`가 `0.0.0.0`/`[::]`에 바인딩. LAN(`192.168.219.0/24`)·Tailscale tailnet(`100.69.187.66`) 양쪽에서 도달 가능했다.
- `sudo ufw status` → `inactive`.
- `tailscale serve status` 실행 결과, 이미 `https://spark1.tail8ff83a.ts.net`이 `http://127.0.0.1:8013`을 tailnet에 프록시하는 중 — **루프백 바인딩을 tailnet에 여는 이 기능이 실제로 이 호스트에서 쓰이고 있다.** 6379/3001에는 아직 안 걸려 있지만, 나중에 누가 걸면 "127.0.0.1 전용" 조치가 무효화된다.
- `docker network inspect bridge`로 확인: `graphiti-falkordb`는 기본 `bridge` 네트워크에서 `172.17.0.3`이고, 같은 네트워크에 `mnsdb`(`172.17.0.2`)와 `captcha_cnn_service`(`172.17.0.4`)가 함께 있다. **포트를 127.0.0.1로 좁혀도 이 두 컨테이너는 `172.17.0.3:6379`로 직접 붙을 수 있다** — "호스트 외부 차단"이지 "이 브리지의 다른 컨테이너 차단"은 아니다.
- `docker inspect graphiti-falkordb`의 `Config.Env` 실측: `REDIS_ARGS=--requirepass <64hex> --appendonly yes --appendfsync everysec`. **인증과 AOF 영속성 둘 다 이 `REDIS_ARGS` 하나로 걸려 있다** — `FALKORDB_PASSWORD` 환경변수 자체는 이미지 실행 스크립트가 참조하지 않는다(별도 확인 안 됐으므로 이 문장의 "참조 안 함" 부분은 `[추정]`, 근거는 Fable 검토의 grep 결과). 즉 계획에서 `-e FALKORDB_PASSWORD`만 넘기고 `REDIS_ARGS`를 빠뜨리면 재생성된 컨테이너는 인증도 AOF도 없이 뜰 위험이 있다.
- `mcp_server/docker/docker-compose-falkordb.yml`을 직접 읽음: `graphiti-mcp` 서비스가 `"8000:8000"`으로 **인증 없는 MCP HTTP 엔드포인트**(그래프 전체 조회·쓰기·`clear_graph` 포함)를 `0.0.0.0`에 노출 — falkordb 포트보다 위험도가 높다.

**group_id**
- `graphiti_mcp_server.py`를 직접 읽음(459-460행): `add_memory`는 `effective_group_id = group_id or config.graphiti.group_id` — 둘 다 falsy면 **빈 문자열이 그대로 큐에 들어간다.**
- 같은 파일(528-536행): `search_nodes`/`search_memory_facts`는 `group_ids`가 없을 때 `config.graphiti.group_id`가 falsy면 `effective_group_ids = []` — **그룹 필터 없는 전역 검색**이 된다. 쓰기 경로와 읽기 경로가 빈 기본값에 반대로 반응한다.
- `graphiti_core/helpers.py`의 `validate_group_id`(136행)를 직접 읽음: group_id는 **ASCII 영숫자·`-`·`_`만 허용**. `2026고합123`처럼 한글이 섞인 사건번호는 그대로 못 쓴다.

---

### Task 1: FalkorDB 네트워크 노출 차단 + 사건별 group_id 강제

**Files:**
- Create: `mcp_server/docker/run-falkordb.sh`
- Modify: `mcp_server/docker/docker-compose-falkordb.yml`
- Modify: `mcp_server/src/graphiti_mcp_server.py` (group_id 필수화 가드, `add_memory`에 5줄 이내)
- Modify: `CONTEXT.md`
- Modify: `HANDOFF.md`
- Create: `decisions/0002-falkordb-network-exposure-and-group-id-isolation.md`

**Interfaces:** 없음(운영 설정) + `add_memory` 도구 시그니처는 변경하지 않는다(내부 검증만 추가).

- [ ] **Step 1: 재생성 전 현재 상태 백업·기록**

```bash
docker inspect graphiti-falkordb > /tmp/falkordb-before.json
docker volume inspect graphiti_falkordb_data
docker exec graphiti-falkordb sh -c 'REDISCLI_AUTH="$(grep -m1 "^FALKORDB_PASSWORD=" /proc/1/environ | tr "\0" "\n" | grep FALKORDB_PASSWORD | cut -d= -f2-)" redis-cli CLIENT LIST'
```
`CLIENT LIST` 출력에 `127.0.0.1`/`172.17.0.x` 외의 주소가 있으면 원격 소비자가 있다는 뜻이니 **중단하고 확인**한다. 없으면 계속 진행.

```bash
docker exec graphiti-falkordb sh -c 'REDISCLI_AUTH="$FALKORDB_PASSWORD" redis-cli BGSAVE'
docker run --rm -v graphiti_falkordb_data:/d -v "$PWD":/b alpine tar czf /b/falkordb-backup-$(date +%Y%m%d).tgz -C /d .
```

- [ ] **Step 2: 재현 가능한 기동 스크립트 작성 — REDIS_ARGS 유지, digest 핀**

```bash
docker inspect --format '{{index .RepoDigests 0}}' falkordb/falkordb:latest
```
위 출력(예: `falkordb/falkordb@sha256:...`)을 아래 스크립트의 `IMAGE`에 그대로 넣는다.

Create `mcp_server/docker/run-falkordb.sh`:

```bash
#!/usr/bin/env bash
# FalkorDB를 127.0.0.1에만 바인딩해서 기동한다.
# REDIS_ARGS를 반드시 포함해야 인증(requirepass)과 AOF 영속성이 유지된다 —
# FALKORDB_PASSWORD 환경변수 단독으로는 이미지가 인증을 걸지 않는다(2026-09-15 실측).
set -euo pipefail
IMAGE="falkordb/falkordb@sha256:REPLACE_WITH_DIGEST_FROM_STEP2"
FALKORDB_PASSWORD="$(grep -m1 '^FALKORDB_PASSWORD=' "$HOME/.hermes/.env" | cut -d= -f2- | tr -d '\r"'"'"'')"
[ -n "$FALKORDB_PASSWORD" ] || { echo "FALKORDB_PASSWORD 추출 실패" >&2; exit 1; }
docker rm -f graphiti-falkordb 2>/dev/null || true
docker run -d --name graphiti-falkordb \
  -p 127.0.0.1:6379:6379 \
  -e REDIS_ARGS="--requirepass ${FALKORDB_PASSWORD} --appendonly yes --appendfsync everysec" \
  -e BROWSER=0 \
  -v graphiti_falkordb_data:/var/lib/falkordb/data \
  --restart unless-stopped \
  "$IMAGE"
```

웹 UI(3001)는 굳이 유지할 이유가 없어 이번에 끈다(`BROWSER=0`, 포트 매핑 제거). 필요해지면 나중에 `127.0.0.1:3001:3000`으로 별도 추가.

```bash
chmod +x mcp_server/docker/run-falkordb.sh
./mcp_server/docker/run-falkordb.sh
```

- [ ] **Step 3: 인증·AOF·바인딩을 모두 재확인**

```bash
docker port graphiti-falkordb
# 기대: 6379/tcp -> 127.0.0.1:6379 만. 0.0.0.0/[::] 없어야 함.

docker exec graphiti-falkordb redis-cli ping
# 기대: NOAUTH Authentication required.

docker exec graphiti-falkordb sh -c 'REDISCLI_AUTH="'"$FALKORDB_PASSWORD"'" redis-cli ping'
# 기대: PONG

docker exec graphiti-falkordb sh -c 'REDISCLI_AUTH="'"$FALKORDB_PASSWORD"'" redis-cli CONFIG GET appendonly'
# 기대: appendonly / yes
```
(REDISCLI_AUTH를 쓰는 이유: `-a`로 넘기면 `ps`/셸 히스토리에 비밀번호가 남는다.)

- [ ] **Step 4: MCP 서버 재연결 및 실제 DB 질의로 확인**

`claude mcp list`는 stdio 프로세스 기동만 확인하고 DB 연결은 보증하지 않는다. 이 세션의 MCP 연결도 재기동 전 소켓을 쥐고 있을 수 있으므로, MCP 클라이언트(Claude Code)를 재시작하거나 새 세션에서 아래를 호출한다:

Call `mcp__graphiti-legal__get_status()` — 기대: FalkorDB 연결 정상(실제 카운트 쿼리 결과 포함).

- [ ] **Step 5: 외부에서 더 이상 안 열리는지 확인**

호스트에 `redis-cli` 클라이언트가 없을 수 있으므로 `/dev/tcp`로 확인:
```bash
ss -tlnp 2>/dev/null | rg '6379|3001'
# 기대: 127.0.0.1 로 시작하는 줄만 있어야 함(3001은 매핑 자체를 없앴으니 안 보여야 함).

timeout 3 bash -c 'cat < /dev/null > /dev/tcp/192.168.219.100/6379' && echo "LAN: 열림(문제)" || echo "LAN: 거부/타임아웃(정상)"
timeout 3 bash -c 'cat < /dev/null > /dev/tcp/100.69.187.66/6379' && echo "Tailscale: 열림(문제)" || echo "Tailscale: 거부/타임아웃(정상)"
```
같은 호스트에서 자기 자신의 사설/tailnet IP로 접속을 시도하는 것이라 완벽한 외부 검증은 아니다 — 가능하면 다른 tailnet 노드(Mac 등)에서 같은 명령으로 한 번 더 확인한다.

- [ ] **Step 6: compose 파일도 동일하게 수정 — falkordb + graphiti-mcp(8000) 둘 다**

Modify `mcp_server/docker/docker-compose-falkordb.yml`:
```yaml
  falkordb:
    ...
    ports:
      - "127.0.0.1:6379:6379" # Redis/FalkorDB port — localhost only
    environment:
      - REDIS_ARGS=--requirepass ${FALKORDB_PASSWORD:-} --appendonly yes --appendfsync everysec
      - BROWSER=0
    healthcheck:
      test: ["CMD", "sh", "-c", "REDISCLI_AUTH=$$FALKORDB_PASSWORD redis-cli -p 6379 ping | grep -q PONG"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
```
(기존 healthcheck는 `-a` 없이 ping만 해서 NOAUTH를 받고도 종료코드 0으로 "성공" 처리됐을 가능성이 있다 — `grep -q PONG`으로 실제 응답 내용을 검사하도록 고친다.)

```yaml
  graphiti-mcp:
    ...
    ports:
      - "127.0.0.1:8000:8000" # HTTP transport — localhost only, 인증 없는 엔드포인트라 외부 노출 금지
```

- [ ] **Step 7: group_id — CONTEXT.md에 규칙 명시 + config 기본값을 안전한 센티널로**

Modify `mcp_server/config/config-local-kure.yaml`:
```yaml
graphiti:
  group_id: "_unassigned"  # 빈 문자열 금지 — add_memory/search 경로가 빈 값에 서로 다르게(둘 다 위험하게) 반응함(실측, ADR 0002)
```
`legal-eval`에서 `_unassigned`로 바꾸는 이유: 사건 group_id를 깜빡하고 호출한 데이터가 기존 eval 데이터와 안 섞이고 한 곳(`_unassigned`)에 모여 사후에 찾아 지우기 쉽게 하기 위함.

Modify `CONTEXT.md`, `## 스택` 절 아래에 추가:
```markdown
## 사건별 데이터 분리 (group_id)

`add_memory`/`search_nodes`/`search_memory_facts` 호출마다 `group_id`(또는 `group_ids`)를
**반드시** 사건 식별자로 명시한다. 생략하면:
- 쓰기(`add_memory`): 기본값 `_unassigned` 그룹으로 조용히 들어간다.
- 읽기(`search_*`): 기본값이 있으면 그 그룹으로, 없으면(빈 문자열) 전역 검색이 된다.
group_id는 **ASCII 영숫자·`-`·`_`만 허용**(한글/공백 불가, `graphiti_core/helpers.py:validate_group_id`
실측 확인). 사건번호는 로마자로 옮겨 쓴다 — 예: `2026고합123` → `2026-gohap-123`.
```

- [ ] **Step 8: group_id 필수화 가드 — `add_memory`에 5줄 패치**

Modify `mcp_server/src/graphiti_mcp_server.py`, `add_memory` 함수 안 그룹 결정 직후(460행 부근)에 추가:

```python
        effective_group_id = group_id or config.graphiti.group_id
        if not effective_group_id or effective_group_id == '_unassigned':
            logger.warning(
                f"add_memory called without explicit group_id (episode '{name}') — "
                f"routed to '_unassigned'. Pass group_id explicitly for real case data."
            )
```

에러로 막지 않고 경고 로그만 남기는 이유: MCP stdio 세션에서 호출자가 로그를 못 볼 수도 있으므로 완전한 강제는 아니지만, 최소한 조용히 사라지지 않고 `_unassigned` 그룹에 모여 사후 감사(`search_nodes(group_ids="_unassigned")`)로 걸러낼 수 있다. 이 저장소는 이미 reranker 관련 로컬 패치 전례(`mcp_server/src/services/factories.py`)가 있어 이 정도 패치는 기존 관행과 일치한다.

- [ ] **Step 9: ADR 작성, HANDOFF 갱신, 커밋**

Create `decisions/0002-falkordb-network-exposure-and-group-id-isolation.md`:
```markdown
# ADR 0002: FalkorDB 네트워크 노출 차단 + group_id 사건 분리

## 배경 (실측, 2026-09-15)
- `docker port`: 6379/3001이 0.0.0.0 바인딩 — LAN·Tailscale tailnet에서 도달 가능했음.
- `ufw`: inactive.
- `tailscale serve`가 이미 다른 포트(8013)에 활성 — 같은 메커니즘이 6379/3001에 걸리면
  이번 조치가 무효화될 수 있음.
- `docker network inspect bridge`: falkordb가 mnsdb, captcha_cnn_service와 같은 기본
  bridge 네트워크에 있어, 포트를 127.0.0.1로 좁혀도 이 두 컨테이너는 컨테이너 IP로
  직접 접속 가능 — "호스트 외부 차단"이지 "브리지 내부 차단"은 아님.
- 인증(`requirepass`)과 AOF 영속성이 `REDIS_ARGS` 환경변수 하나에 걸려 있었음 —
  `FALKORDB_PASSWORD`만 넘기고 재생성하면 이 값이 통째로 사라질 뻔했음.
- `graphiti-mcp` HTTP 서비스(compose, 포트 8000)가 인증 없이 0.0.0.0에 노출 —
  falkordb 포트보다 위험도가 높은 별개 구멍이었음.
- `graphiti.group_id`가 고정값 하나뿐이라 사건 간 데이터가 섞일 수 있었음. 코드 실측
  결과 쓰기(`add_memory`)와 읽기(`search_*`) 경로가 빈 기본값에 서로 다르게 반응함.

## 결정
1. FalkorDB·graphiti-mcp 컨테이너 포트를 `127.0.0.1`에만 바인딩, `REDIS_ARGS`를
   보존해 인증·AOF를 유지, 이미지를 digest로 고정.
2. FalkorDB 웹 UI(3001)는 필요성이 불분명해 이번에 끈다.
3. group_id 기본값을 `_unassigned` 센티널로 바꾸고, `add_memory`에 누락 시 경고
   로그를 남기는 5줄 패치를 추가. 완전한 접근 통제가 아니라 "실수로 섞임" 방지용 —
   같은 MCP 서버를 쓰는 호출자는 여전히 임의의 group_id를 넘길 수 있다(네임스페이스이지
   권한 통제가 아님). 단일 사용자 로컬 환경이라 이 수준으로 충분하다고 판단.

## 남은 위험 / 후속 과제 (미해결로 명시)
- 같은 bridge 네트워크의 다른 컨테이너(mnsdb, captcha_cnn_service)는 여전히
  `172.17.0.3:6379`로 직접 접속 가능 — 필요시 전용 네트워크로 분리.
- `tailscale serve`가 이 포트들에 추가되지 않도록 운영 규칙으로만 막고 있음 —
  기술적 강제 없음.
- `~/.hermes/.env`의 비밀번호가 `docker inspect`에 평문 노출, 다른 서비스와 공유 —
  회전은 별도 작업.
- 이 조치는 FalkorDB/graphiti-mcp만 처리한다. 호스트 전체의 0.0.0.0 published
  port(Postgres 5432, 9100, 9500 등 실측됨)는 이 계획 범위 밖 — 별도 후속 점검 필요.

## 대안 기각
- Tailscale ACL + `ufw enable`: 원격 접근이 애초에 불필요하므로 더 단순한 로컬 바인딩으로 충분.
- 사건마다 별도 MCP 서버 프로세스로 group_id 강제: 사건이 늘 때마다 프로세스를
  늘려야 해서 과함 — 호출 파라미터 + 경고 로그 조합으로 충분.
```

Modify `HANDOFF.md` — "검증 안 됨 / 다음 세션 과제" 절에서 접근 통제 항목을 "완료"로 옮기고, Step 3/5의 실제 출력과 위 "남은 위험" 목록을 근거로 남긴다.

```bash
git add mcp_server/docker/run-falkordb.sh mcp_server/docker/docker-compose-falkordb.yml \
  mcp_server/src/graphiti_mcp_server.py mcp_server/config/config-local-kure.yaml \
  CONTEXT.md HANDOFF.md decisions/0002-falkordb-network-exposure-and-group-id-isolation.md
git commit -m "$(cat <<'EOF'
Bind FalkorDB/graphiti-mcp to localhost only; preserve auth+AOF; add group_id safety net

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: bi-temporal 무효화(invalid_at) 동작 재현 검증

**Files:**
- Modify: `HANDOFF.md`

**Interfaces:**
- Consumes: `mcp__graphiti-legal__add_memory(name, episode_body, group_id, reference_time, source)`, `get_episodes(group_ids)`, `search_memory_facts(query, group_ids, invalid_at_after)`, `get_episode_entities(episode_uuids)`, `clear_graph(group_ids)`, `get_status()`.

**전제:** `add_memory`는 큐에 넣고 즉시 반환하며, MCP 도구로는 큐 처리 완료 여부를 직접 알 수 없다(`get_status`는 DB ping만 함, 실측). 그래서 아래 각 단계는 **폴링**으로 완료를 확인한다.

- [ ] **Step 1: 연결 확인**

Call `get_status()` — 기대: FalkorDB 연결 정상.

- [ ] **Step 2: 최초 사실 주입 — 날짜를 본문에 명시**

Call `add_memory`:
```
name: "temporal-test-1"
episode_body: "2026년 1월 1일 현재, 김철수는 리앤플리그 법무법인 소속이다."
group_id: "temporal-verify-20260915"
source: "text"
reference_time: "2026-01-01T00:00:00+09:00"
```
본문에 날짜를 명시하는 이유: valid_at은 reference_time이 아니라 LLM이 본문에서 추출한 날짜로 결정되므로(코드 근거는 재검토 대상 — 이 부분은 Fable 검토에서 나온 주장이라 `[추정]`), 날짜 없는 문장이면 valid_at이 null로 나와 애초에 무효화 대상이 안 될 수 있다.

- [ ] **Step 3: 폴링으로 처리 완료 대기**

```
반복(최대 12회, 5초 간격, 총 60초 타임아웃):
  get_episodes(group_ids="temporal-verify-20260915")
  → "temporal-test-1" episode가 나타나면 중단
```
60초 안에 안 나타나면 **여기서 멈추고 MCP 서버 stderr 로그를 확인**한다(stdio 모드 로그 출력 위치를 이 스텝 실행 시점에 확인해 기록 — 위치를 몰라서 실패 원인을 못 보는 사고를 막기 위함).

- [ ] **Step 4: 첫 fact 스냅샷 확보 (전이 증거의 "이전" 상태)**

Call `search_memory_facts(query="김철수 소속", group_ids="temporal-verify-20260915")`.
기대: fact 1건, `valid_at` ≈ `2026-01-01`(반환된 타임존 그대로 기록), `invalid_at` = null.
**이 스냅샷을 확보하기 전에는 Step 5로 넘어가지 않는다** — 두 episode를 연달아 넣으면 "이전 상태"를 기록할 기회가 사라진다.

`get_episode_entities(episode_uuids=["<temporal-test-1의 uuid>"])`로 생성된 엔티티/엣지 UUID를 캡처해 둔다(텍스트 검색 매칭이 아니라 UUID로 전/후 대조하기 위함).

- [ ] **Step 5: 모순되는 새 사실 주입**

Call `add_memory`:
```
name: "temporal-test-2"
episode_body: "김철수는 2026년 6월 1일부로 리앤플리그 법무법인을 퇴사했다."
group_id: "temporal-verify-20260915"
reference_time: "2026-06-01T00:00:00+09:00"
```
Step 3과 같은 방식으로 폴링 대기.

- [ ] **Step 6: 판정 — 정확한 기준으로 대조**

Call `search_memory_facts(query="김철수 소속", group_ids="temporal-verify-20260915")`.

**합격 기준(정확히, "≈" 아님):** Step 4에서 캡처한 엣지의 `invalid_at`이 Step 5에서 생성된 새 엣지의 `valid_at`과 **동일한 값**으로 채워져 있어야 한다. 둘 중 하나만 확인되거나 값이 다르면 실패로 기록하고, `get_episode_entities`로 캡처해 둔 UUID를 근거로 "어느 단계에서 끊겼는지"(날짜 추출 실패/노드 매칭 실패/모순 미판정) HANDOFF에 남긴다.

새 fact도 별도 조회로 확인:
```
search_memory_facts(query="김철수 퇴사", group_ids="temporal-verify-20260915")
```
기대: `valid_at` ≈ `2026-06-01`, `invalid_at` = null.

**주의:** graphiti의 기본 검색은 무효화된 fact도 걸러내지 않고 그대로 반환한다(검토에서 지적된 내용, 코드 재확인 전이라 `[추정]`) — "무효화된 옛 fact가 검색에서 안 보여야 한다"는 것은 이 시스템의 설계 속성이 **아니다**. 이 단계의 목적은 "안 보이는지" 확인이 아니라 "`invalid_at` 필드가 정확히 채워지는지" 확인이다.

- [ ] **Step 7: group_id 격리 자체를 음성 대조로 실측**

```
search_memory_facts(query="김철수 소속", group_ids="_unassigned")
```
기대: 결과 0건(다른 그룹으로 안 샜음을 확인). 이 스텝이 Task 1의 group_id 분리 주장을 "실측"으로 뒷받침한다.

- [ ] **Step 8: 신뢰성 확보 — 다른 모순 쌍으로 반복**

동일 절차를 서로 다른 사실 쌍 1~2개 더 반복(예: "회사 소재지 A → B로 이전", "계약 유효 → 해지"). 결과를 "N/N 성공"으로 집계하고, 사용한 LLM(로컬 Qwen, `json_object` 모드)을 명기해 **일반적 신뢰성 주장이 아니라 이번 구성에서의 결과**임을 명확히 한다.

- [ ] **Step 9: 결과를 HANDOFF.md에 기록, 정리, 커밋**

Modify `HANDOFF.md` — 시간성 항목을 "검증됨"으로 옮기고 Step 4/6/7/8의 실제 반환값(uuid, valid_at, invalid_at, N/N)을 원문 그대로 인용해 근거로 남긴다.

정리:
```
clear_graph(group_ids="temporal-verify-20260915")
```
(group_ids를 반드시 명시 — 생략하면 기본 그룹이 지워질 위험이 있다, Global Constraints 참조.)

```bash
git add HANDOFF.md
git commit -m "$(cat <<'EOF'
Reproduce bi-temporal invalidation verification after 2026-09-15 rebuild

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
