#!/usr/bin/env bash
set -euo pipefail
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install
# Run using rooted config defaults
python scrape.py
