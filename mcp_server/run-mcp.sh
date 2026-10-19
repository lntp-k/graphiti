#!/usr/bin/env bash
# Graphiti MCP 런처 — ~/.config/qwen38/api-key에서 vLLM api_key, ~/.config/graphiti/falkordb.env에서 FALKORDB_PASSWORD 주입 후 stdio로 기동.
# 키를 config yaml/레포에 평문 커밋하지 않기 위해 실행 시점에 env로만 주입한다.
set -euo pipefail
export FALKORDB_PASSWORD="$(grep -m1 '^FALKORDB_PASSWORD=' "$HOME/.config/graphiti/falkordb.env" | cut -d= -f2-)"
export VLLM_API_KEY="$(tr -d '\r\n' < "$HOME/.config/qwen38/api-key")"
[ -n "$FALKORDB_PASSWORD" ] || { echo "FALKORDB_PASSWORD 추출 실패" >&2; exit 1; }
[ -n "$VLLM_API_KEY" ] || { echo "VLLM_API_KEY 추출 실패" >&2; exit 1; }
cd "$(dirname "$0")"
exec uv run python main.py --config config/config-local-kure.yaml --transport "${MCP_TRANSPORT:-stdio}"
