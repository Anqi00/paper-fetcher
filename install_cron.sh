#!/bin/bash
# Install a daily cron job to run the paper fetcher at 8:00 AM.
# Semantic Scholar is skipped by default to avoid rate-limit issues.
# To enable S2 (recommended: get a free API key first), remove --no-s2 below.
# Get your free S2 API key at: https://semanticscholar.org/product/api

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi
CRON_CMD="0 8 * * * cd \"$SCRIPT_DIR\" && $PYTHON fetcher.py --no-s2 >> \"$SCRIPT_DIR/output/cron.log\" 2>&1"

if crontab -l 2>/dev/null | grep -qF "fetcher.py"; then
    echo "Cron job already installed:"
    crontab -l | grep "fetcher.py"
    echo ""
    read -rp "Replace it? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
    crontab -l 2>/dev/null | grep -vF "fetcher.py" | crontab -
fi

(crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
echo "Cron installed: every day at 08:00"
echo "  Log: $SCRIPT_DIR/output/cron.log"
echo ""
echo "To also enable Semantic Scholar (CVPR/ICRA/etc. venue-filtered papers):"
echo "  1. Get a free API key at semanticscholar.org/product/api"
echo "  2. Set S2_API_KEY in config.py"
echo "  3. Remove '--no-s2' from the cron line above"
