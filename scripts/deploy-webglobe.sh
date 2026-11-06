#!/usr/bin/env bash
# Uploads FINILA to Webglobe hosting over FTPS.
#
#   MODE=check   only connects and lists the web root (changes nothing)
#   MODE=deploy  backs up, archives the old WordPress site to /archiv/ (first
#                run only), then uploads FINILA next to it
#
# Needs FTP_SERVER, FTP_USERNAME, FTP_PASSWORD (only the password is a secret); optional FTP_DIR (default /),
# FTP_INSECURE_TLS=1 when the host's certificate does not match FTP_SERVER,
# OLD_SITE_URL (default https://www.finila.com/).
# The only existing file that changes is .htaccess (replaced by deploy/htaccess);
# its WordPress original is kept on the server as .htaccess-wordpress-zaloha and in
# $WORK/htaccess.original (the repo is public, so wp-config.php and other site
# files are never copied into the workflow's artifacts).
# Nothing on the server is deleted. WordPress files stay but no longer run: old
# addresses are served from the static copy in archiv/ under their original URLs.
set -euo pipefail

for v in FTP_SERVER FTP_USERNAME FTP_PASSWORD; do
  [ -n "${!v:-}" ] || { echo "::error title=Missing setting::$v is not set"; exit 1; }
done
MODE="${MODE:-check}"
FTP_DIR="${FTP_DIR:-/}"
OLD_SITE_URL="${OLD_SITE_URL:-https://www.finila.com/}"
WORK="${WORK:-$PWD/_deploy}"
SRC="${SRC:-$PWD}"
rm -rf "$WORK" && mkdir -p "$WORK"

LFTP_SETTINGS="set net:max-retries 3; set net:timeout 30; set ftp:ssl-allow yes; set ftp:ssl-force ${FTP_FORCE_TLS:-yes}; set ftp:ssl-protect-data yes"
# Webglobe also offers SFTP (FTP_SERVER=sftp://ftp.example.com:222); first contact accepts the host key.
LFTP_SETTINGS="$LFTP_SETTINGS; set sftp:auto-confirm yes"
if [ "${FTP_INSECURE_TLS:-0}" = "1" ]; then LFTP_SETTINGS="$LFTP_SETTINGS; set ssl:verify-certificate no"; fi

# Errors also go out as workflow annotations, which stay readable when the log is not.
ftp() {
  local err="$WORK/lftp.err" rc=0
  lftp -u "$FTP_USERNAME","$FTP_PASSWORD" -e "$LFTP_SETTINGS; $1; bye" "$FTP_SERVER" 2>"$err" || rc=$?
  cat "$err" >&2
  if [ "$rc" != 0 ]; then
    echo "::error title=Server connection failed::$(tail -n 5 "$err" | tr '\n' ' ' | sed "s/$FTP_PASSWORD/***/g")"
  fi
  return "$rc"
}

echo "== Web root ($FTP_DIR) on $FTP_SERVER"
ftp "cd '$FTP_DIR' && cls -1 --sort=name" | tee "$WORK/root-listing.txt"

if ! grep -qxE '(wp-config\.php|index\.php|index\.html)' "$WORK/root-listing.txt"; then
  echo "::error::$FTP_DIR does not look like the web root (no index.php/index.html/wp-config.php). Set FTP_DIR to the folder FileZilla shows the site files in."
  exit 1
fi
[ "$MODE" = "check" ] && { echo "::notice title=Server check OK::$FTP_DIR contains: $(tr '\n' ' ' < "$WORK/root-listing.txt")"; exit 0; }
[ "$MODE" = "deploy" ] || { echo "Unknown MODE=$MODE"; exit 1; }

echo "== Archive of the old site"
if grep -qxE 'archiv/?' "$WORK/root-listing.txt"; then
  echo "archiv/ already exists on the server, keeping it."
  ARCHIVE=0
else
  ARCHIVE=1
  host=$(echo "$OLD_SITE_URL" | sed -E 's#https?://([^/:]+).*#\1#')
  bare=${host#www.}
  # wget exits non-zero on any single 404; the site is checked below instead.
  ( cd "$WORK" && wget --mirror --page-requisites --convert-links --adjust-extension \
      --no-host-directories --domains "$bare,www.$bare" --directory-prefix=archiv \
      --reject-regex '/(wp-admin|wp-json|xmlrpc\.php|wp-login\.php|feed)(/|$)|\?(replytocom|s|p)=' \
      --wait=0.2 --tries=3 --timeout=30 -e robots=off --user-agent="FINILA archive" \
      "$OLD_SITE_URL" ) || true
  [ -f "$WORK/archiv/index.html" ] || { echo "::error::Archive failed, archiv/index.html missing."; exit 1; }
  echo "Archived $(find "$WORK/archiv" -name '*.html' | wc -l) pages, $(du -sh "$WORK/archiv" | cut -f1)."
fi

echo "== Preparing FINILA"
mkdir -p "$WORK/site"
cp "$SRC"/index.html "$SRC"/screener.html "$SRC"/insiders.html "$SRC"/shorts.html "$SRC"/assets.html "$SRC"/rates.html "$SRC"/property.html "$SRC"/map.html "$SRC"/groups.html "$WORK/site/"
cp -r "$SRC/css" "$SRC/js" "$WORK/site/"
cp "$SRC/robots.txt" "$SRC/favicon.ico" "$SRC"/google*.html "$WORK/site/"
# One page per company (/stock/<name>), the A-Z list and sitemap.xml, built from the current data.
python3 "$SRC/scripts/build-stock-pages.py" "$SRC" "$WORK/site"
# Market Radar (/radar and /radar/<indicator>), built from js/radar.json.
python3 "$SRC/scripts/build-radar-pages.py" "$SRC" "$WORK/site"

# .htaccess comes from deploy/htaccess: "/" opens FINILA, old WordPress addresses
# show their /archiv/ copy, WordPress's PHP is closed. The WordPress original is
# saved once on the server as .htaccess-wordpress-zaloha (.ht* files are never served).
ftp "cd '$FTP_DIR' && get -O '$WORK' .htaccess" 2>/dev/null || touch "$WORK/.htaccess"
cp "$WORK/.htaccess" "$WORK/htaccess.original"
cp "$SRC/deploy/htaccess" "$WORK/site/.htaccess"
SAVE_WP_HTACCESS=0
grep -q '^# BEGIN FINILA' "$WORK/htaccess.original" || SAVE_WP_HTACCESS=1

echo "== Uploading"
cmds="cd '$FTP_DIR'"
[ "$SAVE_WP_HTACCESS" = 1 ] && cmds="$cmds && put '$WORK/htaccess.original' -o .htaccess-wordpress-zaloha"
cmds="$cmds && mirror -R --parallel=4 --no-perms --verbose --exclude-glob insiders.json --exclude-glob shorts.json --exclude-glob invest.json --exclude-glob rates.json --exclude-glob companies.json --exclude-glob fundamentals.js --exclude-glob prices.js --exclude-glob company-prices.json --exclude-glob housing.json --exclude-glob housing-levels.json --exclude-glob radar.json '$WORK/site' ."
[ "$ARCHIVE" = 1 ] && cmds="$cmds && mirror -R --parallel=4 --no-perms '$WORK/archiv' archiv"
# Archive pages rebuilt in the FINILA design (scripts/build-archive.py) replace the
# WordPress HTML; the images under archiv/wp-content stay as they are.
[ -d "$SRC/archive-site" ] && cmds="$cmds && mirror -R --parallel=4 --no-perms '$SRC/archive-site' archiv"
ftp "$cmds"
echo "Done. FINILA is at the site root, the old site at /archiv/."
