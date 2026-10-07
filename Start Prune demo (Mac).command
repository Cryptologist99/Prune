#!/bin/bash
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then python3 prune.py --demo; else echo "Prune needs Python 3. Install it from https://www.python.org/downloads/ and try again."; read -p "Press Enter to close."; fi
