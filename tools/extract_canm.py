#!/usr/bin/env python3
"""Extract CANM/MDEC "talking panel" animations from Future Cop: L.A.P.D. PS1 MIS files.

The SWVR section of the Crime War MIS files (not the PA ones, not FE) holds, next to
the BGAV voice blocks, top-level chunks tagged `MNAC` (= "CANM" byte-reversed). They are
small MDEC-compressed animations (64x48, the in-game radio message panels such as
"WEAPONS UPGRADE HEAVY") that play together with the voice stream named by the
preceding RVWS chunk. fc-mit's IFF.cpp reads the header but dumps the payload as
an unknown "mdec" resource; the Mac/PC equivalent is the `Canm` (ANM) resource.

Top-level chunk layout (little-endian, inside the 0x6000-byte block grid):
  +0x00 4s   'MNAC'
  +0x04 u32  chunk size (header + frames)
  +0x08 u32  1   (fc-mit: CANM metadata, always 1)
  +0x0c u32  0
  +0x10 4s   'CEDM' ("MDEC" reversed)
  +0x14 u8   frames in this chunk (15)
  +0x15 u8   number of chunks the animation is split into (2)
  +0x16 u16  frames per chunk (15)
  +0x18 u16  total frame count (30)
  +0x1a u16  frame slot size in bytes (0x600)
  +0x1c u16  width (64)     +0x1e u16 height (48)
  +0x20 8B   zero
  +0x28      frames, each in a fixed slot of `slot size` bytes:
               u16 width, u16 height, BS v2 frame (see lib/bs_decode.py), zero padding
Consecutive MNAC chunks under the same RVWS stream name form one animation.

Confirmed: every frame decodes to a clean 64x48 picture; 2 chunks x 15 frames per
stream in all 9 CW levels. Each frame carries a 1-pixel green border that appears to
be part of the encoded art.

Playback rate: 10 fps, played once (confirmed in SLUS_007.39). The SWVR stream parser
0x8002902c queues up to two MNAC chunks of the current voice stream and sums their
+0x16 frame counts. The panel updater
0x80029748 then shows frame (clock - start) / 30 - 1, where start = clock - 30 at the
moment the voice starts, i.e. frame 0 immediately and one new frame every 30 clock
units. The game clock (0x8009fa60 / 0x8009faa4) advances by 5 per VSync in the VSync
callback 0x8001eff8, so 300 units = 1 s (NTSC) and 30 units = 0.1 s. The frame is
picked through the chunk list (frame slot size at +0x1a) and MDEC-decoded to VRAM
(0x3c0, 0x1cc) by 0x8001bbec. When the frame index passes the total count the panel
stops (no loop): 30 frames = 3.0 s.

Output: parsed/<LEVEL>/anims/<stream>.png  (vertical strip, frame 0 on top)
        parsed/<LEVEL>/anims/<stream>.gif  (animated preview at the game's 10 fps)
        parsed/<LEVEL>/anims/anims.json    (per-stream metadata incl. fps)
"""

import json
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

_BLOCK = 0x6000
_FRAME_DATA_OFFSET = 0x28
_PREVIEW_FPS = 10   # 30 clock units (1/300 s) per frame, see docstring


def _iter_chunks(data):
    """Yield (offset, tag, chunk_bytes) for every top-level chunk after CTRL."""
    pos = struct.unpack_from("<I", data, 4)[0]
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if size < 8 or (pos % _BLOCK) + size > _BLOCK:
            pos = (pos // _BLOCK + 1) * _BLOCK      # tail fill / end of block
            continue
        yield pos, tag, data[pos:pos + size]
        if tag == b"LLIF":
            pos = (pos // _BLOCK + 1) * _BLOCK
        else:
            pos += size


def _stream_name(rvws):
    raw = rvws[16:36].split(b"\x00")[0].decode("ascii", "replace")
    if raw.startswith("ELIF"):
        raw = raw[4:]
    for n in range(len(".stream"), 0, -1):
        if raw.endswith(".stream"[:n]):
            return raw[:-n]
    return raw


def iter_canm(data):
    """Yield (stream_name, file_offset, header_dict, [bs_frame_bytes, ...]) per MNAC chunk."""
    stream = None
    for pos, tag, chunk in _iter_chunks(data):
        if tag == b"RVWS":
            stream = _stream_name(chunk)
        elif tag == b"MNAC":
            (meta, zero, mdec, in_chunk, n_chunks, per_chunk, total,
             slot, w, h) = struct.unpack_from("<II4sBBHHHHH", chunk, 8)
            hdr = {"metadata": meta, "zero": zero, "tag": mdec[::-1].decode("ascii", "replace"),
                   "frames_in_chunk": in_chunk, "chunks": n_chunks,
                   "frames_per_chunk": per_chunk, "total_frames": total,
                   "slot_size": slot, "width": w, "height": h,
                   "reserved": chunk[0x20:0x28].hex()}
            frames = []
            for k in range(in_chunk):
                s = _FRAME_DATA_OFFSET + k * slot
                if s + slot > len(chunk):
                    break
                fw, fh = struct.unpack_from("<HH", chunk, s)
                frames.append((fw, fh, chunk[s + 4:s + slot]))
            yield stream, pos, hdr, frames


def process_mis(path, out_dir):
    with open(path, "rb") as f:
        data = f.read()
    anims = {}
    order = []
    for stream, pos, hdr, frames in iter_canm(data):
        key = stream or "canm_%06x" % pos
        if key not in anims:
            anims[key] = {"stream": stream, "chunks": [], "images": []}
            order.append(key)
        anims[key]["chunks"].append(dict(hdr, file_offset=hex(pos)))
        for fw, fh, bs in frames:
            img, info = decode_bs_image(bs, fw, fh)
            anims[key]["images"].append(img)
    if not order:
        return 0
    anim_dir = os.path.join(out_dir, "anims")
    os.makedirs(anim_dir, exist_ok=True)
    meta = []
    for key in order:
        imgs = anims[key]["images"]
        w, h = imgs[0].size
        strip = Image.new("RGB", (w, h * len(imgs)))
        for i, im in enumerate(imgs):
            strip.paste(im, (0, i * h))
        strip.save(os.path.join(anim_dir, key + ".png"))
        imgs[0].save(os.path.join(anim_dir, key + ".gif"), save_all=True,
                     append_images=imgs[1:], duration=int(1000 / _PREVIEW_FPS), loop=0)
        meta.append({"name": key, "voice_stream": anims[key]["stream"],
                     "width": w, "height": h, "frames": len(imgs), "fps": _PREVIEW_FPS,
                     "strip": key + ".png", "chunks": anims[key]["chunks"]})
        print(f"  anims/{key}.png  {len(imgs)} frames {w}x{h}")
    with open(os.path.join(anim_dir, "anims.json"), "w") as f:
        json.dump(meta, f, indent=1)
    return len(order)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    parsed = os.path.join(repo_root, "parsed")
    mis_files = find_game_files("MIS")
    total = 0
    for path in mis_files:
        level = os.path.splitext(os.path.basename(path))[0]
        print(f"\n{level}.MIS -> parsed/{level}/anims/")
        n = process_mis(path, os.path.join(parsed, level))
        if not n:
            print("  (no CANM chunks)")
        total += n
    print(f"\nDone - {total} animations across {len(mis_files)} levels.")


if __name__ == "__main__":
    main()
