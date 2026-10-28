#!/usr/bin/env python3
"""Extract the .LSC loading screens of Future Cop: L.A.P.D. (PS1).

Despite the name (an old note once called them "level-loading
scripts"), .LSC files are LoadingSCreen images: one full-screen 320x256 picture
shown while a MIS is streamed in. The executable names them all explicitly
(feld.lsc, lax1ld.lsc, conftpo.lsc, alegal.lsc, plus an unused alang.lsc).

File layout (little-endian):
  +0   u32  size_a      byte size of the left-half image record
  +4   u32  size_b      byte size of the right-half image record
  +8   image record A   (size_a bytes)
  +8+size_a image record B (size_b bytes)          8 + size_a + size_b == file size
Image record (identical to a FE.MIS Cmdc resource):
  +0   u16  width  (160)
  +2   u16  height (256)
  +4   BS v2 MDEC frame (see lib/bs_decode.py)
The two 160x256 halves are placed side by side: A on the left, B on the right.

Naming: <LEVEL>LD.LSC = loading screen for <LEVEL>.MIS; <LEVEL>PO.LSC = second
screen used by the Precinct Assault levels (purpose of "PO" not confirmed; the
pictures look like alternative loading art); FELD = front end; ALEGAL = legal /
trademark notice shown at boot.

Confirmed: all 21 files decode cleanly ("LOADING IN PROGRESS" art, the legal text).

Output: parsed/<LEVEL>/loading/<NAME>.png  (ALEGAL goes to parsed/FE/loading/)
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.bs_decode import decode_bs_image
from lib.iff_utils import find_game_files

try:
    from PIL import Image
except ImportError:
    print("PIL/Pillow required: pip install Pillow", file=sys.stderr)
    sys.exit(1)


def _level_for(name):
    """Map an LSC basename (no extension) to the MIS level folder it belongs to."""
    if name == "ALEGAL":
        return "FE"
    for suffix in ("LD", "PO"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def _halves(data):
    """Yield (width, height, bs_frame) for the two image records."""
    size_a, size_b = struct.unpack_from("<II", data, 0)
    if 8 + size_a + size_b != len(data):
        raise ValueError("LSC size fields do not add up (%d+%d+8 != %d)"
                         % (size_a, size_b, len(data)))
    for off, size in ((8, size_a), (8 + size_a, size_b)):
        w, h = struct.unpack_from("<HH", data, off)
        yield w, h, data[off + 4: off + size]


def decode_lsc(data):
    """Return a Pillow image of the full loading screen."""
    parts = [decode_bs_image(frame, w, h)[0] for w, h, frame in _halves(data)]
    full = Image.new("RGB", (sum(p.width for p in parts), max(p.height for p in parts)))
    x = 0
    for p in parts:
        full.paste(p, (x, 0))
        x += p.width
    return full


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    parsed = os.path.join(repo_root, "parsed")
    files = find_game_files("LSC")
    for path in files:
        name = os.path.splitext(os.path.basename(path))[0].upper()
        level = _level_for(name)
        out_dir = os.path.join(parsed, level, "loading")
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "rb") as f:
            img = decode_lsc(f.read())
        out = os.path.join(out_dir, name + ".png")
        img.save(out)
        print(f"  {name}.LSC -> parsed/{level}/loading/{name}.png  {img.width}x{img.height}")
    print(f"\nDone - {len(files)} loading screens.")


if __name__ == "__main__":
    main()
