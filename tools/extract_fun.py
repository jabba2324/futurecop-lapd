#!/usr/bin/env python3
"""Extract Cfun (level trigger functions) from Future Cop: L.A.P.D. PS1 MIS archives.

WHAT Cfun IS
  The level's polled trigger list: each "function" is a (condition script, action script)
  pair of RPN byte-code (same VM as RPNS — the opcode table and encodings are documented
  in extract_rpns.py, which this script imports). Every tick the game decrements each
  active function's countdown; when it underflows it reloads the countdown with the
  period, runs the condition and, if that returns non-zero, runs the action and
  decrements the remaining-fires count.

  Evidence (SLUS_007.39): loader 0x80049420 (finds the tFUN/tEXT chunks, entry count =
  (tFUN size - 12) / 20, multiplies period by 5, seeds countdown, turns the two script
  offsets into pointers into tEXT); ticker 0x80049564 (calls the interpreter twice);
  0x8004953c handles actor message 126 by writing count of function N (so scripts
  enable/disable functions with actor_msg(N, 126, count)).
  fc-mit FUNResource.cpp parses the same 20-byte entries (names there:
  how_many_times, time_units, zero, start_parameter_offset, start_code_offset).

Binary layout (little-endian)
  tFUN chunk
    +0  4cc  'tFUN' (stored "NUFt")
    +4  u32  chunk size (includes this 8-byte header)
    +8  u32  unknown_8 (always 1; fc-mit "un_data_id")
    +12 entries, 20 bytes each:
        +0  i32 count       0 = inactive, >0 = fires remaining, <0 = unlimited
        +4  i32 period      tick period in file units (loader stores x5). Negative =
                            first fire after |period|; positive = first fire after a
                            random phase in [0, period).
        +8  i32 countdown   runtime field, 0 on disc (unknown_8 if ever non-zero)
        +12 u32 cond_offset   offset into tEXT code of the condition script
        +16 u32 action_offset offset into tEXT code of the action script
  tEXT chunk (immediately after tFUN)
    +0  'tEXT' ("TXEt")  +4 u32 size  +8 u32 unknown_8 (always 1)  +12 code bytes
  Bytes after tEXT are reported as leftover.

  Time units (confirmed): one file unit = one NTSC VSync = 1/60 s. The loader
  multiplies the period by 5 into game clock units, and the clock advances by 5 per
  VSync (VSync callback 0x8001eff8), i.e. 300 units per second; the ticker 0x80049564
  is called from the main loop (0x800271a4 -> 0x80026d9c) with the frame's tick delta
  in the same units (0x8009f9fc, min 5 = one VSync, max 60). So period_seconds =
  period / 60 (59.94 Hz on real NTSC hardware; fc-mit's 0.016424851 s/unit is ~1.5 %
  off). A function is checked at most once per frame, so periods shorter than the
  frame time (usually 2 VSyncs at 30 fps) run once per frame.

Output: parsed/<LEVEL>/fun.json
  {"level", "id", "unknown_tfun_8", "unknown_text_8", "code_size",
   "functions": [{"index", "count", "count_meaning", "period", "first_fire",
                  "period_seconds", "countdown", "cond_offset", "action_offset",
                  "condition": {offset,length,hex,asm,pseudo,refs},
                  "action": {...}}],
   "unreached_code_bytes": {"offsets", "all_zero"} (tEXT bytes no function reaches;
   observed: 0-3 trailing bytes of 4-byte alignment pad,
   often non-zero garbage), "leftover_bytes"}
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import iter_iff_resources, find_game_files
from extract_rpns import build_context, script_length, script_record

_TAG_TFUN = b"NUFt"
_TAG_TEXT = b"TXEt"
VSYNCS_PER_SECOND = 60    # 1 period unit = 1 VSync = 5 clock units (1/300 s)


def _decode_cfun(payload, ctx):
    """Return the fun.json body for one Cfun payload."""
    if payload[:4] != _TAG_TFUN:
        return {"error": "missing tFUN", "raw": payload.hex()}
    fun_size, fun_unk = struct.unpack_from("<II", payload, 4)
    n = (fun_size - 12) // 20
    text_pos = fun_size
    if payload[text_pos:text_pos + 4] != _TAG_TEXT:
        return {"error": "missing tEXT", "raw": payload.hex()}
    text_size, text_unk = struct.unpack_from("<II", payload, text_pos + 4)
    code = payload[text_pos + 12:text_pos + text_size]

    functions = []
    reached = bytearray(len(code))
    for i in range(n):
        count, period, countdown, c_off, a_off = struct.unpack_from(
            "<iiiII", payload, 12 + 20 * i)
        rec = {
            "index": i,
            "count": count,
            "count_meaning": ("inactive" if count == 0 else
                              "unlimited" if count < 0 else f"{count} fire(s)"),
            "period": abs(period),
            "period_raw": period,
            "first_fire": "after_period" if period < 0 else "random_phase",
            "period_seconds": round(abs(period) / VSYNCS_PER_SECOND, 4),
            "countdown": countdown,
            "cond_offset": c_off,
            "action_offset": a_off,
        }
        for key, off in (("condition", c_off), ("action", a_off)):
            if off < len(code):
                rec[key] = script_record(code, off, ctx)
                ln = script_length(code, off)
                reached[off:off + ln] = b"\x01" * ln
            else:
                rec[key] = {"error": f"offset {off} outside tEXT ({len(code)} bytes)"}
        functions.append(rec)

    return {
        "unknown_tfun_8": fun_unk,
        "unknown_text_8": text_unk,
        "function_count": n,
        "tfun_entry_bytes_leftover": (fun_size - 12) % 20,
        "code_size": len(code),
        "functions": functions,
        "unreached_code_bytes": {
            "offsets": [i for i, r in enumerate(reached) if not r],
            "all_zero": all(code[i] == 0 for i, r in enumerate(reached) if not r)},
        "leftover_bytes": len(payload) - (text_pos + text_size),
    }


def extract_fun(mis_path, out_dir):
    with open(mis_path, "rb") as f:
        data = f.read()
    ctx = build_context(data)
    funs = [(rid, raw) for t, rid, raw in iter_iff_resources(data) if t == "Cfun"]
    if not funs:
        return 0
    out = {"level": os.path.splitext(os.path.basename(mis_path))[0], "cfun": []}
    for rid, raw in funs:
        body = {"id": rid, "size": len(raw)}
        body.update(_decode_cfun(raw, ctx))
        out["cfun"].append(body)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "fun.json"), "w") as f:
        json.dump(out, f, indent=1)
    return sum(len(b.get("functions", [])) for b in out["cfun"])


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mis_files = find_game_files("MIS")
    for mis_path in mis_files:
        level = os.path.splitext(os.path.basename(mis_path))[0]
        n = extract_fun(mis_path, os.path.join(repo_root, "parsed", level))
        print(f"{level}.MIS → {n} Cfun functions")


if __name__ == "__main__":
    main()
