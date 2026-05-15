#!/usr/bin/env bash
set -e

echo "Starting AI lead research pipeline..."

if [ ! -f ".env" ]; then
  echo "Warning: .env file not found. Create one from .env.example if Gemini is needed."
fi

if [ ! -d "input" ]; then
  echo "Creating input directory..."
  mkdir -p input
fi

if [ ! -d "output" ]; then
  echo "Creating output directory..."
  mkdir -p output
fi

if [ ! -d "logs" ]; then
  echo "Creating logs directory..."
  mkdir -p logs
fi

python3 main.py
