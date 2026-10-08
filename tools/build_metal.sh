#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/build/native"
xcrun clang++ -O3 -ffp-contract=off -std=c++17 -fobjc-arc -dynamiclib \
  -framework Foundation -framework Metal \
  "$ROOT/core/native/metal_remap.mm" -o "$ROOT/build/native/metal_remap.dylib"
