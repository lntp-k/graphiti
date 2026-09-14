#!/usr/bin/env bash
# Graphiti MCP 런처 — ~/.hermes/config.yaml에서 vLLM api_key, ~/.hermes/.env에서 FALKORDB_PASSWORD 주입 후 stdio로 기동.
# 키를 config yaml/레포에 평문 커밋하지 않기 위해 실행 시점에 env로만 주입한다.
set -euo pipefail
set -a; source "$HOME/.hermes/.env"; set +a   # FALKORDB_PASSWORD
export VLLM_API_KEY="$(grep -m1 '^  api_key:' "$HOME/.hermes/config.yaml" | head -1 | awk '{print $2}')"
cd "$(dirname "$0")"
exec uv run python main.py --config config/config-local-kure.yaml --transport "${MCP_TRANSPORT:-stdio}"
