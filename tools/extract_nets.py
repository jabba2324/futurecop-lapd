#!/usr/bin/env python3
"""Extract Cnet navigation networks (AI path node graphs) from Future Cop: L.A.P.D. PS1 MIS archives.

WHAT Cnet IS
  A directed graph of path nodes that pathed actors (vehicles, walkers, pathed turrets)
  follow. Actors reference a net through a 'Cnet' slot in their LSRa block; an actor
  starts on the node whose raw position equals its own TCAt position >> 8 (fc-mit
  NetResource::getNodeIndexFromPosition; this script reports that match per actor).
  Scripts can enable/disable single nodes with RPN opcode 0x3b (see extract_rpns.py).

  Sources: fc-mit NetResource.cpp/.h (node layout, link packing, height bits) and the
  PS1 loader at 0x800511e4 in SLUS_007.39 (height modes, net table at 0x800a2210 in
  load order, which is the index opcode 0x3b uses).

Binary layout (little-endian)
  Header (16 bytes)
    +0  4cc  'tNOD' (stored "NtDO")
    +4  u32  size of the whole payload
    +8  u16  unknown_8   (always 0)
    +10 u16  unknown_10  (always 1; version?) - no PS1 code reads it: the loader
                         0x800511e4 stores payload+12 as the net's runtime base, and
                         the path code only uses base+2 (node_count) and the nodes
    +12 u16  unknown_12  (always 0)
    +14 u16  node_count
  Node (12 bytes) x node_count
    +0  u32  word0   bits 0-1  flags: bit 1 = disabled (op 0x3b and messages 20/21 set
                                and clear it; 0x8004f0f0 / 0x8004f188; path following
                                skips disabled links, 0x8004f330),
                                bit 0 = target node (fc-mit "UNKNOWN" state; output
                                "flag0" and "target_node"): class-26 actors in state 6
                                (message 90 with arg 6) pick the nearest enabled node
                                with this bit as their destination (0x8002b1dc);
                                set on only 4 nodes (M1A1 1, M2C 2, M3A 1)
                     bits 2-11, 12-21, 22-31  three 10-bit links (node index, 0x3ff = none)
    +4  u16  word1   bits 6-15  fourth 10-bit link (0x3ff = none)
                     bits 0-5   unknown (always 0 on disc). The loader clears bits 0-3;
                                at runtime they count actors heading for the node
                                (class 5 decrements it when it leaves, 0x800382b4)
    +6  i16  raw_z   position, 1/256 of an actor tile unit
    +8  i16  raw_x
    +10 u16  height  bits 0-3  height mode, bits 4-15 signed height (raw / 16 node units)
                     mode 0 -> ground raycast level -1 (fc-mit "HIGH")
                     mode 1 -> raycast level -2 (fc-mit "LOW")
                     mode 2/3/4 -> raycast level 0/1/2 (fc-mit calls 2 "NONE", 3 "MIDDLE")
                     mode 5 -> explicit height = signed field (fc-mit: LOW + read_offset)
  Nothing follows the last node (leftover_bytes is reported per net).

Coordinate convention (same as actors.json)
  actors.json pos_x = TCAt word at body+8 / 65536, pos_z = word at body+16 / 65536.
  The loader builds the runtime vector as (raw_x, height, raw_z) next to actor positions
  whose raw values are 256x larger, so:
      pos_x = raw_x / 256        pos_z = raw_z / 256
  i.e. node raw = actor raw >> 8 (verified: every pathed actor sits exactly on a node).
  fc-mit "world units" (and Godot units before generate_scene.gd's offsets) are
  raw / 32 = pos * 8. height_raw = int16(height) >> 4 is in raw node units, so
  height_world (mode 5 only) = height_raw / 32 (fc-mit getHeightOffset; the PS1 loader
  stores ((int16(height) << 4) + 127) >> 8, the same value rounded).

Output: parsed/<LEVEL>/nets.json
  {"level", "coordinate_note", "nets": [{"id", "index", "size", "unknown_8",
   "unknown_10", "unknown_12", "node_count", "leftover_bytes", "bounds",
   "nodes": [{"index", "pos_x", "pos_z", "raw_x", "raw_z", "links", "disabled",
              "flag0", "target_node", "height_mode", "height_mode_name", "height_world",
              "height_raw", "unknown_word1_low6", "word0", "word1"}],
   "edges": [[from, to], ...], "actors": [{"id", "kind", "class", "pos_x", "pos_z",
   "start_node"}]}]}
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import iter_iff_resources, find_game_files

_TAG = b"NtDO"
_NO_LINK = 0x3ff
_HEIGHT_MODES = {0: "raycast_high", 1: "raycast_low", 2: "raycast_level0",
                 3: "raycast_level1", 4: "raycast_level2", 5: "explicit"}


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


def _actor_net_refs(data):
    """Return [(actor dict, [cnet ids])] for every Cact/Csac with a Cnet LSRa slot."""
    out = []
    for rtype, rid, raw in iter_iff_resources(data):
        if rtype not in ("Cact", "Csac"):
            continue
        actor = {"id": rid, "kind": rtype}
        nets = []
        for tag, body in _sub_chunks(raw):
            if tag == "TCAt" and len(body) >= 20:
                actor["class"] = body[4]
                actor["raw_x"] = struct.unpack_from("<i", body, 8)[0]
                actor["raw_z"] = struct.unpack_from("<i", body, 16)[0]
            elif tag == "LSRa":
                for i in range(4, len(body) - 7, 8):
                    if body[i:i + 4][::-1] == b"Cnet":
                        nets.append(struct.unpack_from("<I", body, i + 4)[0])
        if nets and "raw_x" in actor:
            out.append((actor, nets))
    return out


def _decode_node(index, blob, off):
    w0, w1, raw_z, raw_x, hf = struct.unpack_from("<IHhhH", blob, off)
    links = [(w0 >> (2 + 10 * k)) & _NO_LINK for k in range(3)] + [w1 >> 6]
    mode = hf & 0xf
    signed = struct.unpack("<h", struct.pack("<H", hf))[0] >> 4
    return {
        "index": index,
        "pos_x": round(raw_x / 256.0, 5),
        "pos_z": round(raw_z / 256.0, 5),
        "raw_x": raw_x,
        "raw_z": raw_z,
        "links": [ln for ln in links if ln != _NO_LINK],
        "disabled": bool(w0 & 2),
        "flag0": w0 & 1,
        "target_node": bool(w0 & 1),
        "height_mode": mode,
        "height_mode_name": _HEIGHT_MODES.get(mode, f"unknown_{mode}"),
        "height_world": round(signed / 32.0, 5) if mode == 5 else None,
        "height_raw": signed,
        "unknown_word1_low6": w1 & 0x3f,
        "word0": f"0x{w0:08x}",
        "word1": f"0x{w1:04x}",
    }


def _decode_net(rid, index, payload):
    if payload[:4] != _TAG or len(payload) < 16:
        return {"id": rid, "index": index, "error": "bad tNOD header", "raw": payload.hex()}
    size, u8, u10, u12, count = struct.unpack_from("<IHHHH", payload, 4)
    nodes = [_decode_node(i, payload, 16 + 12 * i) for i in range(count)
             if 16 + 12 * i + 12 <= len(payload)]
    edges = [[n["index"], t] for n in nodes for t in n["links"]]
    bad = [e for e in edges if e[1] >= count]
    xs = [n["pos_x"] for n in nodes] or [0]
    zs = [n["pos_z"] for n in nodes] or [0]
    rec = {
        "id": rid, "index": index, "size": size, "size_matches_payload": size == len(payload),
        "unknown_8": u8, "unknown_10": u10, "unknown_12": u12,
        "node_count": count,
        "leftover_bytes": len(payload) - 16 - 12 * count,
        "bounds": {"min_x": min(xs), "max_x": max(xs), "min_z": min(zs), "max_z": max(zs)},
        "nodes": nodes,
        "edges": edges,
    }
    if bad:
        rec["dangling_edges"] = bad
    return rec


def extract_nets(mis_path, out_dir):
    with open(mis_path, "rb") as f:
        data = f.read()

    nets = []
    for rtype, rid, raw in iter_iff_resources(data):
        if rtype == "Cnet":
            nets.append(_decode_net(rid, len(nets), raw))
    if not nets:
        return 0

    by_id = {n["id"]: n for n in nets}
    for actor, refs in _actor_net_refs(data):
        for nid in refs:
            net = by_id.get(nid)
            entry = {"id": actor["id"], "kind": actor["kind"], "class": actor.get("class"),
                     "pos_x": round(actor["raw_x"] / 65536.0, 4),
                     "pos_z": round(actor["raw_z"] / 65536.0, 4), "start_node": None}
            if net is None:
                entry["error"] = f"Cnet {nid} not present"
            else:
                key = (actor["raw_x"] >> 8, actor["raw_z"] >> 8)
                for n in net.get("nodes", []):
                    if (n["raw_x"], n["raw_z"]) == key:
                        entry["start_node"] = n["index"]
                        break
                net.setdefault("actors", []).append(entry)

    out = {"level": os.path.splitext(os.path.basename(mis_path))[0],
           "coordinate_note": "pos_x/pos_z use the actors.json convention "
                              "(raw/256); Godot/fc-mit world units = pos*8",
           "nets": nets}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "nets.json"), "w") as f:
        json.dump(out, f, indent=1)
    return len(nets)


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mis_files = find_game_files("MIS")
    for mis_path in mis_files:
        level = os.path.splitext(os.path.basename(mis_path))[0]
        n = extract_nets(mis_path, os.path.join(repo_root, "parsed", level))
        print(f"{level}.MIS → {n} Cnet networks")


if __name__ == "__main__":
    main()
