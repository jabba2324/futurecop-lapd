#!/usr/bin/env python3
"""Extract Cact/Csac actor instances from Future Cop: L.A.P.D. PS1 MIS archives.

Each actor has two sub-chunks (Csac adds a third):
  TCAt — instance data: actor id, class code, world position (16.16 fixed-point XZ),
          and class-specific behaviour parameters (not fully decoded).
  LSRa — resource slot array: links actor to other resources by (type, id) pairs.
          Slots containing 'NULL' are skipped. Cobj links resolve to object filenames;
          Cnet links are recorded as network-entity IDs.
  CASt — (Csac only) additional state block; included as raw hex for future decoding.

Position encoding:
  TCAt body word[2] = X as 16.16 fixed-point (integer part = tile column, fractional
  part = sub-tile offset). word[4] = Z in the same format. word[3] is always 0 (Y is
  ground-level, derived from Ctil heightmap, not stored in the actor record).
  Tile units: 1 unit ≈ one Ctil chunk width (each Ctil spans ±8 world units = 16 units).

Resource references:
  Cobj N  → objects/cobjNNN.obj  (3D mesh, also has a paired .mtl)
  Cbmp N  → textures/cbmpNNN.png (textures used by those objects, resolved via 3DTL)
  Cnet N  → network entity id N  (multiplayer spawn/config; no extracted file)

Output: parsed/<LEVEL>/actors.json  (one file per MIS level)
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import iter_iff_resources, find_game_files


# ── helpers ────────────────────────────────────────────────────────────────

def _sub_chunks(raw):
    """Walk little-endian (tag u32 + total_size u32) sub-chunks; yield (tag_str, body)."""
    pos = 0
    while pos + 8 <= len(raw):
        tag_str = raw[pos:pos+4].decode("ascii", errors="replace")
        size = struct.unpack_from("<I", raw, pos + 4)[0]
        if size < 8 or pos + size > len(raw):
            break
        yield tag_str, raw[pos + 8 : pos + size]
        pos += size


def _decode_lsra(body):
    """Return list of (res_type_str, res_id) for non-NULL slots, skipping actor-id word."""
    slots = []
    i = 4  # skip leading actor-id word
    while i + 8 <= len(body):
        # types are stored little-endian (byte-reversed vs. IFF resource type strings)
        rtype_le = body[i : i + 4]
        rid = struct.unpack_from("<I", body, i + 4)[0]
        rtype_str = rtype_le[::-1].decode("ascii", errors="replace")
        if rtype_str not in ("NULL", "\x00\x00\x00\x00"):
            slots.append((rtype_str, rid))
        i += 8
    return slots


def _build_cobj_textures(data):
    """Return {cobj_res_id: sorted_list_of_cbmp_res_ids} by scanning 3DTL in every Cobj."""
    _TAG_3DTL = 0x3344544C  # b'LTD3' in LE = '3DTL' reversed

    mapping = {}
    for res_type, res_id, raw in iter_iff_resources(data):
        if res_type != "Cobj":
            continue
        bmps = set()
        p = 0
        while p + 8 <= len(raw):
            itag = struct.unpack_from("<I", raw, p)[0]
            isz  = struct.unpack_from("<I", raw, p + 4)[0]
            if isz < 8 or p + isz > len(raw):
                break
            if itag == _TAG_3DTL:
                body = raw[p + 8 : p + isz]
                p2 = 4  # skip leading u32(1)
                while p2 < len(body):
                    op = body[p2] if p2 < len(body) else 0
                    if op == 1:
                        p2 += 4
                    elif op in (2, 3):
                        if p2 + 16 <= len(body):
                            bmp_id = struct.unpack_from("<I", body, p2 + 12)[0]
                            bmps.add(bmp_id + 1)  # 0-indexed → Cbmp resource ID
                        p2 += 16
                    else:
                        break
            p += isz
        mapping[res_id] = sorted(bmps)
    return mapping


def _decode_tcat(body):
    """Return dict of decoded TCAt fields."""
    if len(body) < 20:
        return {}
    actor_id = struct.unpack_from("<I", body, 0)[0]
    cls      = struct.unpack_from("<I", body, 4)[0]
    pos_x    = struct.unpack_from("<I", body, 8)[0] / 65536.0
    pos_z    = struct.unpack_from("<I", body, 16)[0] / 65536.0
    # remaining words kept as raw hex for future decoding
    extra = [hex(struct.unpack_from("<I", body, i)[0])
             for i in range(20, len(body) - 3, 4)]
    return {
        "actor_id": actor_id,
        "class": cls,
        "pos_x": round(pos_x, 4),
        "pos_z": round(pos_z, 4),
        "params": extra,
    }


# ── per-level extraction ────────────────────────────────────────────────────

def extract_actors(mis_path, out_dir):
    with open(mis_path, "rb") as f:
        data = f.read()

    cobj_textures = _build_cobj_textures(data)

    actors = []
    for res_type, res_id, raw in iter_iff_resources(data):
        if res_type not in ("Cact", "Csac"):
            continue

        record = {"id": res_id, "kind": res_type}
        cast_hex = None

        for tag, body in _sub_chunks(raw):
            if tag == "TCAt":
                record.update(_decode_tcat(body))
            elif tag == "LSRa":
                objects  = []
                textures = set()
                cnet_ids = []
                for rtype, rid in _decode_lsra(body):
                    if rtype == "Cobj":
                        obj_name = f"cobj{rid:03d}.obj"
                        if obj_name not in objects:
                            objects.append(obj_name)
                        for cbmp_id in cobj_textures.get(rid, []):
                            textures.add(f"cbmp{cbmp_id:03d}.png")
                    elif rtype == "Cnet":
                        cnet_ids.append(rid)
                record["objects"]  = objects
                record["textures"] = sorted(textures)
                if cnet_ids:
                    record["cnet_ids"] = cnet_ids
            elif tag == "CASt":
                cast_hex = body.hex()

        if cast_hex is not None:
            record["cast_raw"] = cast_hex

        actors.append(record)

    if not actors:
        return 0

    os.makedirs(os.path.join(out_dir, "actors"), exist_ok=True)  # reserved for future per-actor files
    out_path = os.path.join(out_dir, "actors.json")
    with open(out_path, "w") as f:
        json.dump({"level": os.path.splitext(os.path.basename(mis_path))[0],
                   "actors": actors}, f, indent=2)

    return len(actors)


# ── main ───────────────────────────────────────────────────────────────────

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root  = os.path.dirname(script_dir)
    parsed     = os.path.join(repo_root, "parsed")

    mis_files = find_game_files("MIS")

    total = 0
    for mis_path in mis_files:
        level   = os.path.splitext(os.path.basename(mis_path))[0]
        out_dir = os.path.join(parsed, level)
        n = extract_actors(mis_path, out_dir)
        if n:
            print(f"{level}.MIS → {n} actors")
        total += n

    print(f"\nDone — {total} actors extracted.")


if __name__ == "__main__":
    main()
