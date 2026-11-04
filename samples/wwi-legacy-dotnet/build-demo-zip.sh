#!/usr/bin/env bash
# Assemble WideWorldImporters SSDT (downloaded) + the WWI legacy .NET fixture into one ZIP for the
# Database Discovery upload flow. WideWorldImporters is MIT-licensed and is never committed here.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$PWD/wwi-demo.zip}"
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Downloading WideWorldImporters SSDT (sparse clone)..."
git clone --quiet --depth 1 --filter=blob:none --sparse \
  https://github.com/microsoft/sql-server-samples.git "$WORK/sql-server-samples"
git -C "$WORK/sql-server-samples" sparse-checkout set \
  samples/databases/wide-world-importers/wwi-ssdt

mkdir -p "$WORK/wwi-demo"
cp -R "$WORK/sql-server-samples/samples/databases/wide-world-importers/wwi-ssdt" "$WORK/wwi-demo/database-wwi-ssdt"
cp -R "$HERE/src" "$WORK/wwi-demo/src"
cp -R "$HERE/database" "$WORK/wwi-demo/database"
cp "$HERE/README.md" "$WORK/wwi-demo/README.md"

rm -f "$OUT"
(cd "$WORK" && zip -qr "$OUT" wwi-demo)
echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"
