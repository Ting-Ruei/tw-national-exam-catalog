#!/bin/sh
# Build the macOS Vision OCR probe. Apple's Vision framework is part of the OS: nothing is
# downloaded, no model folder is needed, and no network is touched.
set -e
here=$(cd "$(dirname "$0")" && pwd)
out="${1:-$here/bin/macOS_vision_ocr}"
mkdir -p "$(dirname "$out")"
swiftc -O "$here/macos_vision_ocr.swift" -o "$out"
echo "built $out"
