#!/usr/bin/env bash
# Uploads only FINILA's data files (insiders, shorts, invest, rates, companies, fundamentals, prices, housing, radar) to the live site's js/ folder.
# Pages, styles and scripts are published only by deploy-finila.yml. Never deletes anything.
# Needs FTP_SERVER, FTP_USERNAME, FTP_PASSWORD; optional FTP_DIR (default /).
set -euo pipefail
for v in FTP_SERVER FTP_USERNAME FTP_PASSWORD; do
  [ -n "${!v:-}" ] || { echo "::error title=Missing setting::$v is not set"; exit 1; }
done
FTP_DIR="${FTP_DIR:-/}"
files=()
for f in js/insiders.json js/insiders-home.json js/shorts.json js/invest.json js/rates.json js/companies.json js/fundamentals.js js/prices.js js/company-prices.json js/housing.json js/housing-levels.json js/radar.json; do [ -f "$f" ] && files+=("$f"); done
[ ${#files[@]} -gt 0 ] || { echo "No data files to upload."; exit 0; }
cmds="cd '$FTP_DIR' && cd js"
# SFTP cannot rename onto an existing file: upload to .tmp, then swap it in (the old file is gone only for a moment).
for f in "${files[@]}"; do b=$(basename "$f"); cmds="$cmds && put '$f' -o '$b.tmp' && rm -f '$b' && mv '$b.tmp' '$b'"; done
err=$(mktemp)
lftp -u "$FTP_USERNAME","$FTP_PASSWORD" \
  -e "set net:max-retries 3; set net:timeout 30; set sftp:auto-confirm yes; $cmds; bye" "$FTP_SERVER" 2>"$err" || {
  echo "::error title=Data upload failed::$(tail -n 5 "$err" | tr '\n' ' ' | sed "s/$FTP_PASSWORD/***/g")"; exit 1; }
echo "Uploaded: ${files[*]}"
