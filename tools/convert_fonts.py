#!/usr/bin/env python3
"""Convert extracted Cfnt bitmap fonts to scalable TTF.

Each opaque pixel becomes a filled square in the glyph outline, with horizontal
pixel runs merged into single rectangles to keep contour counts low.

Reads parsed/<LEVEL>/fonts/cfntNNN.fnt + cfntNNN.png; writes cfntNNN.ttf.
Deduplicates: only the first occurrence of each filename is converted.
"""

import glob
import os
import sys

try:
    from PIL import Image
except ImportError:
    print("Pillow required: pip install Pillow", file=sys.stderr)
    sys.exit(1)

try:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
except ImportError:
    print("fonttools required: pip install fonttools", file=sys.stderr)
    sys.exit(1)

UPEM = 1024  # units per em

# Standard PostScript glyph names for printable ASCII
_PS_NAMES = {
    0x20: "space", 0x21: "exclam", 0x22: "quotedbl", 0x23: "numbersign",
    0x24: "dollar", 0x25: "percent", 0x26: "ampersand", 0x27: "quotesingle",
    0x28: "parenleft", 0x29: "parenright", 0x2a: "asterisk", 0x2b: "plus",
    0x2c: "comma", 0x2d: "hyphen", 0x2e: "period", 0x2f: "slash",
    0x30: "zero", 0x31: "one", 0x32: "two", 0x33: "three", 0x34: "four",
    0x35: "five", 0x36: "six", 0x37: "seven", 0x38: "eight", 0x39: "nine",
    0x3a: "colon", 0x3b: "semicolon", 0x3c: "less", 0x3d: "equal",
    0x3e: "greater", 0x3f: "question", 0x40: "at",
    0x41: "A", 0x42: "B", 0x43: "C", 0x44: "D", 0x45: "E", 0x46: "F",
    0x47: "G", 0x48: "H", 0x49: "I", 0x4a: "J", 0x4b: "K", 0x4c: "L",
    0x4d: "M", 0x4e: "N", 0x4f: "O", 0x50: "P", 0x51: "Q", 0x52: "R",
    0x53: "S", 0x54: "T", 0x55: "U", 0x56: "V", 0x57: "W", 0x58: "X",
    0x59: "Y", 0x5a: "Z",
    0x5b: "bracketleft", 0x5c: "backslash", 0x5d: "bracketright",
    0x5e: "asciicircum", 0x5f: "underscore", 0x60: "grave",
    0x61: "a", 0x62: "b", 0x63: "c", 0x64: "d", 0x65: "e", 0x66: "f",
    0x67: "g", 0x68: "h", 0x69: "i", 0x6a: "j", 0x6b: "k", 0x6c: "l",
    0x6d: "m", 0x6e: "n", 0x6f: "o", 0x70: "p", 0x71: "q", 0x72: "r",
    0x73: "s", 0x74: "t", 0x75: "u", 0x76: "v", 0x77: "w", 0x78: "x",
    0x79: "y", 0x7a: "z",
    0x7b: "braceleft", 0x7c: "bar", 0x7d: "braceright", 0x7e: "asciitilde",
}


def _ps_name(cid):
    return _PS_NAMES.get(cid, f"uni{cid:04X}")


def _parse_fnt(fnt_path):
    """Parse a BMFont .fnt text file.

    Returns (atlas_path, glyphs) where glyphs is a list of dicts with keys:
    id, x, y, w, h, xoff, yoff, xadv.
    """
    fnt_dir = os.path.dirname(fnt_path)
    atlas_path = None
    glyphs = []

    with open(fnt_path) as f:
        for line in f:
            parts = line.strip().split()
            if not parts:
                continue
            kv = {}
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    kv[k] = v
            if parts[0] == "page":
                atlas_path = os.path.join(fnt_dir, kv["file"].strip('"'))
            elif parts[0] == "char":
                glyphs.append({
                    "id":   int(kv["id"]),
                    "x":    int(kv["x"]),
                    "y":    int(kv["y"]),
                    "w":    int(kv["width"]),
                    "h":    int(kv["height"]),
                    "xoff": int(kv["xoffset"]),
                    "yoff": int(kv["yoffset"]),
                    "xadv": int(kv["xadvance"]),
                })

    # Deduplicate by ID (keep first occurrence — some fonts repeat entries)
    seen_ids = set()
    unique = []
    for g in glyphs:
        if g["id"] not in seen_ids:
            seen_ids.add(g["id"])
            unique.append(g)
    return atlas_path, unique


def _glyph_rows(atlas, g):
    """Return a list-of-lists of booleans (True = opaque) for the glyph bitmap."""
    rows = []
    for row in range(g["h"]):
        cols = [
            atlas.getpixel((g["x"] + col, g["y"] + row))[3] > 0
            for col in range(g["w"])
        ]
        rows.append(cols)
    return rows


def _draw_glyph(pen, rows, g, baseline_px, scale):
    """Draw glyph outline into pen using horizontal run merging.

    Coordinate mapping (line pixel coords → TTF y-up units, baseline at 0):
      y_top = (baseline_px - yoff - row_idx) * scale
      y_bot = (baseline_px - yoff - row_idx - 1) * scale

    Filled rectangles use CCW winding (PostScript convention).
    """
    xoff = g["xoff"]
    yoff = g["yoff"]

    for row_idx, pixels in enumerate(rows):
        y_top = (baseline_px - yoff - row_idx) * scale
        y_bot = y_top - scale

        col = 0
        while col < len(pixels):
            if not pixels[col]:
                col += 1
                continue
            run_start = col
            while col < len(pixels) and pixels[col]:
                col += 1
            x_left  = (xoff + run_start) * scale
            x_right = (xoff + col) * scale
            # CCW rectangle: bottom-left → bottom-right → top-right → top-left
            pen.moveTo((x_left,  y_bot))
            pen.lineTo((x_right, y_bot))
            pen.lineTo((x_right, y_top))
            pen.lineTo((x_left,  y_top))
            pen.closePath()


def _draw_notdef(pen, width_units, ascender_units, scale):
    """Draw a simple hollow rectangle as the .notdef glyph."""
    w = width_units
    h = ascender_units
    s = scale  # border thickness = 1 pixel
    # Outer rectangle (CCW = filled)
    pen.moveTo((0, 0))
    pen.lineTo((w, 0))
    pen.lineTo((w, h))
    pen.lineTo((0, h))
    pen.closePath()
    # Inner rectangle (CW = hole)
    pen.moveTo((s, s))
    pen.lineTo((s, h - s))
    pen.lineTo((w - s, h - s))
    pen.lineTo((w - s, s))
    pen.closePath()


def build_ttf(fnt_path, face_name):
    atlas_path, glyphs = _parse_fnt(fnt_path)
    atlas = Image.open(atlas_path).convert("RGBA")

    printable = [g for g in glyphs if g["id"] > 0x20]

    # Baseline = bottom of most capital letters (A-Z excluding Q which has a tail)
    caps = [g for g in glyphs if 0x41 <= g["id"] <= 0x5a and g["id"] != ord("Q")]
    baseline_px = max(g["yoff"] + g["h"] for g in caps) if caps else 9

    # Ascender = pixels above baseline (topmost glyph top)
    ascender_px  = baseline_px - min(g["yoff"] for g in printable)
    # Descender = pixels below baseline (deepest glyph bottom)
    descender_px = max(g["yoff"] + g["h"] - baseline_px for g in printable)
    descender_px = max(descender_px, 0)

    # Scale: leave 1px breathing room top and bottom
    total_px = ascender_px + descender_px + 2
    scale = UPEM // total_px

    ascender_u  =  ascender_px  * scale
    descender_u = -descender_px * scale

    # Glyph order: .notdef first, then sorted by codepoint
    sorted_glyphs = sorted(glyphs, key=lambda g: g["id"])
    glyph_order = [".notdef"] + [_ps_name(g["id"]) for g in sorted_glyphs]

    cmap = {g["id"]: _ps_name(g["id"]) for g in sorted_glyphs}

    fb = FontBuilder(UPEM, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)

    # Build outlines
    glyphs_dict = {}
    metrics = {}

    # .notdef: hollow box, 5px wide
    notdef_w = 5 * scale
    pen = TTGlyphPen(None)
    _draw_notdef(pen, notdef_w, ascender_u, scale)
    glyphs_dict[".notdef"] = pen.glyph()
    metrics[".notdef"] = (notdef_w, 0)

    for g in sorted_glyphs:
        name = _ps_name(g["id"])
        adv  = g["xadv"] * scale
        lsb  = g["xoff"] * scale if g["w"] > 0 and g["h"] > 0 else 0

        pen = TTGlyphPen(None)
        rows = _glyph_rows(atlas, g)
        _draw_glyph(pen, rows, g, baseline_px, scale)
        glyph_obj = pen.glyph()

        glyphs_dict[name] = glyph_obj
        metrics[name] = (adv, lsb)

    fb.setupGlyf(glyphs_dict)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ascender_u, descent=descender_u)
    fb.setupNameTable({"familyName": face_name, "styleName": "Regular"})
    fb.setupOS2(
        sTypoAscender=ascender_u,
        sTypoDescender=descender_u,
        sTypoLineGap=0,
        usWinAscent=ascender_u,
        usWinDescent=abs(descender_u),
        fsType=0,
    )
    fb.setupPost()

    out_path = os.path.splitext(fnt_path)[0] + ".ttf"
    fb.font.save(out_path)

    return out_path, len(sorted_glyphs), scale, ascender_px, descender_px


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root  = os.path.dirname(script_dir)
    parsed     = os.path.join(repo_root, "parsed")

    to_convert = sorted(glob.glob(os.path.join(parsed, "*", "fonts", "*.fnt")))

    if not to_convert:
        print("No .fnt files found under parsed/*/fonts/", file=sys.stderr)
        sys.exit(1)

    for fnt_path in to_convert:
        stem      = os.path.splitext(os.path.basename(fnt_path))[0]
        face_name = f"FutureCop-{stem}"
        out_path, n_glyphs, scale, asc, desc = build_ttf(fnt_path, face_name)
        rel = os.path.relpath(out_path, repo_root)
        print(f"{rel}  ({n_glyphs} glyphs, {scale}u/px, +{asc}px/-{desc}px)")


if __name__ == "__main__":
    main()
