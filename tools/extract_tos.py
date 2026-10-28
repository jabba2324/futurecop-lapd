#!/usr/bin/env python3
"""Extract Ctos (streamed-audio offset table) from Future Cop: L.A.P.D. PS1 MIS archives.

WHAT Ctos IS
  "Table Of Streams": the index of the level's SWVR streams (the 0x6000-aligned "FILE"
  blocks after the DATA section that hold BGAV voice clips and CANM talking-head
  animations). Scripts play a stream by its *index in this table*: RPN opcodes 0x0c
  (queue) / 0x0d (play now) push the index into an 8-entry queue at 0x800a0bc0, and the
  voice player at 0x80029d54 reads table[index].offset (pointer stored at gp+0x484 by
  the Ctos loader 0x80029e20). fc-mit uses the same offsets to key its SWVR accessors
  (Accessor.cpp, AnnouncementPlayer.cpp: "tos_offset = file_offset - first_swvr_offset").

  Verified here: every offset lands exactly on an 'RVWS' chunk in every level, and the
  names resolve to the voices/<name>.wav files extract_sfx.py writes. Index 0-based:
  e.g. CONFT Cfun 2 plays index 6 = m0007.

Binary layout (little-endian)
  +0  4cc 'tTOS' (stored "SOTt")
  +4  u32 chunk size (whole payload)
  +8  u32 version (always 1; fc-mit rejects anything else)
  +12 u32 entry_count
  +16 entries, 12 bytes each:
      +0 u32 offset   byte offset of the stream's RVWS chunk, relative to the first SWVR
                      chunk; the SWVR section starts at the file offset in CTRL +0x14
      +4 u32 unknown_4 (always 0)
      +8 u32 unknown_8 (always 0)

  RVWS chunk (36 bytes): 'RVWS', u32 size=36, 8 bytes, 'ELIF' + name (".stream"
  suffix possibly truncated). The next chunk's tag tells the content: 'BGAV' = voice,
  'MNAC' (CANM) = animation followed by its audio, 'MGAV' = level music (the one
  stream not listed in the table; reported as unlisted_streams).

Output: parsed/<LEVEL>/tos.json
  {"level", "id", "size", "version", "entry_count", "swvr_base",
   "entries": [{"index", "offset", "file_offset", "stream", "content", "voice_file",
                "unknown_4", "unknown_8"}],
   "unlisted_streams": [names of RVWS blocks not in the table], "leftover_bytes"}
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import iter_iff_resources, find_game_files

_TAG = b"SOTt"
_BLOCK = 0x6000
_CONTENT = {b"BGAV": "voice", b"MNAC": "animation+voice", b"MGAV": "music"}


def _rvws_name(data, pos):
    """Return the stream name of the RVWS chunk at pos, or None."""
    if data[pos:pos + 4] != b"RVWS":
        return None
    raw = data[pos + 16:pos + 36].split(b"\x00")[0]
    if not raw.startswith(b"ELIF"):
        return None
    name = raw[4:].decode("ascii", errors="replace")
    for n in range(len(".stream"), 0, -1):
        if name.endswith(".stream"[:n]):
            return name[:-n]
    return name


def _decode_tos(data, payload, voices_dir):
    if payload[:4] != _TAG or len(payload) < 16:
        return {"error": "bad tTOS header", "raw": payload.hex()}
    size, version, count = struct.unpack_from("<III", payload, 4)
    base = struct.unpack_from("<I", data, 0x14)[0]
    entries = []
    listed = set()
    for i in range(count):
        if 16 + 12 * i + 12 > len(payload):
            break
        off, u4, u8 = struct.unpack_from("<III", payload, 16 + 12 * i)
        fpos = base + off
        name = _rvws_name(data, fpos)
        listed.add(fpos)
        tag = data[fpos + 36:fpos + 40]
        voice = f"voices/{name}.wav" if name else None
        entries.append({
            "index": i,
            "offset": off,
            "file_offset": fpos,
            "stream": name,
            "content": _CONTENT.get(tag, tag.decode("ascii", errors="replace")),
            "voice_file": voice,
            "voice_file_exists": bool(voice and os.path.exists(os.path.join(voices_dir, f"{name}.wav"))),
            "unknown_4": u4,
            "unknown_8": u8,
        })
    unlisted = []
    pos = base
    while pos < len(data):
        if pos not in listed:
            name = _rvws_name(data, pos)
            if name is not None:
                unlisted.append({"file_offset": pos, "offset": pos - base, "stream": name,
                                 "content": _CONTENT.get(data[pos + 36:pos + 40], "?")})
        pos += _BLOCK
    return {"size": size, "size_matches_payload": size == len(payload), "version": version,
            "entry_count": count, "swvr_base": base, "entries": entries,
            "unresolved_entries": [e["index"] for e in entries if e["stream"] is None],
            "unlisted_streams": unlisted,
            "leftover_bytes": len(payload) - 16 - 12 * count}


def extract_tos(mis_path, out_dir):
    with open(mis_path, "rb") as f:
        data = f.read()
    tables = [(rid, raw) for t, rid, raw in iter_iff_resources(data) if t == "Ctos"]
    if not tables:
        return 0
    out = {"level": os.path.splitext(os.path.basename(mis_path))[0], "ctos": []}
    for rid, raw in tables:
        body = {"id": rid}
        body.update(_decode_tos(data, raw, os.path.join(out_dir, "voices")))
        out["ctos"].append(body)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "tos.json"), "w") as f:
        json.dump(out, f, indent=1)
    return sum(len(b.get("entries", [])) for b in out["ctos"])


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mis_files = find_game_files("MIS")
    for mis_path in mis_files:
        level = os.path.splitext(os.path.basename(mis_path))[0]
        n = extract_tos(mis_path, os.path.join(repo_root, "parsed", level))
        print(f"{level}.MIS → {n} Ctos stream entries")


if __name__ == "__main__":
    main()
