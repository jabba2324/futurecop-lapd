#!/usr/bin/env python3
"""Extract the front-end-only resources of Future Cop: L.A.P.D. (PS1 FE.MIS).

Resource types handled (all little-endian, payload = concatenated SDAT data):

  Cmdc - MDEC-compressed still image (16 in FE.MIS).
           +0 u16 width, +2 u16 height, +4 BS v2 frame (see lib/bs_decode.py).
           Two sizes: 160x256 halves of full 320x256 screens (title screen EN/DE,
           "demo" title, menu frame) and 128x144 panels (mech portraits, grid
           backgrounds). Halves are consecutive ids: (1,2) (3,4) (10,11) (17,18).
           Same record format as the two halves of an .LSC file.
           Confirmed visually.
           Output: frontend/cmdcNN.png, frontend/screen_cmdcNN_MM.png (stitched pair)

  Ctdt - Raw VRAM upload (1 in FE.MIS, 133 KB).
           +0 u16 version (2), +2 u16 n_rects, +4 u32 link hash (== Ctin hash),
           +8 n_rects x {u16 x, u16 y, u16 w, u16 h} in VRAM halfword units,
           then the rectangles' 16-bit pixels back to back (w*h*2 bytes each).
           FE: (512,0) 512x128 = texture pages 8-15 top half incl. CLUTs, and
           (768,128) 62x15. Sizes add up exactly to the payload size.
           Output: frontend/ctdt_vram.png (1024x512 view of VRAM as 15-bit colour)

  Ctin - Sprite table into the Ctdt VRAM image (1 in FE.MIS).
           +0 u16 version (2), +2 u16 count (83), +4 u32 link hash (== Ctdt hash),
           +8 count x {u16 tpage, u16 u, u16 v, u16 w, u16 h, u16 clut}.
           tpage/clut are standard PS1 GPU words: page x = (tpage & 15) * 64,
           page y = ((tpage >> 4) & 1) * 256, colour mode = (tpage >> 7) & 3
           (0 = 4bpp, 1 = 8bpp, 2 = 15bpp); clut x = (clut & 63) * 16, y = clut >> 6.
           Confirmed visually: weapon icons, the L.A. zone map and its 40-odd zone
           pieces, HUD bars, cursors.
           Output: frontend/sprites/ctinNNN.png + frontend/sprites.json

  Ctrs - Localised UI string table (3 in FE.MIS: id 1 English, 2 German, 3 French).
           +0 u16 version (1), +2 u16 count,
           +4 count x {u16 string_id, u16 offset}, offset relative to byte 4;
           strings are NUL-terminated Latin-1 ('\\n' = line break; '*', '^', '$', '#'
           are button glyph placeholders). Confirmed.
           Output: frontend/strings_<lang>.json  {string_id: text}

  Cmic - Memory-card save icon (1 in FE.MIS, 448 bytes).
           Sub-chunks (tag stored reversed, u32 size incl. 8-byte header):
             '4XIP' (PIX4) x3 : 16x16 4bpp frame, 128 bytes, LOW nibble = left pixel
             '61LP' (PL16)    : 16-colour palette, u16 BGR555 (0x0000 = transparent)
           Matches the PS1 memory-card icon format (16x16, 4bpp, up to 3 frames).
           The nibble order is plausible (low-first gives a smoother image than
           high-first; the frames show the X1-Alpha walker).
           Output: frontend/cmic_f0..2.png, frontend/cmic.gif

Output root: parsed/FE/frontend/
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import iter_iff_resources, find_game_files
from lib.bs_decode import decode_bs_image

try:
    from PIL import Image
except ImportError:
    print("PIL/Pillow required: pip install Pillow", file=sys.stderr)
    sys.exit(1)

_VRAM_W, _VRAM_H = 1024, 512
_LANGS = {1: "en", 2: "de", 3: "fr"}   # by inspection of the strings


def _rgba555(v, transparent_zero=True):
    r = (v & 31) << 3
    g = ((v >> 5) & 31) << 3
    b = ((v >> 10) & 31) << 3
    a = 0 if (transparent_zero and v == 0) else 255
    return (r, g, b, a)


# ── Cmdc ──────────────────────────────────────────────────────────────────

def _decode_cmdc(raw):
    w, h = struct.unpack_from("<HH", raw, 0)
    return decode_bs_image(raw[4:], w, h)


def _stitch_pairs(images):
    """Pair consecutive-id 160-wide halves into full screens. Returns [(id_a, id_b, img)]."""
    out = []
    ids = sorted(images)
    used = set()
    for rid in ids:
        a = images[rid]
        b = images.get(rid + 1)
        if rid in used or b is None or a.width != 160 or b.width != 160 or a.height != b.height:
            continue
        full = Image.new("RGB", (a.width + b.width, a.height))
        full.paste(a, (0, 0))
        full.paste(b, (a.width, 0))
        out.append((rid, rid + 1, full))
        used.update((rid, rid + 1))
    return out


# ── Ctdt / Ctin ───────────────────────────────────────────────────────────

def _parse_ctdt(raw):
    version, n, link = struct.unpack_from("<HHI", raw, 0)
    rects = [struct.unpack_from("<4H", raw, 8 + 8 * i) for i in range(n)]
    vram = [0] * (_VRAM_W * _VRAM_H)
    p = 8 + 8 * n
    for x, y, w, h in rects:
        for j in range(h):
            row = struct.unpack_from("<%dH" % w, raw, p)
            p += 2 * w
            start = (y + j) * _VRAM_W + x
            vram[start:start + w] = row
    info = {"version": version, "link_hash": hex(link),
            "rects": [{"x": x, "y": y, "w": w, "h": h} for x, y, w, h in rects],
            "trailing_bytes": len(raw) - p}
    return vram, info


def _parse_ctin(raw):
    version, n, link = struct.unpack_from("<HHI", raw, 0)
    recs = []
    for i in range(n):
        tpage, u, v, w, h, clut = struct.unpack_from("<6H", raw, 8 + 12 * i)
        recs.append({"index": i, "tpage": tpage, "u": u, "v": v, "w": w, "h": h, "clut": clut,
                     "page_x": (tpage & 15) * 64, "page_y": ((tpage >> 4) & 1) * 256,
                     "colour_mode": (tpage >> 7) & 3, "semi_trans": (tpage >> 5) & 3,
                     "clut_x": (clut & 63) * 16, "clut_y": clut >> 6})
    return {"version": version, "link_hash": hex(link), "count": n}, recs


def _cut_sprite(vram, s):
    w, h = max(s["w"], 1), max(s["h"], 1)
    px, py, mode = s["page_x"], s["page_y"], s["colour_mode"]
    clut_base = s["clut_y"] * _VRAM_W + s["clut_x"]
    data = []
    for yy in range(h):
        row = (py + s["v"] + yy) * _VRAM_W + px
        for xx in range(w):
            u = s["u"] + xx
            if mode == 0:
                idx = (vram[row + u // 4] >> (4 * (u & 3))) & 15
                data.append(_rgba555(vram[clut_base + idx]))
            elif mode == 1:
                idx = (vram[row + u // 2] >> (8 * (u & 1))) & 255
                data.append(_rgba555(vram[clut_base + idx]))
            else:
                data.append(_rgba555(vram[row + u]))
    img = Image.new("RGBA", (w, h))
    img.putdata(data)
    return img


# ── Ctrs ──────────────────────────────────────────────────────────────────

def _parse_ctrs(raw):
    version, n = struct.unpack_from("<HH", raw, 0)
    strings = {}
    for i in range(n):
        sid, off = struct.unpack_from("<HH", raw, 4 + 4 * i)
        start = 4 + off
        end = raw.index(b"\x00", start)
        strings[str(sid)] = raw[start:end].decode("latin-1")
    return version, strings


# ── Cmic ──────────────────────────────────────────────────────────────────

def _parse_cmic(raw):
    frames, palette = [], None
    p = 0
    while p + 8 <= len(raw):
        tag = raw[p:p + 4][::-1].decode("ascii", "replace")
        size = struct.unpack_from("<I", raw, p + 4)[0]
        body = raw[p + 8:p + size]
        if tag == "PIX4":
            frames.append(body)
        elif tag == "PL16":
            palette = struct.unpack_from("<16H", body, 0)
        if size < 8:
            break
        p += size
    imgs = []
    for f in frames:
        px = []
        for byte in f:
            px.append(_rgba555(palette[byte & 15]))
            px.append(_rgba555(palette[byte >> 4]))
        img = Image.new("RGBA", (16, 16))
        img.putdata(px)
        imgs.append(img)
    return imgs, palette


# ── driver ────────────────────────────────────────────────────────────────

def process_mis(path, out_dir):
    with open(path, "rb") as f:
        data = f.read()
    res = {}
    for rtype, rid, raw in iter_iff_resources(data):
        res.setdefault(rtype, {})[rid] = raw
    if not any(t in res for t in ("Cmdc", "Ctdt", "Ctin", "Ctrs", "Cmic")):
        return False
    fe_dir = os.path.join(out_dir, "frontend")
    os.makedirs(fe_dir, exist_ok=True)
    summary = {}

    cmdc_imgs = {}
    cmdc_meta = []
    for rid, raw in sorted(res.get("Cmdc", {}).items()):
        img, info = _decode_cmdc(raw)
        cmdc_imgs[rid] = img
        img.save(os.path.join(fe_dir, "cmdc%02d.png" % rid))
        cmdc_meta.append(dict(info, id=rid, width=img.width, height=img.height,
                              file="cmdc%02d.png" % rid))
    screens = []
    for a, b, full in _stitch_pairs(cmdc_imgs):
        name = "screen_cmdc%02d_%02d.png" % (a, b)
        full.save(os.path.join(fe_dir, name))
        screens.append({"left": a, "right": b, "file": name})
    summary["Cmdc"] = {"images": cmdc_meta, "screens": screens}
    print(f"  Cmdc: {len(cmdc_meta)} images, {len(screens)} stitched screens")

    vram = None
    for rid, raw in sorted(res.get("Ctdt", {}).items()):
        vram, info = _parse_ctdt(raw)
        img = Image.new("RGBA", (_VRAM_W, _VRAM_H))
        img.putdata([_rgba555(v) for v in vram])
        img.save(os.path.join(fe_dir, "ctdt_vram.png"))
        summary["Ctdt"] = dict(info, id=rid, file="ctdt_vram.png")
        print(f"  Ctdt: {len(info['rects'])} VRAM rects")

    for rid, raw in sorted(res.get("Ctin", {}).items()):
        head, sprites = _parse_ctin(raw)
        if vram is not None:
            spr_dir = os.path.join(fe_dir, "sprites")
            os.makedirs(spr_dir, exist_ok=True)
            for s in sprites:
                s["file"] = "sprites/ctin%03d.png" % s["index"]
                _cut_sprite(vram, s).save(os.path.join(fe_dir, s["file"]))
        with open(os.path.join(fe_dir, "sprites.json"), "w") as f:
            json.dump(dict(head, id=rid, sprites=sprites), f, indent=1)
        summary["Ctin"] = dict(head, id=rid, file="sprites.json")
        print(f"  Ctin: {len(sprites)} sprites")

    for rid, raw in sorted(res.get("Ctrs", {}).items()):
        version, strings = _parse_ctrs(raw)
        lang = _LANGS.get(rid, "lang%d" % rid)
        name = "strings_%s.json" % lang
        with open(os.path.join(fe_dir, name), "w", encoding="utf-8") as f:
            json.dump({"id": rid, "language": lang, "version": version, "strings": strings},
                      f, indent=1, ensure_ascii=False)
        summary.setdefault("Ctrs", []).append({"id": rid, "language": lang,
                                               "count": len(strings), "file": name})
        print(f"  Ctrs {rid}: {len(strings)} strings ({lang})")

    for rid, raw in sorted(res.get("Cmic", {}).items()):
        imgs, palette = _parse_cmic(raw)
        for i, im in enumerate(imgs):
            im.save(os.path.join(fe_dir, "cmic_f%d.png" % i))
        if imgs:
            imgs[0].save(os.path.join(fe_dir, "cmic.gif"), save_all=True,
                         append_images=imgs[1:], duration=200, loop=0, disposal=2)
        summary["Cmic"] = {"id": rid, "frames": len(imgs),
                           "palette": ["0x%04x" % c for c in palette]}
        print(f"  Cmic: {len(imgs)} icon frames")

    with open(os.path.join(fe_dir, "frontend.json"), "w") as f:
        json.dump(summary, f, indent=1)
    return True


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    parsed = os.path.join(repo_root, "parsed")
    mis_files = find_game_files("MIS")
    for path in mis_files:
        level = os.path.splitext(os.path.basename(path))[0]
        print(f"\n{level}.MIS")
        if process_mis(path, os.path.join(parsed, level)):
            print(f"  -> parsed/{level}/frontend/")
        else:
            print("  (no front-end resources)")


if __name__ == "__main__":
    main()
