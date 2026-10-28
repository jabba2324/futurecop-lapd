#!/usr/bin/env python3
"""Extract Cdcs (decal / sprite-quad atlas rectangles) from Future Cop: L.A.P.D. PS1 MIS archives.

WHAT Cdcs IS
  A small table of named sub-rectangles of the level's 256x256 Cbmp texture pages. Each
  entry gives a texture page and a pixel rectangle; the "DCS quad" actor class (class
  97 in TCAt) draws a flat textured quad (decal: road markings, signs, glows...) using
  the entry whose dcs_id matches the actor's. fc-mit DCSResource.cpp / ACT/DCSQuad.cpp
  and ParticleDraw.cpp (uv = rect / 256, texture = Cbmp cbmp_index + 1).

  Verified here: every class-97 actor's dcs_id is present in the level's table, every
  referenced Cbmp exists, and every rectangle an actor uses fits inside 256x256.
  Exceptions, all unused by actors: M2C id 22 and UN id 5 run past y=256 (wrap?), and
  M4A1 has placeholder entries with cbmp_index 0xff and an empty rect (reported with
  placeholder=true, cbmp_id null). Absent in FE and M1A1.

Binary layout (little-endian)
  +0  4cc 'Cdcs' (stored "scdC")
  +4  u32 chunk size (whole payload)
  +8  u32 entry_count
  +12 entries, 8 bytes each:
      +0 u8 x        left edge in texture pixels
      +1 u8 y        top edge
      +2 u8 width
      +3 u8 height
      +4 u8 cbmp_index  0-based: texture = Cbmp resource id cbmp_index + 1
                        (= textures/cbmpNNN.png from extract_visuals.py); 0xff = none
      +5 u8 pad0 (always 0)
      +6 u8 pad1 (always 0)
      +7 u8 dcs_id   id referenced by DCS-quad actors (fc-mit keeps the last match)

  DCS-quad actor (TCAt class 97) type data starts at TCAt body +20:
      +20 u8 bitfield, +21 u8 dcs_id, +22 u16 height_offset (/512), +24 u16 width,
      +26 u16 height (/1024 -> world scale), +28/+30/+32 u16 rotation y/z/x,
      +34 u8 ground_cast, +35..+37 u8 r,g,b (fc-mit ACT/DCSQuad.h)

Output: parsed/<LEVEL>/dcs.json
  {"level", "id", "size", "entry_count", "entries": [{"index", "dcs_id", "x", "y",
   "width", "height", "cbmp_index", "cbmp_id", "placeholder", "cbmp_exists",
   "texture", "texture_exists", "uv", "fits_256",
   "pad0", "pad1", "used_by_actors"}], "actor_refs_missing", "leftover_bytes"}
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import iter_iff_resources, find_game_files

_TAG = b"scdC"
_DCS_QUAD_CLASS = 97


def _sub_chunks(raw):
    """Walk (tag u32 + total_size u32) sub-chunks; yield (tag_str, body)."""
    pos = 0
    while pos + 8 <= len(raw):
        tag = raw[pos:pos + 4].decode("ascii", errors="replace")
        size = struct.unpack_from("<I", raw, pos + 4)[0]
        if size < 8 or pos + size > len(raw):
            break
        yield tag, raw[pos + 8:pos + size]
        pos += size


def _dcs_quad_actors(data):
    """Return [(actor resource id, dcs_id)] for every class-97 actor."""
    out = []
    for rtype, rid, raw in iter_iff_resources(data):
        if rtype not in ("Cact", "Csac"):
            continue
        for tag, body in _sub_chunks(raw):
            if tag == "TCAt" and len(body) > 21 and body[4] == _DCS_QUAD_CLASS:
                out.append((rid, body[21]))
    return out


def _decode_dcs(payload, cbmp_ids, textures_dir):
    if payload[:4] != _TAG or len(payload) < 12:
        return {"error": "bad Cdcs header", "raw": payload.hex()}
    size, count = struct.unpack_from("<II", payload, 4)
    entries = []
    for i in range(count):
        off = 12 + 8 * i
        if off + 8 > len(payload):
            break
        x, y, w, h, bmp, p0, p1, did = struct.unpack_from("<8B", payload, off)
        placeholder = bmp == 0xff
        tex = None if placeholder else f"cbmp{bmp + 1:03d}.png"
        entries.append({
            "index": i, "dcs_id": did, "x": x, "y": y, "width": w, "height": h,
            "cbmp_index": bmp, "cbmp_id": None if placeholder else bmp + 1,
            "placeholder": placeholder,
            "cbmp_exists": (not placeholder) and (bmp + 1) in cbmp_ids,
            "texture": f"textures/{tex}" if tex else None,
            "texture_exists": bool(tex) and os.path.exists(os.path.join(textures_dir, tex)),
            "uv": [round(x / 256, 6), round(y / 256, 6),
                   round((x + w) / 256, 6), round((y + h) / 256, 6)],
            "fits_256": x + w <= 256 and y + h <= 256,
            "pad0": p0, "pad1": p1, "used_by_actors": [],
        })
    return {"size": size, "size_matches_payload": size == len(payload),
            "entry_count": count, "entries": entries,
            "leftover_bytes": len(payload) - 12 - 8 * count}


def extract_dcs(mis_path, out_dir):
    with open(mis_path, "rb") as f:
        data = f.read()
    res = list(iter_iff_resources(data))
    cbmp_ids = {rid for t, rid, _ in res if t == "Cbmp"}
    tables = [(rid, raw) for t, rid, raw in res if t == "Cdcs"]
    if not tables:
        return 0
    out = {"level": os.path.splitext(os.path.basename(mis_path))[0], "cdcs": []}
    for rid, raw in tables:
        body = {"id": rid}
        body.update(_decode_dcs(raw, cbmp_ids, os.path.join(out_dir, "textures")))
        out["cdcs"].append(body)

    missing = []
    for actor_id, did in _dcs_quad_actors(data):
        hit = False
        for body in out["cdcs"]:
            matches = [e for e in body.get("entries", []) if e["dcs_id"] == did]
            if matches:
                matches[-1]["used_by_actors"].append(actor_id)  # fc-mit: last match wins
                hit = True
        if not hit:
            missing.append({"actor": actor_id, "dcs_id": did})
    out["actor_refs_missing"] = missing

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "dcs.json"), "w") as f:
        json.dump(out, f, indent=1)
    return sum(len(b.get("entries", [])) for b in out["cdcs"])


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mis_files = find_game_files("MIS")
    for mis_path in mis_files:
        level = os.path.splitext(os.path.basename(mis_path))[0]
        n = extract_dcs(mis_path, os.path.join(repo_root, "parsed", level))
        print(f"{level}.MIS → {n} Cdcs entries")


if __name__ == "__main__":
    main()
