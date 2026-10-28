#!/usr/bin/env python3
"""Extract Cshd (sound-effect definition tables) from Future Cop: L.A.P.D. PS1 MIS archives.

WHAT Cshd IS
  The table that maps sound-effect ids used by game code / scripts to a sample
  ("program") in a Cvkb/Cvkh sound bank, plus playback parameters (loop flag, max
  simultaneous voices). Levels carry two Cshd resources, BOTH with resource id 1:
    * the global table  (id_0=16, id_1=1)  -> banks 1..16 (identical in every level)
    * the mission table (id_0=1,  id_1=50) -> bank 50 (level-specific sounds)
  FE.MIS has a single (1, 1) table for the menu bank. iff_utils.iter_iff_resources keys
  resources by (type, id) and so concatenates the two tables; this script walks the
  SHDR headers itself (extract_rpns._iter_resources) to keep them apart.

  Evidence: fc-mit SHDResource.cpp (header, optional entries, 12-byte entries and the
  PS1_GUI / GLOBAL / MISSION type guess from id_0/id_1). Verified here: id_0 always
  equals 1 + number of optional groups, the optional group bank ids are exactly the
  other Cvkh/Cvkb ids present (2..16 in the global table), and every sound_id is below
  the program count of the bank it is assigned to (Cvkh byte +4). So id_0 = number of
  banks, id_1 = first bank id, and optional groups are (bank_id, count, first_entry)
  for the extra banks. (Block lengths are entry counts, not program counts: the global
  main block has 62 entries for bank 1's 52 programs.)

Binary layout (little-endian)
  +0  u16 header        always 4
  +2  u16 bank_count    (fc-mit id_0)
  +4  u16 first_bank    (fc-mit id_1) Cvkb/Cvkh resource id of the main block's bank
  +6  u16 main_count    entries [0, main_count) belong to first_bank (fc-mit entry_count)
  +8  u16 table_offset  byte offset of the entry table
  +10 groups of 6 bytes until table_offset: u16 bank_id, u16 count,
      u16 byte offset of the group's first entry (-> entry index)
  table_offset: 12-byte entries until fewer than 12 bytes remain
      +0  u16 group_id     sound key; entries with one key share one runtime sound slot
                           and are its random alternatives (up to 4)
      +2  u16 sound_id     program index inside the entry's bank (Cvkh "tone" record)
      +4  u8  priority     voice-stealing priority (fc-mit unk_metadata; 7 or 9 on disc;
                           0 is read as 1 and values > 10 as 10)
      +5  u8  sound_limit  max simultaneous instances (clamped to 4 at runtime)
      +6  u8  loop         0/1
      +7  u8  positional   1 = the script ids go into the positional (3D) id table,
                           0 = the plain (2D) id table (fc-mit unk_boolean)
      +8  u8[4] script ids  ids used by game code / scripts; byte +8 is the only one
                           used on disc (script_id), +9..+11 are zero (zero)
  Trailing bytes (< 12) after the last entry are kept as unknown_tail (2 bytes in
  every mission/FE table, apparently 4-byte alignment padding with stale bytes).

  Runtime (SLUS_007.39, confirmed by decompilation): Cshd is kept by the sound loader
  0x80023930 (two tables max, 0x8009f640). The header from +2 is really bank_count
  records of (u16 bank_id, u16 count, u16 byte offset of the first entry), the main
  block being record 0: 0x80023528 finds a bank's record and 0x800235d4 registers its
  entries when the bank has been uploaded. 0x80047344 registers one entry: it reuses
  the runtime slot of an earlier entry with the same group_id (0x800472e8) or takes
  the next of 110 slots (0x800a7938, 28 bytes), stores priority / limit / loop, then
  maps each non-zero script id to the slot: ids 1..69 into 0x800a1880 (positional = 0)
  or 0x800a1830 (positional = 1), ids 70..129 into a shared table (0x800a18d0).
  0x800241d0 appends (program, bank slot) as one of up to 4 alternatives.
  Plain ids are played by 0x80046c84 (51 call sites, incl. the script variable
  *ref[3], see extract_rpns.py); positional ids by 0x80046e88 / 0x80045678 (looping
  emitters, e.g. BaseEntity.ambient_sound via 0x8004df28) and 0x80046a58 / 0x80046cf0.
  Voice stealing (0x80046b04) compares distance + priority * 16.

Sample files: sound_id is a PROGRAM index of the bank's Cvkh header. Each program
  record has a 4-byte header (+0 u8 tone count, always 1) followed by 20-byte tones
  whose +16 u32 is the sample offset in the Cvkb (relocated by 0x800219ec). Several
  programs share a sample with a different pitch, which is why bank 1 has 52 programs
  but only 35 samples. Each entry therefore lists
      sample_offset  the program's Cvkb sample offset
      tone_wav       sfx_tones/vkbNN_tPP.wav (extract_sfx_meta.py: the program at its
                     in-game pitch)
      sample_wav     sfx/vkbNN_sMMM.wav (extract_sfx.py: the raw sample at 11025 Hz)
  All 1775 entries in the 15 levels resolve to a sample.

Output: parsed/<LEVEL>/shd.json
  {"level", "tables": [{"index", "id", "kind", "header", "bank_count", "first_bank",
   "main_count", "table_offset", "groups": [{"bank", "count", "first_entry"}],
   "entries": [{"index", "bank", "group_id", "sound_id", "priority", "priority_raw",
                "sound_limit", "sound_limit_effective", "loop", "loop_raw",
                "positional", "positional_raw", "script_id", "script_ids", "zero",
                "sample_offset", "tone_wav", "sample_wav"}],
   "unknown_tail", "checks"}], "banks": {cvkh_id: program_count}}
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_rpns import _iter_resources
from lib.iff_utils import find_game_files
from extract_sfx_meta import _extract_sfx_segments

_KINDS = {(1, 1): "ps1_gui", (16, 1): "global", (1, 50): "mission"}


def _decode_shd(payload, banks, programs):
    hdr, bank_count, first_bank, main_count, table_off = struct.unpack_from("<5H", payload, 0)
    groups = []
    for off in range(10, table_off - 5, 6):
        bid, cnt, eoff = struct.unpack_from("<3H", payload, off)
        groups.append({"bank": bid, "count": cnt, "first_entry": (eoff - table_off) // 12,
                       "first_entry_byte_offset": eoff})
    entries = []
    for i, off in enumerate(range(table_off, len(payload) - 11, 12)):
        g, s, pri, lim, loop, u7, sid, z0, z1, z2 = struct.unpack_from("<HHBBBBBBBB", payload, off)
        entries.append({"index": i, "bank": None, "group_id": g, "sound_id": s,
                        "priority_raw": pri, "priority": 1 if pri == 0 else min(pri, 10),
                        "sound_limit": lim, "sound_limit_effective": min(lim, 4),
                        "loop": bool(loop), "loop_raw": loop,
                        "positional": u7 == 1, "positional_raw": u7, "script_id": sid,
                        "script_ids": [x for x in (sid, z0, z1, z2) if x],
                        "zero": [z0, z1, z2]})
    for e in entries[:main_count]:
        e["bank"] = first_bank
    for grp in groups:
        for e in entries[grp["first_entry"]:grp["first_entry"] + grp["count"]]:
            e["bank"] = grp["bank"]
    for e in entries:
        prog = programs.get(e["bank"], [])
        if 0 <= e["sound_id"] < len(prog):
            e.update(prog[e["sound_id"]])

    tail = payload[table_off + 12 * len(entries):]
    blocks = [(first_bank, main_count)] + [(g["bank"], g["count"]) for g in groups]
    checks = {
        "header_is_4": hdr == 4,
        "bank_count_matches_groups": bank_count == 1 + len(groups),
        "blocks_equal_program_count": all(banks.get(b) == n for b, n in blocks),
        "all_banks_present": all(b in banks for b, _ in blocks),
        "sound_ids_in_range": all(e["bank"] in banks and e["sound_id"] < banks[e["bank"]]
                                  for e in entries),
        "unassigned_entries": [e["index"] for e in entries if e["bank"] is None],
        "nonzero_padding": [e["index"] for e in entries if any(e["zero"])],
    }
    return {"kind": _KINDS.get((bank_count, first_bank), "unknown"),
            "header": hdr, "bank_count": bank_count, "first_bank": first_bank,
            "main_count": main_count, "table_offset": table_off, "groups": groups,
            "entries": entries, "unknown_tail": tail.hex(), "checks": checks}


def _program_files(resources):
    """{bank: [{"sample_offset", "tone_wav", "sample_wav"} per program]} from Cvkh/Cvkb."""
    vkb = {r["id"]: r["payload"][:r["size"]] for r in resources if r["type"] == "Cvkb"}
    out = {}
    for r in resources:
        h = r["payload"]
        if r["type"] != "Cvkh" or len(h) < 16:
            continue
        segs = _extract_sfx_segments(vkb[r["id"]]) if r["id"] in vkb else {}
        progs = []
        for p in range(h[4]):
            off = struct.unpack_from("<I", h, 16 + 4 * p)[0]
            smp = struct.unpack_from("<I", h, off + 20)[0] if off + 24 <= len(h) else None
            seg = segs.get(smp - 16 if smp else 0) if smp is not None else None
            progs.append({"sample_offset": smp,
                          "tone_wav": "sfx_tones/vkb%02d_t%02d.wav" % (r["id"], p),
                          "sample_wav": ("sfx/vkb%02d_s%03d.wav" % (r["id"], seg)
                                         if seg is not None else None)})
        out[r["id"]] = progs
    return out


def extract_shd(mis_path, out_dir):
    with open(mis_path, "rb") as f:
        data = f.read()
    resources = list(_iter_resources(data))
    banks = {r["id"]: r["payload"][4] for r in resources
             if r["type"] == "Cvkh" and len(r["payload"]) > 4}
    programs = _program_files(resources)
    tables = []
    for r in resources:
        if r["type"] == "Cshd":
            body = {"index": len(tables), "id": r["id"], "size": len(r["payload"])}
            body.update(_decode_shd(r["payload"], banks, programs))
            tables.append(body)
    if not tables:
        return 0
    out = {"level": os.path.splitext(os.path.basename(mis_path))[0],
           "banks": {str(k): v for k, v in sorted(banks.items())},
           "tables": tables}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "shd.json"), "w") as f:
        json.dump(out, f, indent=1)
    return sum(len(t["entries"]) for t in tables)


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mis_files = find_game_files("MIS")
    for mis_path in mis_files:
        level = os.path.splitext(os.path.basename(mis_path))[0]
        n = extract_shd(mis_path, os.path.join(repo_root, "parsed", level))
        print(f"{level}.MIS → {n} Cshd entries")


if __name__ == "__main__":
    main()
