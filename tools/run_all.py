#!/usr/bin/env python3
"""Run the whole extraction pipeline in dependency order, then sync godot/assets/.

Order matters: objects, terrain and glTF reference the textures extract_visuals.py
writes; convert_fonts.py needs the extracted Cfnt atlases; extract_tos.py checks the
voices extract_sfx.py writes.

Usage: run_all.py [--no-videos] [--from SCRIPT]
  --no-videos   skip extract_videos.py (slow; needs java + ffmpeg with libtheora)
  --from NAME   start at that step, e.g. --from decode_actors
Each step runs as `python -I tools/<step>.py` with this interpreter; the first
failure stops the run and its output is shown.
"""

import os
import subprocess
import sys
import time

STEPS = [
    "extract_visuals", "extract_objects", "extract_terrain", "export_heightmap",
    "extract_actors", "extract_sfx", "convert_fonts", "export_gltf", "decode_actors",
    "extract_nets", "extract_rpns", "extract_fun", "extract_shd", "extract_tos",
    "extract_dcs", "extract_sfx_meta", "extract_frontend", "extract_canm",
    "extract_lsc", "extract_demos", "extract_videos", "sync_godot_assets",
]


def main():
    args = sys.argv[1:]
    steps = list(STEPS)
    if "--no-videos" in args:
        steps.remove("extract_videos")
    if "--from" in args:
        start = args[args.index("--from") + 1]
        if start not in steps:
            sys.exit(f"unknown step {start!r}; steps: {', '.join(steps)}")
        steps = steps[steps.index(start):]

    tools = os.path.dirname(os.path.abspath(__file__))
    t_all = time.time()
    for step in steps:
        t = time.time()
        res = subprocess.run([sys.executable, "-I", os.path.join(tools, step + ".py")],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        print(f"{step:20s} {'ok ' if res.returncode == 0 else 'FAILED'} {time.time() - t:6.1f}s")
        if res.returncode != 0:
            print(res.stdout[-4000:])
            sys.exit(res.returncode)
    print(f"\nDone in {time.time() - t_all:.0f}s.")


if __name__ == "__main__":
    main()
