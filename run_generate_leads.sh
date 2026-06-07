#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
LEAD_TOTAL="${LEAD_TOTAL:-5}"
INSTITUTION_TYPE="${INSTITUTION_TYPE:-both}"
INPUT_CSV="${INPUT_CSV:-input/leads.csv}"
LEAD_BATCH_SIZE="${LEAD_BATCH_SIZE:-50}"
LEAD_MAX_ATTEMPTS="${LEAD_MAX_ATTEMPTS:-8}"

echo "Preparing folders..."
mkdir -p input logs

if [ ! -f ".env" ]; then
  echo "Warning: .env file not found. Create one from .env.example for LLM-backed lead generation."
fi

echo "Generating leads..."
echo "  source=llm"
echo "  total=${LEAD_TOTAL}"
echo "  institution_type=${INSTITUTION_TYPE}"
echo "  output=${INPUT_CSV}"

"${PYTHON_BIN}" generate_leads.py \
  --total "${LEAD_TOTAL}" \
  --institution-type "${INSTITUTION_TYPE}" \
  --batch-size "${LEAD_BATCH_SIZE}" \
  --max-attempts "${LEAD_MAX_ATTEMPTS}" \
  --output "${INPUT_CSV}"

echo "Done."

# Reference commands:
# ./run_generate_leads.sh
# LEAD_TOTAL=1000 ./run_generate_leads.sh
# LEAD_TOTAL=1000 INSTITUTION_TYPE=community_bank ./run_generate_leads.sh
# LEAD_TOTAL=1000 INSTITUTION_TYPE=credit_union ./run_generate_leads.sh
# INPUT_CSV=input/custom_leads.csv LEAD_TOTAL=100 ./run_generate_leads.sh
