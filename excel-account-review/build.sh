#!/usr/bin/env bash
# Ghép app HTML một-file từ các phần trong src/ + thư viện ExcelJS.
# Cần: npm (để lấy exceljs) — chạy: bash build.sh
set -euo pipefail
cd "$(dirname "$0")"

EXCELJS_VER="4.4.0"
OUT="excel-account-review.html"
TMP="$(mktemp -d)"

# Lấy ExcelJS bản browser UMD (đã minify) qua npm
( cd "$TMP" && npm pack "exceljs@${EXCELJS_VER}" >/dev/null && tar xzf "exceljs-${EXCELJS_VER}.tgz" )
EXCELJS_JS="$TMP/package/dist/exceljs.min.js"

{
  cat src/part1.html
  printf '\n<script>\n'; cat "$EXCELJS_JS"
  printf '\n</script>\n<script>\n'; cat src/review-core.js
  printf '\n</script>\n<script>\n'; cat src/app.js
  printf '\n</script>\n</body>\n</html>\n'
} > "$OUT"

rm -rf "$TMP"
echo "Built $OUT ($(du -h "$OUT" | cut -f1))"
