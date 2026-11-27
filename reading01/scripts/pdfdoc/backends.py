"""Text-layer extraction backends.

Every backend returns the same page structure so the layout code never needs
to know where the words came from:

page = {
  "page": 1-based page number, "width": pt, "height": pt,
  "lines": [ {"words": [word, ...], "dir": "h"|"v"} ],
  "images": [[x0, y0, x1, y1], ...],      # raster image boxes (if known)
  "rules":  [[x0, y0, x1, y1], ...],      # thin horizontal rules (table lines)
  "graphics": [[x0, y0, x1, y1], ...],    # clustered vector drawing regions
  "source": backend name, "notes": [...]
}
word = {"t": text, "x0", "y0", "x1", "y1", "size", "font", "bold", "italic",
        "pieces": [[text, role], ...] | None   # role: "", "sup", "sub"
        "conf": OCR confidence 0-100 | None}

Coordinates are PDF points with the origin at the top-left corner of the page.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
    "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
}
_CID_RE = re.compile(r"\(cid:\d+\)")


class PDFOpenError(Exception):
    """Raised when a PDF cannot be opened (encrypted, damaged, not a PDF)."""

    def __init__(self, kind, message):
        Exception.__init__(self, message)
        self.kind = kind  # "encrypted" | "damaged" | "missing" | "unsupported"


# --------------------------------------------------------------------------
# tool discovery
# --------------------------------------------------------------------------

def _import_pymupdf():
    try:
        import pymupdf as fitz  # type: ignore
        return fitz
    except Exception:
        pass
    try:
        import fitz  # type: ignore
        if hasattr(fitz, "open") and hasattr(fitz, "Document"):
            return fitz
    except Exception:
        pass
    return None


def _import(name):
    try:
        return __import__(name)
    except Exception:
        return None


def _which(cmd):
    return shutil.which(cmd)


_TOOLS_CACHE = {}


def detect_tools():
    """Return a dict describing which optional engines are available."""
    if _TOOLS_CACHE:
        return dict(_TOOLS_CACHE)
    t = {}
    fitz = _import_pymupdf()
    t["pymupdf"] = getattr(fitz, "VersionBind", "yes") if fitz else None
    pl = _import("pdfplumber")
    t["pdfplumber"] = getattr(pl, "__version__", "yes") if pl else None
    pp = _import("pypdf")
    t["pypdf"] = getattr(pp, "__version__", "yes") if pp else None
    pil = _import("PIL")
    t["pillow"] = getattr(pil, "__version__", "yes") if pil else None
    np_ = _import("numpy")
    t["numpy"] = getattr(np_, "__version__", "yes") if np_ else None
    for cmd in ("pdftotext", "pdftoppm", "pdfinfo", "tesseract", "mutool"):
        t[cmd] = _which(cmd)
    langs = []
    if t["tesseract"]:
        try:
            out = subprocess.run([t["tesseract"], "--list-langs"], capture_output=True,
                                 text=True, timeout=20)
            langs = [x.strip() for x in (out.stdout + out.stderr).splitlines()
                     if x.strip() and " " not in x.strip() and not x.startswith("List")]
        except Exception:
            langs = []
    t["tesseract_langs"] = langs
    _TOOLS_CACHE.update(t)
    return dict(t)


def choose_backend(preferred="auto"):
    t = detect_tools()
    order = ["pymupdf", "pdfplumber", "poppler"]
    if preferred and preferred != "auto":
        order = [preferred] + [o for o in order if o != preferred]
    for name in order:
        if name == "pymupdf" and t.get("pymupdf"):
            return name
        if name == "pdfplumber" and t.get("pdfplumber"):
            return name
        if name == "poppler" and t.get("pdftotext"):
            return name
    if t.get("pypdf"):
        return "pypdf"
    return None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def normalize_text(s, stats=None):
    """Expand ligatures and drop zero-width/soft-hyphen characters."""
    out = []
    for ch in s:
        if ch in LIGATURES:
            out.append(LIGATURES[ch])
            if stats is not None:
                stats["ligatures"] = stats.get("ligatures", 0) + 1
        elif ch in ("​", "‌", "‍", "﻿", "­"):
            if stats is not None:
                stats["invisible_chars"] = stats.get("invisible_chars", 0) + 1
        else:
            out.append(ch)
    return "".join(out)


def bad_char_count(s):
    n = len(_CID_RE.findall(s))
    for ch in s:
        o = ord(ch)
        if ch == "�" or 0xE000 <= o <= 0xF8FF or (o < 32 and ch not in "\t\n\r"):
            n += 1
    return n


def _mk_word(t, x0, y0, x1, y1, size, font="", bold=False, italic=False, pieces=None, conf=None):
    return {"t": t, "x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1),
            "size": float(size), "font": font or "", "bold": bool(bold), "italic": bool(italic),
            "pieces": pieces, "conf": conf}


def _is_bold_font(name):
    n = (name or "").lower()
    return any(k in n for k in ("bold", "black", "heavy", "semibold", "demi", ".b", "-b", "cmbx", "bx1", "bx9"))


def _is_italic_font(name):
    n = (name or "").lower()
    return any(k in n for k in ("italic", "oblique", "cmti", "cmmi", "-it", ".it"))


def group_words_into_lines(words, ytol=0.45):
    """Cluster words into horizontal lines by vertical overlap (generic)."""
    ws = sorted(words, key=lambda w: ((w["y0"] + w["y1"]) / 2.0, w["x0"]))
    lines = []
    for w in ws:
        h = max(w["y1"] - w["y0"], 0.1)
        cy = (w["y0"] + w["y1"]) / 2.0
        best = None
        for ln in lines[-6:]:
            lh = max(ln["y1"] - ln["y0"], 0.1)
            core0 = ln["y0"] + 0.15 * lh
            core1 = ln["y1"] - 0.15 * lh
            ov = min(w["y1"], core1) - max(w["y0"], core0)
            if ov > ytol * min(h, lh) or (core0 <= cy <= core1):
                best = ln
                break
        if best is None:
            lines.append({"words": [w], "y0": w["y0"], "y1": w["y1"]})
        else:
            best["words"].append(w)
            best["y0"] = min(best["y0"], w["y0"])
            best["y1"] = max(best["y1"], w["y1"])
    out = []
    for ln in lines:
        ln["words"].sort(key=lambda w: w["x0"])
        out.extend(_split_line_on_overlap(ln["words"]))
    return out


def _split_line_on_overlap(words):
    """A cluster may contain words from two text rows when they overlap in x;
    split into separate lines in that case."""
    if len(words) < 2:
        return [{"words": words, "dir": "h"}]
    rows = []
    for w in words:
        placed = False
        for r in rows:
            last = r[-1]
            if w["x0"] >= last["x1"] - 0.5:
                r.append(w)
                placed = True
                break
        if not placed:
            rows.append([w])
    return [{"words": r, "dir": "h"} for r in rows]


def assign_roles(words):
    """Mark superscript / subscript words relative to the line's dominant size."""
    if len(words) < 2:
        return
    sizes = {}
    for w in words:
        k = round(w["size"], 1)
        sizes[k] = sizes.get(k, 0) + len(w["t"])
    tot = float(sum(sizes.values()))
    big = [k for k, v in sizes.items() if v >= max(3, 0.25 * tot)]
    dom = max(big) if big else max(sizes.items(), key=lambda kv: kv[1])[0]
    main = [w for w in words if abs(w["size"] - dom) < 0.6]
    if not main:
        return
    base_y1 = sorted(w["y1"] for w in main)[len(main) // 2]
    base_y0 = sorted(w["y0"] for w in main)[len(main) // 2]
    for w in words:
        if w.get("pieces"):
            continue
        if w["size"] < dom * 0.86:
            if w["y1"] < base_y1 - 0.18 * dom:
                w["pieces"] = [[w["t"], "sup"]]
            elif w["y0"] > base_y0 + 0.18 * dom:
                w["pieces"] = [[w["t"], "sub"]]


# --------------------------------------------------------------------------
# PyMuPDF backend (char level: fonts, sizes, baselines, images, drawings)
# --------------------------------------------------------------------------

def _open_pymupdf_doc(path):
    fitz = _import_pymupdf()
    try:
        doc = fitz.open(path)
    except Exception as e:  # noqa
        raise PDFOpenError("damaged", "PyMuPDF cannot open file: %s" % e)
    if getattr(doc, "needs_pass", False):
        if not doc.authenticate(""):
            raise PDFOpenError("encrypted", "PDF is password protected (user password required)")
    return fitz, doc


def page_count(path):
    fitz = _import_pymupdf()
    if fitz:
        try:
            _, doc = _open_pymupdf_doc(path)
            n = doc.page_count
            doc.close()
            return n
        except PDFOpenError:
            raise
        except Exception:
            pass
    if _which("pdfinfo"):
        out = subprocess.run(["pdfinfo", path], capture_output=True, text=True, timeout=60)
        err = (out.stderr or "").lower()
        if "incorrect password" in err or "encrypted" in err and out.returncode != 0:
            raise PDFOpenError("encrypted", "PDF is password protected")
        m = re.search(r"^Pages:\s+(\d+)", out.stdout, re.M)
        if m:
            return int(m.group(1))
        if out.returncode != 0:
            raise PDFOpenError("damaged", "pdfinfo failed: %s" % out.stderr.strip()[:300])
    pl = _import("pdfplumber")
    if pl:
        try:
            with pl.open(path) as pdf:
                return len(pdf.pages)
        except Exception as e:
            raise PDFOpenError("damaged", "pdfplumber cannot open file: %s" % e)
    pp = _import("pypdf")
    if pp:
        try:
            r = pp.PdfReader(path)
            if r.is_encrypted:
                try:
                    r.decrypt("")
                except Exception:
                    raise PDFOpenError("encrypted", "PDF is password protected")
            return len(r.pages)
        except PDFOpenError:
            raise
        except Exception as e:
            raise PDFOpenError("damaged", "pypdf cannot open file: %s" % e)
    raise PDFOpenError("unsupported", "no PDF engine available (need PyMuPDF, pdfplumber, poppler or pypdf)")


def extract_pymupdf(path, pages, stats):
    fitz, doc = _open_pymupdf_doc(path)
    flags = 0
    for f in ("TEXT_PRESERVE_LIGATURES", "TEXT_PRESERVE_WHITESPACE", "TEXT_MEDIABOX_CLIP"):
        flags |= getattr(fitz, f, 0)
    out = []
    for pno in pages:
        page = doc[pno - 1]
        W, H = float(page.rect.width), float(page.rect.height)
        pg = {"page": pno, "width": W, "height": H, "lines": [], "images": [],
              "rules": [], "graphics": [], "source": "pymupdf", "notes": []}
        try:
            raw = page.get_text("rawdict", flags=flags)
        except Exception as e:
            pg["notes"].append("rawdict failed: %s" % e)
            out.append(pg)
            continue
        for b in raw.get("blocks", []):
            if b.get("type", 0) != 0:
                continue
            for l in b.get("lines", []):
                d = l.get("dir", (1, 0))
                horiz = abs(d[0]) > 0.98
                chars = []
                for s in l.get("spans", []):
                    size = float(s.get("size", 0) or 0)
                    font = s.get("font", "")
                    fl = int(s.get("flags", 0))
                    bold = bool(fl & 16) or _is_bold_font(font)
                    ital = bool(fl & 2) or _is_italic_font(font)
                    for c in s.get("chars", []):
                        ch = c.get("c", "")
                        if not ch:
                            continue
                        bb = c.get("bbox")
                        org = c.get("origin", (bb[0], bb[3]))
                        chars.append((ch, bb, size, font, bold, ital, float(org[1])))
                if not chars:
                    continue
                words = _chars_to_words(chars, stats)
                if not words:
                    continue
                pg["lines"].append({"words": words, "dir": "h" if horiz else "v"})
        # images
        try:
            for info in page.get_image_info():
                bb = info.get("bbox")
                if bb and (bb[2] - bb[0]) > 4 and (bb[3] - bb[1]) > 4:
                    pg["images"].append([float(v) for v in bb])
        except Exception:
            pass
        # drawings: thin horizontal rules and clustered graphic regions
        try:
            drawings = page.get_drawings()
            if len(drawings) < 20000:
                for dr in drawings:
                    r = dr.get("rect")
                    if r is None:
                        continue
                    w_, h_ = r.x1 - r.x0, r.y1 - r.y0
                    if h_ <= 1.6 and w_ >= 15:
                        pg["rules"].append([r.x0, r.y0, r.x1, r.y1])
                if hasattr(page, "cluster_drawings") and len(drawings) < 6000:
                    try:
                        for r in page.cluster_drawings(drawings=drawings):
                            if (r.x1 - r.x0) > 20 and (r.y1 - r.y0) > 20:
                                pg["graphics"].append([r.x0, r.y0, r.x1, r.y1])
                    except Exception:
                        pass
            else:
                pg["notes"].append("too many vector drawings (%d); graphics analysis skipped" % len(drawings))
        except Exception:
            pass
        out.append(pg)
    doc.close()
    return out


def _chars_to_words(chars, stats):
    """chars: (c, bbox, size, font, bold, italic, baseline_y) in line order."""
    sizes = {}
    for c in chars:
        if c[0].strip():
            k = round(c[2], 1)
            sizes[k] = sizes.get(k, 0) + 1
    if not sizes:
        return []
    tot = float(sum(sizes.values()))
    big = [k for k, v in sizes.items() if v >= max(3, 0.25 * tot)]
    dom = max(big) if big else max(sizes.items(), key=lambda kv: kv[1])[0]
    bl = sorted(c[6] for c in chars if abs(c[2] - dom) < 0.6 and c[0].strip())
    base = bl[len(bl) // 2] if bl else chars[0][6]

    def role(c):
        if c[2] < dom * 0.86:
            if c[6] < base - 0.15 * dom:
                return "sup"
            if c[6] > base + 0.08 * dom:
                return "sub"
        return ""

    words = []
    cur = []

    def flush():
        if not cur:
            return
        text = normalize_text("".join(c[0] for c in cur), stats)
        if not text.strip():
            cur[:] = []
            return
        x0 = min(c[1][0] for c in cur)
        y0 = min(c[1][1] for c in cur)
        x1 = max(c[1][2] for c in cur)
        y1 = max(c[1][3] for c in cur)
        # piece runs with roles
        pieces = []
        for c in cur:
            r = role(c)
            t = normalize_text(c[0])
            if pieces and pieces[-1][1] == r:
                pieces[-1][0] += t
            else:
                pieces.append([t, r])
        has_role = any(p[1] for p in pieces)
        main = [c for c in cur if role(c) == ""] or cur
        size = max(set(round(c[2], 1) for c in main), key=lambda s: sum(1 for c in main if round(c[2], 1) == s))
        bold = sum(1 for c in main if c[4]) > len(main) / 2.0
        ital = sum(1 for c in main if c[5]) > len(main) / 2.0
        font = main[0][3]
        words.append(_mk_word(text, x0, y0, x1, y1, size, font, bold, ital,
                              pieces if has_role else None))
        cur[:] = []

    prev = None
    for c in chars:
        ch = c[0]
        if ch.isspace():
            flush()
            prev = None
            continue
        if prev is not None:
            gap = c[1][0] - prev[1][2]
            if gap > 0.28 * max(prev[2], c[2]) or gap < -2.0 * max(prev[2], 1):
                flush()
        cur.append(c)
        prev = c
    flush()
    return words


# --------------------------------------------------------------------------
# pdfplumber backend
# --------------------------------------------------------------------------

def extract_pdfplumber(path, pages, stats):
    import pdfplumber  # type: ignore
    out = []
    try:
        pdf = pdfplumber.open(path)
    except Exception as e:
        msg = str(e)
        kind = "encrypted" if "password" in msg.lower() or "PDFPassword" in type(e).__name__ else "damaged"
        raise PDFOpenError(kind, "pdfplumber cannot open file: %s" % msg)
    with pdf:
        for pno in pages:
            p = pdf.pages[pno - 1]
            W, H = float(p.width), float(p.height)
            pg = {"page": pno, "width": W, "height": H, "lines": [], "images": [],
                  "rules": [], "graphics": [], "source": "pdfplumber", "notes": []}
            try:
                ws = p.extract_words(keep_blank_chars=False, use_text_flow=False,
                                     x_tolerance=1.5, y_tolerance=2,
                                     extra_attrs=["size", "fontname", "upright"])
            except Exception as e:
                pg["notes"].append("extract_words failed: %s" % e)
                ws = []
            hw, vw = [], []
            for w in ws:
                t = normalize_text(w["text"], stats)
                if not t.strip():
                    continue
                fn = w.get("fontname", "")
                word = _mk_word(t, w["x0"], w["top"], w["x1"], w["bottom"], w.get("size", 10),
                                fn, _is_bold_font(fn), _is_italic_font(fn))
                (hw if w.get("upright", True) else vw).append(word)
            lines = group_words_into_lines(hw)
            for ln in lines:
                assign_roles(ln["words"])
            pg["lines"] = lines
            for ln in group_words_into_lines(vw):
                ln["dir"] = "v"
                pg["lines"].append(ln)
            for im in p.images:
                pg["images"].append([im["x0"], im["top"], im["x1"], im["bottom"]])
            for ob in list(p.lines) + list(p.rects):
                w_ = ob["x1"] - ob["x0"]
                h_ = ob["bottom"] - ob["top"]
                if h_ <= 1.6 and w_ >= 15:
                    pg["rules"].append([ob["x0"], ob["top"], ob["x1"], ob["bottom"]])
            out.append(pg)
    return out


# --------------------------------------------------------------------------
# poppler backend (pdftotext -bbox-layout): word geometry, no font names
# --------------------------------------------------------------------------

_HEIGHT_TO_SIZE = 1.0  # poppler word box height ~= nominal font size (calibrated on pdfTeX output)
_OCR_INK_TO_EM = 0.9  # tesseract line ink height (ascender..descender) as a share of the font size


def extract_poppler(path, pages, stats):
    out = []
    first, last = min(pages), max(pages)
    cmd = ["pdftotext", "-bbox-layout", "-f", str(first), "-l", str(last), path, "-"]
    res = subprocess.run(cmd, capture_output=True, timeout=300)
    err = res.stderr.decode("utf-8", "replace")
    if res.returncode != 0:
        if "password" in err.lower():
            raise PDFOpenError("encrypted", "PDF is password protected: " + err.strip()[:200])
        raise PDFOpenError("damaged", "pdftotext failed: " + err.strip()[:300])
    xml = res.stdout.decode("utf-8", "replace")
    xml = re.sub(r"<!DOCTYPE[^>]*>", "", xml, count=1)
    xml = xml.replace('xmlns="http://www.w3.org/1999/xhtml"', "")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        xml = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xml)
        root = ET.fromstring(xml)
    want = set(pages)
    for i, pel in enumerate(root.iter("page")):
        pno = first + i
        if pno not in want:
            continue
        W, H = float(pel.get("width")), float(pel.get("height"))
        pg = {"page": pno, "width": W, "height": H, "lines": [], "images": [],
              "rules": [], "graphics": [], "source": "poppler", "notes": []}
        for lel in pel.iter("line"):
            words = []
            for wel in lel.iter("word"):
                t = normalize_text(wel.text or "", stats)
                if not t.strip():
                    continue
                x0, y0 = float(wel.get("xMin")), float(wel.get("yMin"))
                x1, y1 = float(wel.get("xMax")), float(wel.get("yMax"))
                words.append(_mk_word(t, x0, y0, x1, y1, (y1 - y0) * _HEIGHT_TO_SIZE))
            if not words:
                continue
            words.sort(key=lambda w: w["x0"])
            assign_roles(words)
            lx0, lx1 = float(lel.get("xMin")), float(lel.get("xMax"))
            ly0, ly1 = float(lel.get("yMin")), float(lel.get("yMax"))
            nch = len("".join(w["t"] for w in words))
            tall = sum(1 for w in words if len(w["t"]) >= 2 and (w["y1"] - w["y0"]) > 1.5 * (w["x1"] - w["x0"]))
            vertical = ((ly1 - ly0) > 3 * (lx1 - lx0) and nch > 3) or tall >= max(1, len(words) // 2)
            if vertical:
                for w in words:  # rotated text: size = glyph width, no sup/sub
                    w["size"] = max(4.0, min(w["x1"] - w["x0"], 30.0))
                    w["pieces"] = None
            pg["lines"].append({"words": words, "dir": "v" if vertical else "h"})
        out.append(pg)
    return out


# --------------------------------------------------------------------------
# pypdf backend (last resort: plain text, no geometry)
# --------------------------------------------------------------------------

def extract_pypdf(path, pages, stats):
    import pypdf  # type: ignore
    r = pypdf.PdfReader(path)
    if r.is_encrypted:
        try:
            r.decrypt("")
        except Exception:
            raise PDFOpenError("encrypted", "PDF is password protected")
    out = []
    for pno in pages:
        p = r.pages[pno - 1]
        W, H = float(p.mediabox.width), float(p.mediabox.height)
        pg = {"page": pno, "width": W, "height": H, "lines": [], "images": [],
              "rules": [], "graphics": [], "source": "pypdf",
              "notes": ["pypdf fallback: no word geometry; reading order = content stream order"]}
        text = normalize_text(p.extract_text() or "", stats)
        y = 10.0
        for raw in text.splitlines():
            if not raw.strip():
                continue
            words = []
            x = 10.0
            for tok in raw.split():
                wdt = 5.0 * len(tok)
                words.append(_mk_word(tok, x, y, x + wdt, y + 10, 10))
                x += wdt + 3
            pg["lines"].append({"words": words, "dir": "h"})
            y += 12.0
        out.append(pg)
    return out


# --------------------------------------------------------------------------
# rendering (for OCR and previews)
# --------------------------------------------------------------------------

_DOC_CACHE = {}


def open_cached(path):
    """A PyMuPDF document kept open for repeated rendering (crops, previews, OCR); None without PyMuPDF."""
    fitz = _import_pymupdf()
    if not fitz:
        return None
    key = os.path.abspath(path)
    d = _DOC_CACHE.get(key)
    if d is None:
        try:
            d = fitz.open(path)
            if getattr(d, "needs_pass", False):
                d.authenticate("")
        except Exception:
            return None
        _DOC_CACHE[key] = d
    return d


def close_cached(path=None):
    for key in ([os.path.abspath(path)] if path else list(_DOC_CACHE)):
        d = _DOC_CACHE.pop(key, None)
        if d is not None:
            try:
                d.close()
            except Exception:
                pass


def render_clip_png(path, pno, bbox, dpi, out_png, pad=4.0):
    """Render a page region (PDF points, top-left origin) to PNG. Returns True on success.
    PyMuPDF: the cached document handle (no re-open per crop). Otherwise pdftoppm renders
    just the region (about 0.07 s per crop; a full page costs ~10x more)."""
    x0, y0, x1, y1 = bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad
    d = open_cached(path)
    if d is not None:
        try:
            fitz = _import_pymupdf()
            pg = d[pno - 1]
            clip = fitz.Rect(max(0, x0), max(0, y0), min(pg.rect.width, x1), min(pg.rect.height, y1))
            pg.get_pixmap(dpi=dpi, clip=clip).save(out_png)
            return True
        except Exception:
            pass
    if _which("pdftoppm"):
        s = dpi / 72.0
        base = out_png[:-4]
        res = subprocess.run(["pdftoppm", "-f", str(pno), "-l", str(pno), "-r", str(dpi), "-png", "-singlefile",
                              "-x", str(int(max(0, x0) * s)), "-y", str(int(max(0, y0) * s)),
                              "-W", str(int((x1 - x0) * s)), "-H", str(int((y1 - y0) * s)), path, base],
                             capture_output=True, timeout=120)
        return res.returncode == 0 and os.path.exists(out_png)
    return False


def render_page_png(path, pno, dpi, out_png, use_fitz=True):
    """Render one page to PNG. Returns True on success."""
    d = open_cached(path) if use_fitz else None
    if d is not None:
        try:
            pix = d[pno - 1].get_pixmap(dpi=dpi)
            pix.save(out_png)
            return True
        except Exception:
            pass
    if _which("pdftoppm"):
        base = out_png[:-4] if out_png.endswith(".png") else out_png
        res = subprocess.run(["pdftoppm", "-f", str(pno), "-l", str(pno), "-r", str(dpi),
                              "-png", "-singlefile", path, base], capture_output=True, timeout=180)
        return res.returncode == 0 and os.path.exists(base + ".png")
    if _which("mutool"):
        res = subprocess.run(["mutool", "draw", "-q", "-r", str(dpi), "-o", out_png, path, str(pno)],
                             capture_output=True, timeout=180)
        return res.returncode == 0 and os.path.exists(out_png)
    return False


# --------------------------------------------------------------------------
# OCR (tesseract) with preprocessing and post-checks
# --------------------------------------------------------------------------

def _preprocess_for_ocr(png_in, png_out):
    """Grayscale + autocontrast + Otsu binarisation (if numpy) + light denoise."""
    try:
        from PIL import Image, ImageFilter, ImageOps  # type: ignore
    except Exception:
        return png_in, ["pillow missing: no preprocessing"]
    notes = []
    im = Image.open(png_in).convert("L")
    if min(im.size) < 1200:  # low-resolution source: upscale
        f = 1200.0 / min(im.size)
        im = im.resize((int(im.size[0] * f), int(im.size[1] * f)), Image.LANCZOS)
        notes.append("upscaled x%.2f" % f)
    im = ImageOps.autocontrast(im, cutoff=1)
    im = im.filter(ImageFilter.MedianFilter(3))
    try:
        import numpy as np  # type: ignore
        a = np.asarray(im, dtype=np.uint8)
        hist = np.bincount(a.ravel(), minlength=256).astype(float)
        total = a.size
        sum_all = (np.arange(256) * hist).sum()
        wb = sb = 0.0
        best_t, best_v = 127, -1.0
        for t in range(256):
            wb += hist[t]
            if wb == 0:
                continue
            wf = total - wb
            if wf == 0:
                break
            sb += t * hist[t]
            mb, mf = sb / wb, (sum_all - sb) / wf
            v = wb * wf * (mb - mf) ** 2
            if v > best_v:
                best_v, best_t = v, t
        im = Image.fromarray(((a > best_t) * 255).astype(np.uint8))
        notes.append("otsu threshold %d" % best_t)
    except Exception:
        notes.append("numpy missing: no binarisation")
    im.save(png_out)
    return png_out, notes


def _tesseract_tsv(img, lang, psm=3, timeout=120):  # noqa: D401
    cmd = ["tesseract", img, "stdout", "-l", lang, "--psm", str(psm), "tsv"]
    res = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.decode("utf-8", "replace")[:300])
    return res.stdout.decode("utf-8", "replace")


_DIGITISH = re.compile(r"^[0-9OoIl|.,%+\-−×()]+$")


def _ocr_fix_token(t):
    """Conservative OCR correction inside numeric tokens only (O->0, l/I/|->1)."""
    if not _DIGITISH.match(t) or sum(ch.isdigit() for ch in t) < max(1, len(t) // 2):
        return t
    return t.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"}))


_CJK_CHAR = re.compile(r"[　-〿㐀-䶿一-鿿豈-﫿＀-￯]")
_HALF_TO_FULL = {",": "，", ":": "：", ";": "；", "(": "（", ")": "）", "?": "？", "!": "！"}


def _ocr_cjk_punct(ws, stats):
    """Tesseract's chi_sim model emits ASCII punctuation (",", ":", "(") inside Chinese text.
    Restore the full-width form when the punctuation touches a CJK character in the same line."""
    flat = "".join(w["t"] for w in ws)
    new = list(flat)
    for k, ch in enumerate(flat):
        if ch not in _HALF_TO_FULL:
            continue
        prev = flat[k - 1] if k > 0 else ""
        nxt = flat[k + 1] if k + 1 < len(flat) else ""
        if _CJK_CHAR.match(prev) or _CJK_CHAR.match(nxt):
            new[k] = _HALF_TO_FULL[ch]
    if new == list(flat):
        return
    n = 0
    for i, w in enumerate(ws):
        t = "".join(new[n:n + len(w["t"])])
        if t != w["t"]:
            stats.setdefault("ocr_corrections", []).append([w["t"], t])
            w["t"] = t
        n += len(w["t"])


def ocr_image_to_lines(img_path, scale, lang, stats, psm=3):
    """Run tesseract on an image; return (lines, mean_conf, n_words, low_conf_words)."""
    tsv = _tesseract_tsv(img_path, lang, psm=psm)
    rows = tsv.splitlines()
    groups = {}
    confs = []
    low = []
    for r in rows[1:]:
        f = r.split("\t")
        if len(f) < 12 or f[0] != "5":
            continue
        txt = f[11]
        if not txt.strip():
            continue
        try:
            conf = float(f[10])
        except ValueError:
            conf = -1.0
        left, top, w, h = (float(f[6]), float(f[7]), float(f[8]), float(f[9]))
        fixed = _ocr_fix_token(txt)
        if fixed != txt:
            stats.setdefault("ocr_corrections", []).append([txt, fixed])
        key = (int(f[2]), int(f[3]), int(f[4]))
        word = _mk_word(normalize_text(fixed, stats), left * scale, top * scale,
                        (left + w) * scale, (top + h) * scale, h * scale * _HEIGHT_TO_SIZE,
                        conf=conf)
        groups.setdefault(key, []).append(word)
        if conf >= 0:
            confs.append(conf)
            if conf < 60:
                low.append(word)
    # Font size from OCR boxes: a single word's box only covers its own ink, so x-height-only words
    # ("sure", "on") look smaller than words with ascenders/descenders and whole body lines were
    # mistaken for headings / footnotes.
    #  - Latin line: ink extent of the whole line (ascender top .. descender bottom ~= 0.9 em);
    #  - CJK line: median word height (ideographs all fill ~0.9 em; chi_sim sometimes returns a
    #    bloated box, so max/union would overshoot);
    #  - then one size per tesseract paragraph (its median). Latin lines always take it (a line
    #    without descenders is ~20% shorter and must not split the paragraph); a CJK line only
    #    when within 12% of it, because chi_sim often puts a footnote into the body paragraph.
    def _is_cjk_line(ws):
        txt = "".join(w["t"] for w in ws)
        return len(_CJK_CHAR.findall(txt)) >= 0.5 * max(1, len(txt))

    def _line_h(ws):
        if _is_cjk_line(ws):
            hs = sorted(w["y1"] - w["y0"] for w in ws)
            return hs[len(hs) // 2]
        return max(w["y1"] for w in ws) - min(w["y0"] for w in ws)
    line_h = {key: _line_h(ws) for key, ws in groups.items()}
    cjk_line = {key: _is_cjk_line(ws) for key, ws in groups.items()}
    par_h = {}
    for key, h in line_h.items():
        par_h.setdefault(key[:2], []).append(h)
    par_med = {k: sorted(v)[len(v) // 2] for k, v in par_h.items()}
    lines = []
    for key in sorted(groups):
        ws = sorted(groups[key], key=lambda w: w["x0"])
        h, pm = line_h[key], par_med[key[:2]]
        snap = not cjk_line[key] or (pm and abs(h - pm) <= 0.12 * pm)
        size = (pm if snap else h) / _OCR_INK_TO_EM
        for w in ws:
            w["size"] = size
        if any(k in lang for k in ("chi", "jpn", "kor")):
            _ocr_cjk_punct(ws, stats)
        lines.append({"words": ws, "dir": "h"})
    mean = sum(confs) / len(confs) if confs else 0.0
    return lines, mean, len(confs), low


def ocr_pages(path, pages, stats, workdir, lang="auto", dpi=300, budget_s=420, is_image=False):
    """OCR the given pages. Returns (page dicts, report)."""
    t = detect_tools()
    report = {"engine": "tesseract", "available": bool(t.get("tesseract")), "pages": [],
              "lang": None, "skipped_pages": [], "notes": []}
    if not t.get("tesseract"):
        report["notes"].append("tesseract not found on PATH; OCR not possible")
        report["skipped_pages"] = list(pages)
        return [], report
    langs = t.get("tesseract_langs") or []
    if lang == "auto":
        cand = ["eng"] if "eng" in langs or not langs else [langs[0]]
        if "chi_sim" in langs:
            cand.append("chi_sim+eng")
    else:
        cand = [lang]
    os.makedirs(workdir, exist_ok=True)
    start = time.time()
    chosen = None
    langs_used = []
    out = []
    for pno in pages:
        if time.time() - start > budget_s:
            report["skipped_pages"].append(pno)
            continue
        if is_image:
            src = path
            try:
                from PIL import Image  # type: ignore
                with Image.open(path) as im:
                    wpx, hpx = im.size
                    xdpi = (im.info.get("dpi") or (dpi, dpi))[0] or dpi
            except Exception:
                wpx, hpx, xdpi = 2480, 3508, dpi
            scale = 72.0 / float(xdpi)
            W, H = wpx * scale, hpx * scale
        else:
            src = os.path.join(workdir, "ocr_p%03d.png" % pno)
            if not render_page_png(path, pno, dpi, src):
                report["skipped_pages"].append(pno)
                report["notes"].append("page %d: rendering failed" % pno)
                continue
            scale = 72.0 / dpi
            W = H = None
        pre, pnotes = _preprocess_for_ocr(src, os.path.join(workdir, "ocr_p%03d_pre.png" % pno))
        try:
            from PIL import Image  # type: ignore
            with Image.open(pre) as im:
                pw, ph = im.size
            with Image.open(src) as im0:
                sw, sh = im0.size
            pscale = scale * (float(sw) / pw)
            if W is None:
                W, H = sw * scale, sh * scale
        except Exception:
            pscale = scale
            if W is None:
                W, H = 612.0, 792.0
        best = None
        tried = []
        # the language that fitted the previous page is tried first and kept while it fits
        # (mean confidence >= 75); otherwise the other candidates are tried too, so a Chinese
        # paper with an English cover page / abstract is not read with the English model only
        order = ([chosen] + [c for c in cand if c != chosen]) if chosen else cand
        for lg in order:
            tstats = {}  # corrections of a language that is not kept must not reach the change log
            try:
                lines, mean, n, low = ocr_image_to_lines(pre, pscale, lg, tstats)
            except Exception as e:
                report["notes"].append("page %d lang %s failed: %s" % (pno, lg, e))
                continue
            tried.append((lg, round(mean, 1), n))
            score = mean * min(n, 400)
            if best is None or score > best[0]:
                best = (score, lg, lines, mean, n, low, tstats)
            if chosen is not None and lg == chosen and mean >= 75:
                break
            if chosen is None and lg == "eng" and mean >= 80:
                break
        if best is None:
            report["skipped_pages"].append(pno)
            continue
        _, lg, lines, mean, n, low, tstats = best
        for k, v in tstats.items():
            if isinstance(v, list):
                stats.setdefault(k, []).extend(v)
            elif isinstance(v, (int, float)):
                stats[k] = stats.get(k, 0) + v
        chosen = lg
        langs_used.append(lg)
        report["lang"] = lg if len(set(langs_used)) == 1 else "per page: " + ", ".join(sorted(set(langs_used)))
        out.append({"page": pno, "width": W, "height": H, "lines": lines, "images": [[0, 0, W, H]],
                    "rules": [], "graphics": [], "source": "ocr-tesseract:" + lg,
                    "notes": pnotes + (["lang trials: %s" % tried] if len(tried) > 1 else [])})
        report["pages"].append({"page": pno, "lang": lg, "mean_conf": round(mean, 1), "words": n,
                                "low_conf_words": [[w["t"], round(w["conf"], 0),
                                                    [round(w["x0"], 1), round(w["y0"], 1),
                                                     round(w["x1"], 1), round(w["y1"], 1)]]
                                                   for w in low[:60]],
                                "preprocess": pnotes})
    report["elapsed_s"] = round(time.time() - start, 1)
    return out, report
