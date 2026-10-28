#!/usr/bin/env python3
"""Decode the .DEM attract-mode demo recordings of Future Cop: L.A.P.D. (PS1).

A .DEM is a raw dump of the game's input recorder: run-length-encoded joypad
state for one or two players plus a per-frame timing stream. Files on the disc:
M2C_A1 and CONFT_A1 (1 player), M3B_B1 and SLIM_B1 (2 players).

Evidence (SLUS_007.39; everything below is confirmed in code unless marked):
  * 0x80059bfc loads the demo for level start (called from the level loader
    0x80024a24 when the demo number 0x8009fa84 != 0). It builds the name with
    "%s_%c%d.dem": level, '@' + number of players (A = 1, B = 2) and the demo
    number (the front end's demo table, 0x80078c88, always uses 1). The level
    loader also seeds the RNG with that number (0x800197a8), so playback is
    deterministic. The retail game only plays demos back (0x8009fa84 > 0); the
    recorder itself is not in the executable.
  * 0x80059db8 is called once per game frame from 0x8002703c and returns the
    frame's tick delta; it replaces the joypad words 0x8009f7c0[0..1] with
    buttons << 16. It ends the demo (game state 5) at the first pad record whose
    frame count is 0.
  * 0x8002703c then uses the returned value as the frame's tick delta
    (0x8009f9fc) AND advances the game clock by it, so a demo replays exactly at
    the recorded speed whatever the real frame rate. A "D E M O" caption blinks
    (0x800271a4).

Layout (little-endian). Header = 36 bytes:
  +0x00 u8   num_players          1 or 2 (also the A/B letter)
  +0x01 u8   difficulty           copied to v[2] and v[38] (p1/p2_difficulty, see
                                  extract_rpns.py); 1 in all four files
  +0x02 6 x u8 weapons            copied to v[10..15]: p1 gun, heavy, special,
                                  p2 gun, heavy, special (00 10 20 00 10 20 in all
                                  files = gun 0, heavy 16, special 32)
  +0x08 u16[2] records            pad records per player. The loader locates the
                                  streams with these counts (stream k starts at
                                  0x24 + 4 * sum(records before k)); only the
                                  first num_players are used (P2's is stale junk,
                                  0x07ff, in 1-player files)
  +0x0c u32[2] stream start       recorder RAM pointers (dev kit, 0x8071xxxx);
  +0x14 u32[2] stream cursor      the loader overwrites start and cursor with the
  +0x1c u32  timing start         real addresses. In the file the cursors hold
  +0x20 u32  timing cursor        the recorder's end positions, so cursor - start
                                  is each stream's length (junk for P2 in
                                  1-player files).
Body, streams packed back to back in this order:
  P1 pad stream   records x {u16 frames, u16 buttons}
  P2 pad stream   only when num_players == 2
  timing stream   (timing cursor - timing start) bytes = {u8 frames, u8 value}
The last pad record of each stream is the terminator (frames = 0, junk buttons).
Checked: 36 + stream lengths == file size for all four files, the counts match
the pointer lengths, and the pad and timing streams of a file cover the same
number of game frames (M2C_A1 1519, M3B_B1 955, CONFT_A1 1510, SLIM_B1 1178).

buttons: NOT raw pad bits but the game's remapped input word >> 16. 0x8001e620
  builds it from the raw pad state (~buffer word of 0x8001ef8c: byte 2/3 =
  bits 16-31 in the standard PS1 order) through the per-player controller
  configuration (14 masks at 0x800aac50 + 0x38*player). With the default
  configuration (0x8001e588), which is identity in meaning:
    2 Select   3 Start   4 L2   5 R2   6 L1   7 R1   8 Triangle   9 Circle
    10 Cross   11 Square   12 Left   13 Right   14 Up   15 Down
  Bits 0 and 1 are never produced. (The recorded data fits: Up = forward is the
  most-held bit, then Square/Cross/Circle fire and Left/Right turning; L1+R1
  together is the transform, 0x80055988.) Which configuration the developers
  recorded with is not stored; the default is assumed.
timing value: the frame's tick delta in game-clock units (1/300 s; 5 per NTSC
  VSync), i.e. frame time = value / 300 s: 10 = 2 VSyncs (30 fps). Typically 10-14
  in 1-player and 16-30 in 2-player split-screen demos. Cross-check: sum(frames *
  value) / 300 is 60.0 s (60.01-60.03) for every file, i.e. each demo was cut at
  one minute.

Output: parsed/<LEVEL>/demos/<NAME>.json
  {"num_players", "difficulty", "weapons", "header_hex", "record_counts",
   "recorder_pointers", "total_frames", "duration_seconds", "button_bits",
   "players": [{"player", "records": [{"start_frame", "frames", "buttons",
                "pressed"[, "terminator"]}], "total_frames"}],
   "timing": [{"start_frame", "frames", "value"}]}
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import find_game_files

_HEADER_SIZE = 0x24
CLOCK_UNITS_PER_SECOND = 300
# demo bit -> button under the default controller configuration (0x8001e588)
BUTTONS = {2: "select", 3: "start", 4: "l2", 5: "r2", 6: "l1", 7: "r1", 8: "triangle",
           9: "circle", 10: "cross", 11: "square", 12: "left", 13: "right", 14: "up",
           15: "down"}
_WEAPON_SLOTS = ("p1_gun", "p1_heavy", "p1_special", "p2_gun", "p2_heavy", "p2_special")


def _button_names(mask):
    names = [BUTTONS.get(bit, f"bit{bit}") for bit in range(16) if mask & (1 << bit)]
    return names


def decode_dem(data):
    num_players = data[0]
    counts = struct.unpack_from("<HH", data, 8)
    p1_s, p2_s, p1_e, p2_e, t_s, t_e = struct.unpack_from("<6I", data, 12)
    lengths = [4 * counts[k] for k in range(num_players)]
    ptr_lengths = [p1_e - p1_s, p2_e - p2_s][:num_players]
    if lengths != ptr_lengths:
        raise ValueError("record counts disagree with the recorder pointers")
    t_len = t_e - t_s
    if _HEADER_SIZE + sum(lengths) + t_len != len(data):
        raise ValueError("stream lengths do not add up to the file size")

    pos = _HEADER_SIZE
    players = []
    for idx, length in enumerate(lengths):
        recs = []
        frame = 0
        for i in range(length // 4):
            frames, buttons = struct.unpack_from("<HH", data, pos + 4 * i)
            rec = {"start_frame": frame, "frames": frames, "buttons": buttons,
                   "pressed": _button_names(buttons)}
            if frames == 0:
                # playback stops here (0x80059db8); its buttons are junk
                rec["pressed"] = []
                rec["terminator"] = True
            recs.append(rec)
            frame += frames
        players.append({"player": idx + 1, "records": recs, "total_frames": frame})
        pos += length

    timing = []
    frame = 0
    clock = 0
    for i in range(t_len // 2):
        frames, value = data[pos + 2 * i], data[pos + 2 * i + 1]
        timing.append({"start_frame": frame, "frames": frames, "value": value})
        frame += frames
        clock += frames * value

    return {
        "num_players": num_players,
        "difficulty": data[1],
        "weapons": dict(zip(_WEAPON_SLOTS, data[2:8])),
        "header_hex": data[:_HEADER_SIZE].hex(),
        "record_counts": list(counts),
        "recorder_pointers": {"p1_start": hex(p1_s), "p1_end": hex(p1_e),
                              "p2_start": hex(p2_s), "p2_end": hex(p2_e),
                              "timing_start": hex(t_s), "timing_end": hex(t_e)},
        "total_frames": frame,
        "timing_unit": "game clock units (1/300 s) per frame",
        "duration_seconds": round(clock / CLOCK_UNITS_PER_SECOND, 3),
        "button_bits": {str(b): n for b, n in BUTTONS.items()},
        "players": players,
        "timing": timing,
    }


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    parsed = os.path.join(repo_root, "parsed")
    files = find_game_files("DEM")
    for path in files:
        name = os.path.splitext(os.path.basename(path))[0].upper()
        level = name.split("_")[0]
        with open(path, "rb") as f:
            dem = decode_dem(f.read())
        out_dir = os.path.join(parsed, level, "demos")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, name + ".json"), "w") as f:
            json.dump(dem, f, indent=1)
        print(f"  {name}.DEM -> parsed/{level}/demos/{name}.json  "
              f"{dem['num_players']}P, {dem['total_frames']} frames, "
              f"{dem['duration_seconds']} s")


if __name__ == "__main__":
    main()
