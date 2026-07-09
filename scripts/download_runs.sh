#!/usr/bin/env bash
# Download the released run exports (47 runs: 39 release-corpus + 3 auxiliary
# controls + 5 Opus 4.7/4.8 probes on the corrected sandbox image, ~43 MB
# zipped / ~400 MB unpacked) into .goa_data/runs/, where
# scripts/make_figures.py and the analysis CLI expect them.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSET_URL="https://github.com/Andromede-AI/game-of-agents/releases/download/v1.0-icml2026-aiwild/goa_runs_v1.zip"
TMP_ZIP="$(mktemp -t goa_runs).zip"

echo "Downloading run exports…"
curl -L --fail -o "$TMP_ZIP" "$ASSET_URL"
unzip -o -q "$TMP_ZIP" -d "$REPO_ROOT"
rm -f "$TMP_ZIP"
echo "Done: $(ls "$REPO_ROOT/.goa_data/runs" | wc -l | tr -d ' ') run exports in .goa_data/runs/"
