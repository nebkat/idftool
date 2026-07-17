#!/usr/bin/env bash
#
# Build the idftool test app twice (v1.0.0 / v2.0.0) and assemble the fixtures the device tests
# need, into test/fixtures/<chip>/. Requires a working ESP-IDF environment (run `. export.sh`
# first) and esptool on PATH.
#
# Usage:  ./build_fixtures.sh [chip]      (default: esp32s3)
#
set -euo pipefail

CHIP="${1:-esp32s3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../fixtures/$CHIP"
mkdir -p "$OUT"

if ! command -v idf.py >/dev/null 2>&1; then
    echo "idf.py not found — source your ESP-IDF export.sh first." >&2
    exit 1
fi

build() {  # $1 = version string
    export IDFTOOL_TEST_VER="$1"
    rm -rf "$HERE/build" "$HERE/sdkconfig"
    idf.py -C "$HERE" set-target "$CHIP" >/dev/null
    idf.py -C "$HERE" build
}

echo ">> Building app v2.0.0"
build 2.0.0
cp "$HERE/build/idftool_test.bin" "$OUT/app-v2.bin"

# Build v1 last so the bootloader / partition table / merged flash image come from this config.
echo ">> Building app v1.0.0"
build 1.0.0
cp "$HERE/build/idftool_test.bin"                    "$OUT/app-v1.bin"
cp "$HERE/build/bootloader/bootloader.bin"           "$OUT/bootloader.bin"
cp "$HERE/build/partition_table/partition-table.bin" "$OUT/partition-table.bin"
cp "$HERE/partitions.csv"                            "$OUT/partitions.csv"
cp "$HERE/nvs.csv"                                   "$OUT/nvs.csv"

# A flash image (bootloader + partition table + factory app) so the tests can provision a known,
# valid state with `idftool write-image` regardless of what was on the device before. No
# --fill-flash-size: the image ends after the last segment (~350 KB, gaps padded 0xFF) rather than
# padding to the full flash size, so it stays small enough to commit.
echo ">> Merging flash image"
( cd "$HERE/build" && esptool --chip "$CHIP" merge-bin \
    -o "$OUT/flash-image.bin" "@flash_args" )

echo ">> Fixtures written to $OUT"
ls -l "$OUT"
