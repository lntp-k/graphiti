#!/usr/bin/env bash
# Graphiti MCP 런처 — ~/.hermes/config.yaml에서 vLLM api_key, ~/.hermes/.env에서 FALKORDB_PASSWORD 주입 후 stdio로 기동.
# 키를 config yaml/레포에 평문 커밋하지 않기 위해 실행 시점에 env로만 주입한다.
set -euo pipefail
export FALKORDB_PASSWORD="$(grep -m1 '^FALKORDB_PASSWORD=' "$HOME/.hermes/.env" | cut -d= -f2-)"
export VLLM_API_KEY="$(grep -A3 -m1 '^  qwen38-27b-nvfp4-local:' "$HOME/.hermes/config.yaml" | grep -m1 'api_key:' | awk '{print $2}')"
[ -n "$FALKORDB_PASSWORD" ] || { echo "FALKORDB_PASSWORD 추출 실패" >&2; exit 1; }
[ -n "$VLLM_API_KEY" ] || { echo "VLLM_API_KEY 추출 실패" >&2; exit 1; }
cd "$(dirname "$0")"
exec uv run python main.py --config config/config-local-kure.yaml --transport "${MCP_TRANSPORT:-stdio}"
