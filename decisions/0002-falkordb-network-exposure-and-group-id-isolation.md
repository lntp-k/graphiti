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
