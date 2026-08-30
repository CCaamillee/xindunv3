#!/usr/bin/env bash
set -euo pipefail

MODEL_PATH="${CARDIAC_RISK_MODEL_PATH:?Set CARDIAC_RISK_MODEL_PATH to the merged xinzangpolie/model directory}"
MODEL_NAME="${CARDIAC_RISK_MODEL:-cardiac-rupture-qwen38}"
MODEL_PORT="${CARDIAC_RISK_PORT:-8000}"
TENSOR_PARALLEL_SIZE="${CARDIAC_RISK_TP:-2}"

python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL_PATH" \
  --served-model-name "$MODEL_NAME" \
  --tensor-parallel-size "$TENSOR_PARALLEL_SIZE" \
  --dtype bfloat16 \
  --max-model-len 8192 \
  --port "$MODEL_PORT"
