#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
INPUT_CSV="${INPUT_CSV:-input/leads.csv}"

echo "Preparing folders..."
mkdir -p input output logs

if [ ! -f ".env" ]; then
  echo "Warning: .env file not found. Create one from .env.example for LLM-backed enrichment."
fi

echo "Running enrichment pipeline..."
echo "  input=${INPUT_CSV}"
INPUT_CSV="${INPUT_CSV}" "${PYTHON_BIN}" main.py

echo "Done."

# Reference commands:
# ./run_pipeline.sh
# INPUT_CSV=input/leads.csv ./run_pipeline.sh
# INPUT_CSV=input/custom_leads.csv ./run_pipeline.sh
