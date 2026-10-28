#!/usr/bin/env python3
"""Export a terrain heightmap lookup table from a Future Cop MIS archive.

Output: parsed/<LEVEL>/heightmap.json
  {
    "grid_w": 14, "grid_h": 16,
    "tiles": {
      "<row>,<col>": [[ch0, ch1, ch2], ...]  // 17x17 signed bytes, row-major
    }
  }

Height at world (wx, wz):
  col = floor((wx + 8.5) / 16) + grid_w/2
  row = floor((wz + 8.5) / 16) + grid_h/2
  ty  = wx + 8.5 - (col - grid_w/2) * 16   (local X, 0-16)
  tx  = wz + 8.5 - (row - grid_h/2) * 16   (local Z, 0-16)
  world_y = bilinear(tile[tx][ty][0]) / 32.0
"""

import json, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import iter_iff_resources, find_game_files
from extract_terrain import _inner_tags, _TAG_SECT, _parse_cptc

def export_heightmap(mis_path, out_dir):
    with open(mis_path, 'rb') as f:
        data = f.read()

    result = _parse_cptc(data)
    if not result:
        print(f"  no Cptc in {mis_path}")
        return False
    grid_w, grid_h, grid = result

    ctil_hm = {}
    for rt, rid, raw in iter_iff_resources(data):
        if rt != 'Ctil':
            continue
        tags = _inner_tags(raw)
        if _TAG_SECT not in tags:
            continue
        sect = tags[_TAG_SECT]
        p = 4  # skip color_amount + tc_amount u16s
        rows = []
        for _y in range(17):
            row = []
            for _x in range(17):
                c0, c1, c2 = struct.unpack_from('bbb', sect, p); p += 3
                row.append([c0, c1, c2])
            rows.append(row)
        ctil_hm[rid] = rows

    tiles = {}
    for row in range(grid_h):
        for col in range(grid_w):
            cid = grid[row][col]
            if cid == 0 or cid not in ctil_hm:
                continue
            tiles[f"{row},{col}"] = ctil_hm[cid]

    out = {"grid_w": grid_w, "grid_h": grid_h, "tiles": tiles}
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "heightmap.json")
    with open(out_path, 'w') as f:
        json.dump(out, f, separators=(',', ':'))
    print(f"  heightmap.json  ({len(tiles)} tiles, grid {grid_w}x{grid_h})")
    return True


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root  = os.path.dirname(script_dir)
    parsed     = os.path.join(repo_root, 'parsed')

    mis_files = find_game_files("MIS")

    for mis_path in mis_files:
        level   = os.path.splitext(os.path.basename(mis_path))[0]
        out_dir = os.path.join(parsed, level)
        export_heightmap(mis_path, out_dir)
        print(f"{level}.MIS → heightmap.json")


if __name__ == '__main__':
    main()
