#!/usr/bin/env bash
# FalkorDB를 127.0.0.1에만 바인딩해서 기동한다.
# REDIS_ARGS를 반드시 포함해야 인증(requirepass)과 AOF 영속성이 유지된다 —
# FALKORDB_PASSWORD 환경변수 단독으로는 이미지가 인증을 걸지 않는다(2026-09-15 실측).
set -euo pipefail
IMAGE="falkordb/falkordb@sha256:0d793d4b249a9cf0837faa9f30fea1b86fb50086fc8aa21e9447078a07f995bc"
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
