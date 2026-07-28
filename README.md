# claude-dwg-assets

Prebuilt binaries and scripts for the Claude `dwg-reader` skill.

## Contents

- `libredwg_ubuntu24.tar.gz` (6.1 MB) — `dwg2dxf` + `libredwg.so.0`,
  LibreDWG 0.14.8531, built on Ubuntu 24.04 x86_64, stripped.
- `dwg_to_pdf_v5.py` — the full DWG→PDF pipeline, one page per title-block
  sheet. v5 (2026-07-28) fixes three silent-failure bugs found in v4:
  xclipped INSERTs bucketed/dropped by their raw bbox (blank sheets),
  one wide entity squeezing a whole sheet into a thin band, and a
  whole-page ink self-check that could not see either failure. Also adds
  a shared bbox cache, zone-based self-check, and a per-stage timing table.
  Requires `vncodec.py` from the skill (Vietnamese TCVN3/VNI decoding).

## Why

Ubuntu has no apt package for LibreDWG; building from source takes ~25 minutes.
This bundle drops that to a few seconds. The script rides along so the skill
can always fetch its newest version without re-saving the skill itself.

## Usage

    mkdir -p /tmp/libredwg && cd /tmp/libredwg
    curl -sL <raw-url-of-the-tar.gz> -o libredwg.tar.gz
    tar xzf libredwg.tar.gz && chmod +x dwg2dxf
    LD_LIBRARY_PATH=/tmp/libredwg ./dwg2dxf -y -o out.dxf in.dwg

Full pipeline (needs `pip install ezdxf pymupdf numpy` and `vncodec.py`
next to the script):

    python3 dwg_to_pdf_v5.py IN.dwg OUT.pdf --mono \
            --cache /tmp/dwgcache --dwg2dxf /tmp/libredwg/dwg2dxf

## Verified on

DWG R14 (AC1014), 2007 (AC1021), 2013 (AC1027), 2018 (AC1032) —
including real Vietnamese architectural drawings (TCVN3 + VNI-Windows
+ Unicode mixed in one file).

## License

LibreDWG is GPLv3 — https://github.com/LibreDWG/libredwg
The bundle contains unmodified builds of upstream source; `dwg2dxf` is
invoked as a separate process (aggregation, not linking).
`dwg_to_pdf_v5.py` is the skill author's own code.
