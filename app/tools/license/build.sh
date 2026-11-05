#!/bin/sh
# Builds the orbit-license command and "Orbit License Maker.app" (dialogs around it) into build/.
# The app is for the Orbit vendor's own Mac only: it needs the signing key in
# ~/Library/Application Support/Orbit License Maker/private.key (create it once: build/orbit-license keygen).
set -e
cd "$(dirname "$0")/../.."
mkdir -p build
for a in arm64 x86_64; do swiftc -O -target $a-apple-macos11.0 tools/license/orbit_license.swift -o build/orbit-license-$a; done
lipo -create build/orbit-license-arm64 build/orbit-license-x86_64 -output build/orbit-license
rm build/orbit-license-arm64 build/orbit-license-x86_64
rm -rf "build/Orbit License Maker.app"
osacompile -o "build/Orbit License Maker.app" "tools/license/Orbit License Maker.applescript"
cp build/orbit-license "build/Orbit License Maker.app/Contents/Resources/"
codesign --force --deep -s - "build/Orbit License Maker.app"
echo "built build/Orbit License Maker.app"
