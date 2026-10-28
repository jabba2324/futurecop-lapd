#!/usr/bin/env python3
"""Decode the .DEM attract-mode demo recordings of Future Cop: L.A.P.D. (PS1).

A .DEM is a raw dump of the game's input recorder: run-length-encoded joypad
state for one or two players plus a per-frame timing stream. The executable builds
the name with "%s_%c%d.dem" (level, letter, digit). Files on the disc:
M2C_A1 and CONFT_A1 (1 player), M3B_B1 and SLIM_B1 (2 players).

Layout (little-endian). Header = 36 bytes, the recorder's RAM pointers were dumped
verbatim (they point into the dev kit's 8 MB RAM, 0x8071xxxx):
  +0x00 u8   num_players          1 or 2  (matches the A/B letter: A=1, B=2)
  +0x01 u8   unknown              always 1
  +0x02 6B   unknown              always 00 10 20 00 10 20
  +0x08 u16  p1_records           number of 4-byte records in the P1 pad stream
  +0x0a u16  p2_records           ditto for P2 (stale junk 0x07ff when 1 player)
  +0x0c u32  p1_start   ptr       +0x10 u32 p2_start ptr (junk if 1 player)
  +0x14 u32  p1_end     ptr       +0x18 u32 p2_end   ptr (junk if 1 player)
  +0x1c u32  timing_start ptr     +0x20 u32 timing_end ptr
Body, streams packed back to back in this order:
  P1 pad stream   (p1_end - p1_start) bytes = p1_records x {u16 frames, u16 buttons}
  P2 pad stream   (p2_end - p2_start) bytes, only when num_players == 2
  timing stream   (timing_end - timing_start) bytes = {u8 frames, u8 value} pairs
Confirmed: 36 + sum(stream lengths) == file size for all four files, len/4 equals the
record counts, and the frame totals of every stream in a file are identical
(M2C_A1 1519, M3B_B1 955, CONFT_A1 1510, SLIM_B1 1178 game frames).

buttons: active-high PS1 digital pad bits in the standard (psx-spx) order -
  0 Select, 3 Start, 4 Up, 5 Right, 6 Down, 7 Left, 8 L2, 9 R2, 10 L1, 11 R1,
  12 Triangle, 13 Circle, 14 Cross, 15 Square. (Plausible: 0 = idle is the most
  common value, and Cross / L1+R1 dominate, which fits Future Cop's controls; not
  verified by playing the demo back.)
timing value: per-game-frame duration in an unknown tick unit (typically 10-14 in
  1-player demos and 16-30 in 2-player split-screen demos, i.e. it grows with load).

Output: parsed/<LEVEL>/demos/<NAME>.json
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import find_game_files

_HEADER_SIZE = 0x24
_BUTTONS = {0: "select", 1: "l3", 2: "r3", 3: "start", 4: "up", 5: "right", 6: "down",
            7: "left", 8: "l2", 9: "r2", 10: "l1", 11: "r1", 12: "triangle",
            13: "circle", 14: "cross", 15: "square"}


def _button_names(mask):
    return [name for bit, name in _BUTTONS.items() if mask & (1 << bit)]


def decode_dem(data):
    num_players = data[0]
    p1_n, p2_n = struct.unpack_from("<HH", data, 8)
    p1_s, p2_s, p1_e, p2_e, t_s, t_e = struct.unpack_from("<6I", data, 12)
    lengths = [p1_e - p1_s]
    if num_players == 2:
        lengths.append(p2_e - p2_s)
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
            recs.append({"start_frame": frame, "frames": frames, "buttons": buttons,
                         "pressed": _button_names(buttons)})
            frame += frames
        players.append({"player": idx + 1, "records": recs, "total_frames": frame})
        pos += length

    timing = []
    frame = 0
    for i in range(t_len // 2):
        frames, value = data[pos + 2 * i], data[pos + 2 * i + 1]
        timing.append({"start_frame": frame, "frames": frames, "value": value})
        frame += frames

    return {
        "num_players": num_players,
        "header_unknown": data[1:8].hex(),
        "record_counts": [p1_n, p2_n],
        "recorder_pointers": {"p1_start": hex(p1_s), "p1_end": hex(p1_e),
                              "p2_start": hex(p2_s), "p2_end": hex(p2_e),
                              "timing_start": hex(t_s), "timing_end": hex(t_e)},
        "total_frames": frame,
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
              f"{dem['num_players']}P, {dem['total_frames']} frames")


if __name__ == "__main__":
    main()
