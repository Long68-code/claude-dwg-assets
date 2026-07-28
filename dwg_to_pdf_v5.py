#!/usr/bin/env python3
"""DWG/DXF -> multi-page PDF, one page per title-block sheet.  v5 (2026-07-28)

What v5 fixes over v4 (all three found on a real xclipped renovation drawing):

  1. XCLIP. The main plan lived inside an INSERT with an active spatial filter
     (xclip). v4 bucketed by the RAW bbox centre (outside every frame -> orphan)
     and tame_runaway_dashes measured the RAW span (-> "junk", entity DROPPED).
     Result: three blank sheets that passed the ink check. v5 computes the
     effective bbox = raw bbox ∩ xclip outer bounds and uses it everywhere.
  2. Sheet stretched into a thin band. One wide HATCH whose bbox reached far
     outside the frame made fit_page shrink the whole sheet. v5 pins the render
     window with get_pdf_bytes(page, render_box=<frame rect>). --no-crop
     disables this if a drawing legitimately draws past its frame.
  3. Ink self-check was blind. Whole-page ink% cannot see "drawing area empty,
     title block fine" (~20% of the sheet is title block). v5 measures the
     drawing zone and the title zone separately, and flags content squeezed
     into a thin band (the symptom of bug 2).

Speed-ups, measured targets:
  * ONE shared bbox cache. v4 called bbox.extents() per entity in
    tame_runaway_dashes AND again in bucket_entities - two full geometry
    sweeps over the same modelspace.
  * ink check via numpy instead of a per-pixel Python loop (~50x).
  * timing table printed at the end: per stage + total DWG->PDF.

Design rules carried over from v4 (still true, still the hard way):
  * NEVER silently drop a sheet. Count frames in, count pages out, shout if
    they differ.
  * ezdxf.readfile, not recover.readfile.
  * One fresh PyMuPdfBackend PER SHEET. The pymupdf player mutates the
    recordings: calling get_pdf_bytes() twice on one backend returns blank
    pages that still report success.
  * There is no "keep text as text" flag in ezdxf; text is ~15% of render
    time, keep FILLING.

Usage:
    python dwg_to_pdf_v5.py IN.dwg|IN.dxf OUT.pdf [--mono] [--cache DIR]
                            [--only N,M] [--dwg2dxf PATH] [--no-crop]
"""
from __future__ import annotations
import argparse, math, os, re, subprocess, sys, time, unicodedata
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collections import Counter

import fitz
import ezdxf
from ezdxf import bbox
from ezdxf.math import BoundingBox2d
from ezdxf.addons.drawing import RenderContext, Frontend, layout
from ezdxf.addons.drawing.config import (Configuration, BackgroundPolicy, LineweightPolicy,
                                         ColorPolicy)
from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend

T0 = time.time()
def log(*a): print(f"[{time.time()-T0:7.1f}s]", *a, flush=True)

# ------------------------------------------------------------------ timing
class Timing:
    """Per-stage wall clock. mark() closes the current stage and opens the next."""
    def __init__(self):
        self.rows: list[tuple[str, float]] = []
        self._t = time.time()
    def mark(self, name: str):
        now = time.time()
        self.rows.append((name, now - self._t))
        self._t = now
    def report(self):
        total = time.time() - T0
        print("\n--- TIMING ---")
        for name, dt in self.rows:
            print(f"  {dt:7.1f}s  {100*dt/total:5.1f}%  {name}")
        print(f"  {total:7.1f}s  100.0%  TOTAL (script start -> PDF on disk)")
        return total

TIMING = Timing()

# ---------------------------------------------------------------- TCVN3 decode
TCVN3 = {'¡':'Ă','¢':'Â','£':'Ê','¤':'Ô','¥':'Ơ','¦':'Ư','§':'Đ','¨':'ă','©':'â','ª':'ê',
 '«':'ô','¬':'ơ','\xad':'ư','®':'đ','µ':'à','¶':'ả','·':'ã','¸':'á','¹':'ạ','»':'ằ','¼':'ẳ',
 '½':'ẵ','¾':'ắ','Æ':'ặ','Ç':'ầ','È':'ẩ','É':'ẫ','Ê':'ấ','Ë':'ậ','Ì':'è','Î':'ẻ','Ï':'ẽ',
 'Ð':'é','Ñ':'ẹ','Ò':'ề','Ó':'ể','Ô':'ễ','Õ':'ế','Ö':'ệ','×':'ì','Ø':'ỉ','Ü':'ĩ','Ý':'í',
 'Þ':'ị','ß':'ò','á':'ỏ','â':'õ','ã':'ó','ä':'ọ','å':'ồ','æ':'ổ','ç':'ỗ','è':'ố','é':'ộ',
 'ê':'ờ','ë':'ở','ì':'ỡ','í':'ớ','î':'ợ','ï':'ù','ñ':'ủ','ò':'ũ','ó':'ú','ô':'ụ','õ':'ừ',
 'ö':'ử','÷':'ữ','ø':'ứ','ù':'ự','ú':'ỳ','û':'ỷ','ü':'ỹ','ý':'ý','þ':'ỵ'}
AMBIG = set("ÂÊÔÝÌÒÓÕÉÈàáâãèéêìíòóôõùúý")
JUNK = set(TCVN3) - AMBIG
NFC = lambda s: unicodedata.normalize("NFC", s)

# LESSON 2026-07: VN fonts in Vietnamese drawings also ship as VH* (VHTime, VHArial),
# not only .Vn*. Missing the vh prefix left half the sheets undecoded.
def is_vn(f: str) -> bool:
    f = (f or "").lower().lstrip(".")
    return f.startswith("vn") or f.startswith("vh")

def is_upper(f: str) -> bool:
    parts = [p for p in re.split(r"[-.|]", (f or "").replace(".TTF", "").replace(".ttf", "")) if p]
    return bool(parts) and (parts[-1].endswith("H") or "H-Bold" in (f or ""))

def conv(run: str, font: str) -> str:
    if not is_vn(font):
        return NFC(run)
    out = "".join(TCVN3.get(c, c) for c in run) if (JUNK & set(run)) else run
    return NFC(out.upper() if is_upper(font) else out)

CODE = re.compile(r"\\[\\{}]|\\[fF][^;]*;|\\[HWQTACSKkpX][^;]*;|\\[PLlOoNn~]")

def conv_mtext(s: str, base: str) -> str:
    out, font, i = [], base, 0
    for m in CODE.finditer(s):
        if m.start() > i:
            out.append(conv(s[i:m.start()], font))
        tok = m.group(0)
        if tok[:2].lower() == "\\f":
            font = tok[2:-1].split("|")[0]
            if is_vn(font):
                tok = "\\fArial" + tok[2 + len(font):]
        out.append(tok)
        i = m.end()
    if i < len(s):
        out.append(conv(s[i:], font))
    return "".join(out)

def decode_doc(doc, text_width: float = 1.0) -> int:
    """v4: decide the encoding per TEXT STYLE from the actual glyph evidence.

    v3 looked only at the style's *font* name and only knew TCVN3.  Real files
    leave the font blank, mix TCVN3 + VNI-Windows + Unicode in one drawing, and
    hide the hint in the style name.  Deciding per string also fails: a label
    like 'VÒ' is too short to vote on.  So: pool every string of a style, vote
    once, apply to all -- including ATTRIBs and block definitions.
    """
    import vncodec
    sf = {st.dxf.name: (st.dxf.font or "") for st in doc.styles}
    spaces = ([doc.modelspace()]
              + [l for l in doc.layouts if l.name.lower() != "model"]
              + list(doc.blocks))

    ents = []
    for sp in spaces:
        for e in sp:
            if e.dxftype() in ("TEXT", "ATTRIB", "ATTDEF", "MTEXT"):
                ents.append(e)
            if e.dxftype() == "INSERT":
                ents.extend(e.attribs)

    pool = {}
    for e in ents:
        st = e.dxf.get("style", "Standard") or "Standard"
        txt = e.text if e.dxftype() == "MTEXT" else e.dxf.get("text", "")
        pool.setdefault(st, []).append(txt)

    dec = {st: vncodec.style_decoder(v, st, sf.get(st, ""))
           for st, v in pool.items()}
    tally = Counter(enc for enc, _ in dec.values())
    n = 0
    for e in ents:
        st = e.dxf.get("style", "Standard") or "Standard"
        enc, fn = dec.get(st, ("unicode", lambda x: x))
        if enc == "unicode":
            continue
        if e.dxftype() == "MTEXT":
            new = vncodec.decode_mtext(e.text, fn)
            if new != e.text:
                e.text = new; n += 1
        else:
            old = e.dxf.get("text", "")
            new = fn(old)
            if new != old:
                e.dxf.text = new; n += 1

    # Point every legacy style at a Unicode font that has the glyphs -- and match
    # its WIDTH.  The drawing's own styles are VNI-Helve *Condense*; substituting
    # a normal-width face made every label wider than the box the designer drew
    # it into, so text ran over neighbouring text and over linework.  Width
    # factor is 1.00 in these styles, i.e. the narrowness lives in the face.
    narrowed = 0
    for st in doc.styles:
        f = (st.dxf.font or "")
        name = st.dxf.name
        if dec.get(name, ("", None))[0] not in ("vni", "tcvn3") and not is_vn(f):
            continue
        tag = f"{name} {f}".lower()
        bold = any(k in tag for k in ("bold", "black", "-b", "cb"))
        cond = any(k in tag for k in ("conden", "cond", "narrow", "helve"))
        if cond:
            st.dxf.font = ("DejaVuSansCondensed-Bold.ttf" if bold
                           else "DejaVuSansCondensed.ttf")
            narrowed += 1
        else:
            st.dxf.font = ("LiberationSans-Bold.ttf" if bold
                           else "LiberationSans-Regular.ttf")
        if text_width != 1.0:
            st.dxf.width = (st.dxf.width or 1.0) * text_width
    if narrowed:
        log(f"condensed substitute font for {narrowed} style(s)")
    log(f"encoding vote per style: {dict(tally)}")
    return n


# ------------------------------------------------- auto page + scale (v4)
# v3 defaulted to "--page 420x297" and compared that to the title-block bbox in
# DRAWING UNITS.  Any drawing at 1:50 / 1:100 (i.e. most of them) has a frame of
# 21000 or 42000 units, matches nothing, and the run aborts with "NO title-block
# frame".  Detect the paper size AND the plot scale instead of demanding them.
A_SERIES = [("A0", 1189.0, 841.0), ("A1", 841.0, 594.0), ("A2", 594.0, 420.0),
            ("A3", 420.0, 297.0), ("A4", 297.0, 210.0)]
SCALES = [1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000]


def detect_page(doc, msp, tol=0.06, want=None):
    """-> (page_w_units, page_h_units, unit_scale, label) or None."""
    counts = Counter(i.dxf.name for i in msp.query("INSERT"))
    best = None
    for name, n in counts.items():
        try:
            b = bbox.extents(doc.blocks.get(name), fast=True)
        except Exception:
            continue
        if not b.has_data or math.isnan(b.size.x) or b.size.x <= 0 or b.size.y <= 0:
            continue
        for label, pw, ph in A_SERIES:
            if want and label != want.upper():
                continue
            for w, h in ((pw, ph), (ph, pw)):
                for s in SCALES:
                    if (abs(b.size.x - w*s)/(w*s) < tol
                            and abs(b.size.y - h*s)/(h*s) < tol):
                        kw = any(k in name.upper()
                                 for k in ("KHUNG", "TITLE", "FRAME", "TEM", "SHEET", "KT"))
                        # A1 1:50 and A3 1:100 describe the SAME frame; prefer
                        # the smaller sheet -- that is what the title block of a
                        # Vietnamese set almost always says, and it prints.
                        cand = (kw, n, -w, b.size.x*b.size.y,
                                (w*s, h*s, s, f"{label} 1:{s:g} ({name})"))
                        if best is None or cand[:4] > best[:4]:
                            best = cand
    return best[4] if best else None

# ------------------------------------------------- MTEXT width=0 (v4, the big one)
# AutoCAD reads MTEXT reference-rectangle width 0 as "no wrapping at all".
# ezdxf reads it literally as a zero-wide column and breaks the line at EVERY
# space, so a title-block line like " THUOC TOA CHUNG CU" comes out as 8 stacked
# lines that bury whatever MTEXT sits below it.  Measured: box height / char
# height = 7.97 for that string, 1.29 for a single-token string.
# This is the real cause of "chữ chồng đè lên nhau" -- not the font, not the
# encoding.  Give every width==0 MTEXT a rectangle wide enough to never wrap.
def fix_mtext_zero_width(doc):
    from ezdxf.fonts import fonts as _f
    spaces = ([doc.modelspace()]
              + [l for l in doc.layouts if l.name.lower() != "model"]
              + list(doc.blocks))
    styles = {st.dxf.name: (st.dxf.font or "") for st in doc.styles}
    cache = {}
    n = 0
    for sp in spaces:
        for e in sp:
            if e.dxftype() != "MTEXT" or e.dxf.get("width", 0):
                continue
            h = e.dxf.char_height or 1.0
            fname = styles.get(e.dxf.get("style", "Standard"), "") or "DejaVuSansCondensed.ttf"
            font = cache.get(fname)
            if font is None:
                try:
                    font = _f.make_font(fname, 1.0)
                except Exception:
                    font = _f.make_font("DejaVuSansCondensed.ttf", 1.0)
                cache[fname] = font
            try:
                lines = e.plain_text(split=True)
            except Exception:
                lines = [e.text]
            widest = max((font.text_width(l) * h for l in lines), default=h)
            e.dxf.width = max(widest * 1.25, h * 2)
            n += 1
    return n

# ------------------------------------------------- MTEXT background fill
# Every dimension text in a Vietnamese drawing tends to carry its own MTEXT background
# mask (bg_fill=3, often with box_fill_scale=0). AutoCAD paints it behind the glyphs;
# ezdxf paints it on top. In colour output it shows as a yellow lozenge over the number;
# in monochrome it turns into a black block that swallows the digits entirely
# ("9780" -> "9<block>0"). Nothing in Configuration controls it — clear the attribute.
def disable_mtext_bg_fill(doc):
    n = 0
    spaces = [doc.modelspace()] + [l for l in doc.layouts if l.name.lower() != "model"] \
             + list(doc.blocks)
    for sp in spaces:
        for e in sp:
            if e.dxftype() == "MTEXT" and e.dxf.get("bg_fill", 0):
                e.dxf.bg_fill = 0
                n += 1
    return n

# ------------------------------------------------- non-plotting layers
# AutoCAD does not print layers whose "plot" flag is off — Defpoints, wipeout helper
# layers, template bounds, and plotter guide layers. ezdxf's frontend ignores that flag
# and happily draws them, which is where the ugly red diagonal across every sheet came
# from (layer KCS_Plotter). It is also where the 176,000-dash line lived. Switching the
# layers off makes the frontend skip them everywhere, including inside block references.
def disable_noplot_layers(doc):
    off = []
    for lay in doc.layers:
        if not lay.dxf.plot:
            off.append(lay.dxf.name)
            lay.off()
    return off

# ------------------------------------------------- shared bbox cache (v5)
# v4 swept the full modelspace geometry TWICE: bbox.extents per entity in
# tame_runaway_dashes and again in bucket_entities. On a 7,000-entity file that
# is the bulk of the preprocessing seconds. One cache, keyed by handle.
class BBoxCache:
    def __init__(self):
        self.d: dict[str, object] = {}
        self.hits = 0
        self.misses = 0
    def get(self, e):
        h = e.dxf.handle
        if h in self.d:
            self.hits += 1
            return self.d[h]
        self.misses += 1
        try:
            b = bbox.extents([e], fast=True)
            v = b if (b.has_data and not math.isnan(b.size.x)) else None
        except Exception:
            v = None
        self.d[h] = v
        return v
    def override(self, handle: str, box2d: BoundingBox2d):
        """Replace a raw bbox with an effective (e.g. xclipped) one."""
        class _B:  # minimal stand-in with the fields we read
            pass
        b = _B()
        b.extmin = box2d.extmin; b.extmax = box2d.extmax
        b.size = box2d.size; b.center = box2d.center
        b.has_data = True
        self.d[handle] = b

# ------------------------------------------------- XCLIP effective bboxes (v5)
# THE BLANK-SHEET BUG. The whole floor plan sat inside an INSERT with an active
# spatial filter (xclip). Its RAW bbox is the full block extents — as big as the
# whole building — so (a) bucket_entities put its centre outside every frame ->
# orphaned, and (b) tame_runaway_dashes measured a runaway span and DROPPED it
# as junk. Three sheets rendered blank and the whole-page ink check passed them.
# ezdxf DOES apply the clip when drawing; only our bookkeeping was blind.
# Effective bbox = raw bbox ∩ clip outer bounds, written into the shared cache
# so every downstream consumer (bucketing, dash guard, orphan page) agrees.
def apply_xclip_bboxes(msp, cache: BBoxCache):
    from ezdxf.xclip import XClip
    fixed = []
    for ins in msp.query("INSERT"):
        try:
            cl = XClip(ins)
            if not (cl.has_clipping_path and cl.is_clipping_enabled):
                continue
            clip_box = cl.get_wcs_clipping_path().outer_bounds()
            if not clip_box.has_data:
                continue
        except Exception:
            continue
        raw = cache.get(ins)
        if raw is not None:
            inter = clip_box.intersection(
                BoundingBox2d([raw.extmin, raw.extmax]))
            eff = inter if inter.has_data else clip_box
        else:
            eff = clip_box
        cache.override(ins.dxf.handle, eff)
        fixed.append(ins.dxf.handle)
    return fixed

# ------------------------------------------------- runaway dashed-line guard
# THE 30-MINUTE BUG (measured 2026-07-28). One stray LINE on layer KCS_Plotter was
# 331,274 drawing units long and carried linetype HIDDEN (pattern cycle 0.375 x
# $LTSCALE 5 = 1.875 units). ezdxf's default line_policy=ACCURATE faithfully emitted
# ~177,000 individual dashes for that single entity: get_pdf_bytes went from 1.0s to
# over 40 minutes. LinePolicy.APPROXIMATE does NOT save you — measured, still hangs.
# LinePolicy.SOLID works but throws away every hidden/centre line in the drawing,
# which in a construction document is information, not decoration.
# So: keep ACCURATE, and demote to Continuous only the individual entities whose dash
# count is absurd. Typically that is a handful of stray construction lines.
#
# v5: spans come from the SHARED cache, where xclipped INSERTs already carry
# their effective (clipped) bbox — so a clipped plan block is measured by what
# it SHOWS, not by the size of the block behind the clip. That is what stops
# the guard from dropping the main drawing as "junk".
def tame_runaway_dashes(doc, msp, cache: BBoxCache, max_dashes=2000, junk_dashes=20000):
    plen = {}
    for lt in doc.linetypes:
        name = lt.dxf.name.upper()
        total = 0.0
        try:
            for tag in lt.pattern_tags.tags:
                if tag.code == 40:
                    total = abs(float(tag.value)); break
        except Exception:
            pass
        plen[name] = total
    layer_lt = {l.dxf.name.upper(): (l.dxf.linetype or "CONTINUOUS").upper() for l in doc.layers}
    gscale = float(doc.header.get("$LTSCALE", 1.0) or 1.0)
    tamed, junk = [], set()
    for e in msp:
        lt = (e.dxf.get("linetype", "BYLAYER") or "BYLAYER").upper()
        if lt in ("BYLAYER", "BYBLOCK"):
            lt = layer_lt.get((e.dxf.get("layer", "0") or "0").upper(), "CONTINUOUS")
        cycle = plen.get(lt, 0.0)
        if cycle <= 0:
            continue                      # continuous / solid -> nothing to blow up
        cycle *= gscale * float(e.dxf.get("ltscale", 1.0) or 1.0)
        if cycle <= 0:
            continue
        b = cache.get(e)
        if b is None:
            continue
        span = math.hypot(b.size.x, b.size.y)
        dashes = span / cycle
        if dashes > junk_dashes:
            # a dashed line this long is a stray construction/plotter guide, not
            # drawing content. Turning it solid paints an ugly diagonal across the
            # sheet, so drop it entirely.
            junk.add(e.dxf.handle)
            tamed.append((e.dxf.handle, e.dxftype(), e.dxf.get("layer", ""), lt,
                          int(dashes), "dropped"))
        elif dashes > max_dashes:
            tamed.append((e.dxf.handle, e.dxftype(), e.dxf.get("layer", ""), lt,
                          int(dashes), "solid"))
            e.dxf.linetype = "Continuous"
    return tamed, junk

# ---------------------------------------------------------------- sheet frames
SHEET_CODE = re.compile(r"\b([A-Z]{1,3})\s*[-–]\s*(\d{1,3})\b", re.I)

def find_title_blocks(doc, msp, page_w, page_h, tol=0.06):
    """Return [(block_name, pw, ph)] for EVERY block whose definition matches the page
    size in either orientation. v1 kept only the single best candidate, which silently
    dropped every sheet drawn with a second frame block."""
    counts = Counter(i.dxf.name for i in msp.query("INSERT"))
    out = []
    for name, n in counts.items():
        try:
            b = bbox.extents(doc.blocks.get(name), fast=True)
        except Exception:
            continue
        if not b.has_data or not (b.size.x > 0 and b.size.y > 0):
            continue
        if math.isnan(b.size.x) or math.isnan(b.size.y):
            continue
        for pw, ph in ((page_w, page_h), (page_h, page_w)):
            if abs(b.size.x - pw) / pw < tol and abs(b.size.y - ph) / ph < tol:
                kw = any(k in name.upper() for k in ("KHUNG", "TITLE", "FRAME", "TEM", "SHEET"))
                out.append((kw, n, name, pw, ph))
                break
    out.sort(reverse=True)
    return [(name, pw, ph) for _, _, name, pw, ph in out]

def collect_frames(msp, blocks, page_w, page_h):
    frames = []
    for name, pw, ph in blocks:
        for ins in msp.query(f'INSERT[name=="{name}"]'):
            sx = ins.dxf.xscale or 1.0
            sy = ins.dxf.yscale or sx
            rot = ins.dxf.rotation or 0.0
            x, y = ins.dxf.insert.x, ins.dxf.insert.y
            w, h = pw * sx, ph * sy
            if abs(rot - 270) < 1 or abs(rot - 90) < 1:
                rect = (x, y - w, x + h, y)
            else:
                rect = (x, y, x + w, y + h)
            # the block base point is often NOT the frame corner -> use the real bbox
            try:
                bb = bbox.extents([ins], fast=True)
                if bb.has_data and not math.isnan(bb.size.x) and bb.size.x > 0:
                    rect = (bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y)
            except Exception:
                pass
            att = {a.dxf.tag: (a.dxf.text or "").strip() for a in ins.attribs}
            title = next((v for v in att.values() if len(v) > 3 and not SHEET_CODE.fullmatch(v)), "")
            code = next((v for v in att.values() if SHEET_CODE.fullmatch(v)), "")
            frames.append({"rect": rect, "block": name, "title": title, "code": code, "attrs": att})
    # order: by sheet code when every frame has one, else drawing layout order
    if frames and all(f["code"] for f in frames):
        def key(f):
            m = SHEET_CODE.search(f["code"])
            return (m.group(1).upper(), int(m.group(2))) if m else ("ZZ", 999)
        frames.sort(key=key)
    else:
        frames.sort(key=lambda f: (-f["rect"][3], f["rect"][0]))
    return frames

def bucket_entities(msp, frames, cache: BBoxCache, junk=frozenset()):
    """v4 also RETURNS the orphans instead of just counting them: a working DWG
    keeps details and alternates outside the title-block frames, and silently
    dropping them is the same sin as dropping a sheet.
    v5: centres come from the shared cache -> an xclipped INSERT is bucketed by
    the centre of what it SHOWS inside the clip window, not by the raw block."""
    buckets = [[] for _ in frames]
    orphans = []
    nan = 0
    rects = [f["rect"] for f in frames]
    for ent in msp:
        if ent.dxf.handle in junk:
            continue
        b = cache.get(ent)
        if b is None:
            nan += 1
            continue
        cx, cy = b.center.x, b.center.y
        if math.isnan(cx) or math.isnan(cy):
            nan += 1
            continue
        for i, (x0, y0, x1, y1) in enumerate(rects):
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                buckets[i].append(ent)
                break
        else:
            orphans.append(ent)
    return buckets, nan, orphans


# ------------------------------------------------- runaway HATCH guard
# Same failure as runaway dashes, different entity: a pattern hatch whose line
# spacing is tiny next to its area makes ezdxf emit millions of hatch lines, and
# the cost lands inside get_pdf_bytes(), not draw_entities() -- so per-entity
# timing looks innocent. Measured: one 6030x3355 HATCH took >15 s on its own.
# v5: ALSO scans hatches inside block definitions. A hatch inside an INSERT
# escaped the v4 guard entirely (same blind spot as the xclip bug: every guard
# only looked at top-level modelspace).
def count_dense_hatches(doc, msp, max_lines=200_000):
    """Estimate Σ (diag/offset) × max(1, diag/dash_cycle) per pattern line.
    Do NOT multiply by pattern_scale -- ezdxf already reports the pattern in
    drawing units."""
    def hatch_cost(e):
        try:
            if e.dxf.get("solid_fill", 0):
                return 0
            pat = getattr(e, "pattern", None)
            if pat is None or not pat.lines:
                return 0
            b = bbox.extents([e], fast=True)
            if not b.has_data or math.isnan(b.size.x):
                return 0
            diag = math.hypot(b.size.x, b.size.y)
            total = 0
            for ln in pat.lines:
                off = math.hypot(*ln.offset)
                if off <= 1e-9:
                    continue
                n_lines = diag / off
                cycle = sum(abs(d) for d in (ln.dash_length_items or []))
                per_line = max(1.0, diag / cycle) if cycle > 1e-9 else 1.0
                total += n_lines * per_line
            return total
        except Exception:
            return 0

    dense = sum(1 for e in msp.query("HATCH") if hatch_cost(e) > max_lines)
    in_blocks = 0
    for blk in doc.blocks:
        for e in blk:
            if e.dxftype() == "HATCH" and hatch_cost(e) > max_lines:
                in_blocks += 1
    if in_blocks:
        log(f"dense hatches inside block definitions: {in_blocks} "
            f"(v4 never saw these)")
    return dense + in_blocks


# ---------------------------------------------------------------- ink self-check
# v5: whole-page ink% is a proven liar — it passed 3 blank sheets (title block
# alone carries ~0.5%) and a sheet squeezed into a thin band (0.16%, "ok").
# Measure the DRAWING ZONE and the TITLE ZONE separately. Vietnamese title
# blocks sit in the right ~20% of the sheet. And measure WHERE the ink is:
# content squashed into a narrow band is the fingerprint of a runaway-extent
# entity stretching the viewport.
INK_MIN, INK_MAX = 0.05, 25.0   # LESSON: 0.15 rejected legitimately sparse sheets
TITLE_FRAC = 0.20               # right-hand fraction of the sheet = title block

def ink_stats(page, dpi=72):
    """-> (ink_total%, ink_draw%, ink_title%, spread_x, spread_y).
    spread_* = fraction of the drawing zone spanned by the middle 98% of ink."""
    import numpy as np
    pix = page.get_pixmap(dpi=dpi)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    dark = a[:, :, :3].sum(axis=2) < 300
    W = pix.width
    split = int(W * (1.0 - TITLE_FRAC))
    draw, title = dark[:, :split], dark[:, split:]
    pct = lambda m: 100.0 * m.mean() if m.size else 0.0
    ys, xs = np.nonzero(draw)
    if len(xs) > 20:
        lo, hi = np.percentile(xs, [1, 99]); sx = (hi - lo) / max(split, 1)
        lo, hi = np.percentile(ys, [1, 99]); sy = (hi - lo) / max(pix.height, 1)
    else:
        sx = sy = 0.0
    return pct(dark), pct(draw), pct(title), sx, sy

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("out")
    ap.add_argument("--page", default="auto",
                    help="WxH in DRAWING UNITS, or 'auto' to detect "
                         "paper size and plot scale from the frame block")
    ap.add_argument("--block", default=None)
    ap.add_argument("--cache", default=None, help="dir for per-sheet pdfs (resumable)")
    ap.add_argument("--only", default=None, help="render only these sheet indices, e.g. 0,1,2")
    ap.add_argument("--dwg2dxf", default="dwg2dxf")
    ap.add_argument("--no-orphan-page", dest="orphan_page", action="store_false",
                    help="skip the extra page collecting work outside the frames")
    ap.add_argument("--orphan-min", type=int, default=50,
                    help="minimum orphan entities before that page is worth making")
    ap.add_argument("--orphan-shrink", type=float, default=3.0,
                    help="how much smaller than 1:1 the orphan page is drawn")
    ap.add_argument("--paper", default=None,
                    help="force the sheet size (A0..A4) instead of detecting it")
    ap.add_argument("--text-width", type=float, default=1.0,
                    help="extra width-factor on substituted fonts; <1 narrows "
                         "text when the replacement face is wider than the "
                         "original (VNI-Helve Condense etc.)")
    ap.add_argument("--unit-scale", type=float, default=1.0,
                    help="drawing units per mm of paper (e.g. 100 for a 1:100 frame)")
    ap.add_argument("--mono", action="store_true",
                    help="plot everything black on white, like AutoCAD monochrome.ctb — "
                         "much easier to read than the CAD screen colours")
    ap.add_argument("--lw-scale", type=float, default=None,
                    help="lineweight multiplier (mono defaults to 1.6)")
    ap.add_argument("--min-lw", type=float, default=None,
                    help="minimum lineweight in mm (mono defaults to 0.30)")
    ap.add_argument("--no-crop", dest="crop", action="store_false",
                    help="do not pin the render window to the frame rectangle "
                         "(v5 default pins it, so an entity whose extents leak "
                         "past the frame cannot shrink the whole sheet)")
    a = ap.parse_args()
    page_w = page_h = None
    if a.page.lower() != "auto":
        page_w, page_h = (float(v) for v in a.page.lower().split("x"))

    src = a.src
    if src.lower().endswith(".dwg"):
        dxf = os.path.join(a.cache or "/tmp", os.path.basename(src)[:-4] + ".dxf")
        if not os.path.exists(dxf):
            os.makedirs(os.path.dirname(dxf), exist_ok=True)
            # the prebuilt bundle ships libredwg.so.0 next to the binary, so
            # point the loader at it -- forgetting this is a silent CalledProcessError
            env = dict(os.environ)
            here = os.path.dirname(os.path.abspath(a.dwg2dxf))
            if os.path.exists(os.path.join(here, "libredwg.so.0")):
                env["LD_LIBRARY_PATH"] = here + os.pathsep + env.get("LD_LIBRARY_PATH", "")
            r = subprocess.run([a.dwg2dxf, "-y", "-o", dxf, src], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if r.returncode != 0 or not os.path.exists(dxf):
                log("dwg2dxf failed: " + r.stderr.decode(errors="replace")[-300:])
                sys.exit(3)
        log(f"dwg2dxf -> {dxf} ({os.path.getsize(dxf)/1e6:.1f} MB)")
        src = dxf
    TIMING.mark("dwg2dxf convert")

    doc = ezdxf.readfile(src)          # NOT recover.readfile — see module docstring
    msp = doc.modelspace()
    log(f"loaded | acad={doc.acad_release} msp_entities={len(msp)}")
    TIMING.mark("ezdxf load")

    if page_w is None:
        found = detect_page(doc, msp, want=a.paper)
        if not found:
            log("could not detect a paper size -- pass --page WxH in drawing units")
            sys.exit(2)
        page_w, page_h, auto_scale, label = found
        if a.unit_scale == 1.0:
            a.unit_scale = auto_scale
        log(f"detected sheet: {label}  frame={page_w:.0f}x{page_h:.0f} units")

    n = decode_doc(doc, a.text_width)
    log(f"VN text decoded: {n} text entities")

    nzw = fix_mtext_zero_width(doc)
    if nzw:
        log(f"MTEXT width=0 rectangles widened: {nzw} (ezdxf wraps at every space)")

    nbg = disable_mtext_bg_fill(doc)
    if nbg:
        log(f"MTEXT background masks cleared: {nbg}")

    noplot = disable_noplot_layers(doc)
    if noplot:
        log(f"non-plotting layers switched off: {noplot}")
    TIMING.mark("decode + text/layer fixes")

    # ---- one shared geometry sweep (v5) --------------------------------
    cache = BBoxCache()
    xfixed = apply_xclip_bboxes(msp, cache)
    if xfixed:
        log(f"xclipped INSERTs, effective bbox = clip window: {len(xfixed)}")

    tamed, junk = tame_runaway_dashes(doc, msp, cache)
    if tamed:
        n_drop = sum(1 for t in tamed if t[5] == "dropped")
        log(f"runaway dashed entities: {len(tamed)} ({n_drop} dropped, "
            f"{len(tamed)-n_drop} forced solid)")
        for h, t, lay, lt, dashes, act in sorted(tamed, key=lambda r: -r[4])[:6]:
            log(f"    {act:7s} {t} h={h} layer={lay} lt={lt} ~{dashes:,} dashes")

    gone = [im for im in list(msp.query("IMAGE"))]
    if gone:
        names = []
        for im in gone:
            try:
                names.append(im.image_def.dxf.filename)
            except Exception:
                names.append("?")
            msp.delete_entity(im)
        log(f"IMAGE refs dropped (pixels not in the DWG): {names}")

    dense = count_dense_hatches(doc, msp)
    if dense:
        log(f"dense pattern hatches: {dense} -> approximate hatching")

    blocks = ([(a.block, page_w, page_h)] if a.block
              else find_title_blocks(doc, msp, page_w, page_h))
    if not blocks:
        log("NO title-block frame matches the page size — nothing to page up."); sys.exit(2)
    log(f"title blocks: {[b[0] for b in blocks]}")

    frames = collect_frames(msp, blocks, page_w, page_h)
    buckets, nan, orphan = bucket_entities(msp, frames, cache, junk)
    N_IN = len(frames)
    log(f"{N_IN} sheet frames | nan-bbox skipped={nan} "
        f"orphan entities={len(orphan)} | bbox cache: "
        f"{cache.misses} computed, {cache.hits} reused")
    TIMING.mark("geometry sweep + guards + bucketing")

    only = {int(v) for v in a.only.split(",")} if a.only else None
    cache_dir = a.cache
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)

    ctx = RenderContext(doc); ctx.set_current_layout(msp)
    ckw = {}
    if a.mono:
        ckw["color_policy"] = ColorPolicy.BLACK
        ckw["lineweight_scaling"] = a.lw_scale if a.lw_scale is not None else 1.6
        ckw["min_lineweight"] = a.min_lw if a.min_lw is not None else 0.30
        log("mono mode: everything black on white, lineweights boosted")
    else:
        if a.lw_scale is not None:
            ckw["lineweight_scaling"] = a.lw_scale
        if a.min_lw is not None:
            ckw["min_lineweight"] = a.min_lw
    if dense:
        from ezdxf.addons.drawing.config import HatchPolicy
        ckw["hatch_policy"] = HatchPolicy.SHOW_APPROXIMATE_PATTERN
        ckw["min_hatch_line_distance"] = 20.0
        ckw["hatching_timeout"] = 5.0
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE,
                        lineweight_policy=LineweightPolicy.ABSOLUTE, **ckw)
    PAGE = layout.Page(page_w / a.unit_scale, page_h / a.unit_scale,
                       layout.Units.mm, layout.Margins.all(3))

    parts = []
    for i, (fr, sel) in enumerate(zip(frames, buckets)):
        cp = os.path.join(cache_dir, f"s{i:03d}.pdf") if cache_dir else None
        if cp and os.path.exists(cp):
            parts.append(cp); continue
        if only is not None and i not in only:
            parts.append(None); continue
        t = time.time()
        # A FRESH backend per sheet is mandatory: the pymupdf player mutates the
        # recordings ("This player changes the original recordings!"), so a
        # second get_pdf_bytes() on the same backend yields a blank page that
        # still reports success.
        be = PyMuPdfBackend(); be.set_background("#FFFFFF")
        Frontend(ctx, be, config=cfg).draw_entities(sel)
        be.finalize()
        # v5: pin the render window to the frame rectangle. Without this, ONE
        # entity whose extents leak past the frame (wide hatch, stray line)
        # re-scales fit_page and squeezes the whole sheet into a thin band.
        rb = None
        if a.crop:
            x0, y0, x1, y1 = fr["rect"]
            rb = BoundingBox2d([(x0, y0), (x1, y1)])
        data = be.get_pdf_bytes(PAGE, render_box=rb)
        if cp:
            open(cp, "wb").write(data); parts.append(cp)
        else:
            parts.append(data)
        log(f"  sheet {i:02d} n={len(sel):5d} {time.time()-t:5.1f}s "
            f"{len(data)/1024:7.0f} KB  {fr['code']} {fr['title'][:40]}")


    # ---------------- one extra page for everything outside the frames --------
    if a.orphan_page and len(orphan) >= a.orphan_min:
        cp = os.path.join(cache_dir, "s999_orphan.pdf") if cache_dir else None
        if cp and os.path.exists(cp):
            parts.append(cp)
        else:
            pts = []
            for e in orphan:
                b = cache.get(e)
                if b is None or math.isnan(b.center.x):
                    continue
                pts.append((b.center.x, b.center.y, e))
            xs = sorted(p[0] for p in pts); ys = sorted(p[1] for p in pts)
            k = len(xs)
            lo_x, hi_x = xs[int(k*0.005)], xs[int(k*0.995)]
            lo_y, hi_y = ys[int(k*0.005)], ys[int(k*0.995)]
            sub = [p[2] for p in pts if lo_x <= p[0] <= hi_x and lo_y <= p[1] <= hi_y]
            ow = (hi_x-lo_x)/a.unit_scale/a.orphan_shrink*1.05
            oh = (hi_y-lo_y)/a.unit_scale/a.orphan_shrink*1.05
            t = time.time()
            be = PyMuPdfBackend(); be.set_background("#FFFFFF")
            Frontend(ctx, be, config=cfg).draw_entities(sub)
            be.finalize()
            data = be.get_pdf_bytes(layout.Page(ow, oh, layout.Units.mm,
                                                layout.Margins.all(5)))
            if cp:
                open(cp, "wb").write(data); parts.append(cp)
            else:
                parts.append(data)
            log(f"  orphan page n={len(sub):5d} {time.time()-t:5.1f}s "
                f"{len(data)/1024:7.0f} KB  {ow:.0f}x{oh:.0f} mm")
    TIMING.mark("render all sheets")

    if any(p is None for p in parts):
        log("partial run (--only) — not assembling"); return

    out = fitz.open()
    for p in parts:
        d = fitz.open(p) if isinstance(p, str) else fitz.open("pdf", p)
        out.insert_pdf(d); d.close()
    out.save(a.out, deflate=True, deflate_images=True, garbage=4, clean=True)
    N_OUT = len(out)
    log(f"saved {a.out} | {N_OUT} pages | {os.path.getsize(a.out)/1e6:.1f} MB")
    TIMING.mark("assemble + save")

    # ------------------------------------------------ mandatory self-check
    print("\n--- SELF CHECK (v5: draw zone and title zone measured separately) ---")
    chk = fitz.open(a.out)
    bad = []
    for i, pg in enumerate(chk):
        is_sheet = i < len(frames)
        ink, ink_d, ink_t, sx, sy = ink_stats(pg)
        n_ent = len(buckets[i]) if i < len(buckets) else 999
        if n_ent < 5 and ink_d < INK_MIN:
            # v5: entity count alone cannot condemn a sheet -- ONE xclipped
            # INSERT can carry the entire floor plan. Only empty if the
            # drawing zone is actually blank too.
            st = "RASTER/EMPTY"
        elif is_sheet and ink_d < INK_MIN and ink_t >= INK_MIN:
            # the v4 blind spot: title block inked, drawing area blank
            st = "EMPTY DRAW AREA"
        elif is_sheet and n_ent >= 20 and 0 < min(sx, sy) < 0.35:
            # the other v4 blind spot: content squeezed into a thin band.
            # The squeeze hits ONE axis (a wide entity shrinks everything
            # vertically), so test min(sx, sy) -- max() stays high and lies.
            st = "THIN BAND"
        elif ink < INK_MIN:
            st = "TOO FAINT"
        elif ink > INK_MAX:
            st = "TOO DARK"
        else:
            st = "ok"
        if st != "ok":
            bad.append((i, st))
        tag = (f"{frames[i]['code']} {frames[i]['title'][:32]}"
               if is_sheet else "OUTSIDE THE FRAMES (working area)")
        print(f"  p{i+1:02d} ink={ink:5.2f}% draw={ink_d:5.2f}% title={ink_t:5.2f}% "
              f"spread={sx:4.2f}x{sy:4.2f} n={n_ent:5d} {st:15s} {tag}")
    extra_pages = 1 if (a.orphan_page and N_OUT == N_IN + 1) else 0
    print(f"\nframes in = {N_IN}   pages out = {N_OUT}"
          + ("  (last page = work outside the frames)" if extra_pages else ""))
    if N_OUT - extra_pages != N_IN:
        print(f"*** PAGE COUNT MISMATCH: {N_IN - N_OUT + extra_pages} sheet(s) lost. "
              f"DO NOT ship this as complete. ***")
    if bad:
        print("*** PROBLEM SHEETS — report these to the user verbatim: ***")
        for i, st in bad:
            tag = (f"{frames[i]['code']} {frames[i]['title']}"
                   if i < len(frames) else "outside-frames page")
            print(f"    p{i+1:02d} {st}  {tag}")
        print("    RASTER/EMPTY: content is an embedded OLE/raster ezdxf cannot render.")
        print("    EMPTY DRAW AREA: title block inked but drawing zone blank -> the")
        print("        content was probably orphaned or dropped. Check xclip/bucketing.")
        print("    THIN BAND: an entity's extents leaked past the frame and squeezed")
        print("        the sheet. Should not happen with cropping on; check --no-crop.")
    if N_OUT - extra_pages == N_IN and not bad:
        print("all sheets accounted for and within the ink band.")
    TIMING.mark("self-check")
    TIMING.report()


if __name__ == "__main__":
    main()
