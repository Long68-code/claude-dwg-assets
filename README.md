# claude-dwg-assets.

Prebuilt LibreDWG binaries for the Claude `dwg-reader` skill.

## Contents
- `libredwg_ubuntu24.tar.gz` (6.1 MB) — `dwg2dxf` + `libredwg.so.0`,
  LibreDWG 0.14.8531, built on Ubuntu 24.04 x86_64, stripped.

## Why
Ubuntu has no apt package for LibreDWG; building from source takes ~25 minutes.
This bundle drops that to a few seconds.

## Usage
    mkdir -p /tmp/libredwg && cd /tmp/libredwg
    curl -sL <raw-url-of-the-tar.gz> -o libredwg.tar.gz
    tar xzf libredwg.tar.gz && chmod +x dwg2dxf
    LD_LIBRARY_PATH=/tmp/libredwg ./dwg2dxf -y -o out.dxf in.dwg

## Verified on
DWG R14 (AC1014), 2007 (AC1021), 2013 (AC1027), 2018 (AC1032) —
including real Vietnamese architectural drawings.

## License
LibreDWG is GPLv3 — https://github.com/LibreDWG/libredwg
These are unmodified builds of upstream source.
