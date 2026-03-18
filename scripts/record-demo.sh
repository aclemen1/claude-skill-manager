#!/usr/bin/env bash
# Record an automated demo of csm tui in a sandboxed environment.
#
# Usage: ./scripts/record-demo.sh
# Output: docs/demo.cast  (asciinema recording)
#         docs/demo.gif   (animated GIF)

set -euo pipefail
cd "$(dirname "$0")/.."

CAST_FILE="docs/demo.cast"
GIF_FILE="docs/demo.gif"

echo "Recording demo (130x42)..."
echo "The TUI will launch and be automated — just watch."
echo ""
echo "Press Enter to start..."
read -r

asciinema rec \
    --window-size 130x42 \
    --overwrite \
    --command "uv run python scripts/demo-driver.py" \
    "$CAST_FILE"

echo ""
echo "Recording saved to $CAST_FILE"

if command -v agg &>/dev/null; then
    echo "Converting to GIF ..."
    agg --speed 1.2 --theme monokai --font-size 14 "$CAST_FILE" "$GIF_FILE"
    echo "GIF saved to $GIF_FILE ($(du -h "$GIF_FILE" | cut -f1))"
else
    echo "Install agg to convert to GIF: brew install agg"
fi
