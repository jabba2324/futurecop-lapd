#!/usr/bin/env python3
"""Extract Ctil terrain chunks from Future Cop: L.A.P.D. MIS archives.

Each Ctil resource is a 16×16 tile map chunk with a 17×17 heightmap.
The three-channel heightmap (R/G/B bytes) stores floor and wall heights:
  RED   (ch 0) — primary floor surface
  GREEN (ch 1) — mid level (ramps, wall mid-points)
  BLUE  (ch 2) — upper level

Output: terrain/ctilNNN.obj + ctilNNN.mtl per resource.
Textures reference ../textures/cbmpNNN.png (already extracted).

Coordinate mapping (from fc-mit TilResource::createPartial):
  world_x = heightmap_row - 8.5
  world_y = channel_byte / 32.0
  world_z = heightmap_col - 8.5
Each chunk spans ±8 units in xz; chunks tile together to form the full map.
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import iter_iff_resources, find_game_files

# ── TileSet constants ──────────────────────────────────────────────────────
FL, FR, BL, BR = 0, 1, 2, 3          # facing_direction
RED, GREEN, BLUE, NE = 0, 1, 2, 3    # heightmap channel; NE = NO_ELEMENT

# 128 polygon entries from fc-mit Til/TileSet.cpp (BahKooJ's MeshData.txt).
# Format: ([(fd, ch), ...], is_opposite)
# 3 points → triangle; 4th point with ch==NE still counts as triangle (last ignored).
# 4 valid points → quad, split into 2 triangles via INDEX_TABLE in BuildQuad.
_TILESET = [
    # 0-11: flat triangles on a single channel
    ([(BL,RED),(BR,RED),(FR,RED)],                  False),  # 0
    ([(FL,RED),(BL,RED),(FR,RED)],                  False),  # 1
    ([(BL,GREEN),(BR,GREEN),(FR,GREEN)],             False),  # 2
    ([(FL,GREEN),(BL,GREEN),(FR,GREEN)],             False),  # 3
    ([(BL,BLUE),(BR,BLUE),(FR,BLUE)],               False),  # 4
    ([(FL,BLUE),(BL,BLUE),(FR,BLUE)],               False),  # 5
    ([(FL,RED),(BR,RED),(FR,RED)],                  False),  # 6
    ([(FL,RED),(BL,RED),(BR,RED)],                  False),  # 7
    ([(FL,GREEN),(BR,GREEN),(FR,GREEN)],             False),  # 8
    ([(FL,GREEN),(BL,GREEN),(BR,GREEN)],             False),  # 9
    ([(FL,BLUE),(BR,BLUE),(FR,BLUE)],               False),  # 10
    ([(FL,BLUE),(BL,BLUE),(BR,BLUE)],               False),  # 11
    # 12-59: slope/mixed-channel triangles
    ([(FL,GREEN),(BR,RED),(FR,RED)],                False),  # 12
    ([(FL,GREEN),(BL,GREEN),(BR,RED)],              False),  # 13
    ([(FL,BLUE),(BR,GREEN),(FR,GREEN)],             False),  # 14
    ([(FL,BLUE),(BL,BLUE),(BR,GREEN)],              False),  # 15
    ([(BL,GREEN),(BR,RED),(FR,RED)],                False),  # 16
    ([(FL,GREEN),(BL,GREEN),(FR,RED)],              False),  # 17
    ([(BL,BLUE),(BR,GREEN),(FR,GREEN)],             False),  # 18
    ([(FL,BLUE),(BL,BLUE),(FR,GREEN)],              False),  # 19
    ([(FL,RED),(BL,RED),(BR,GREEN)],                False),  # 20
    ([(FL,RED),(BR,GREEN),(FR,GREEN)],              False),  # 21
    ([(FL,GREEN),(BL,GREEN),(BR,BLUE)],             False),  # 22
    ([(FL,GREEN),(BR,BLUE),(FR,BLUE)],              False),  # 23
    ([(FL,RED),(BL,RED),(FR,GREEN)],                False),  # 24
    ([(BL,RED),(BR,GREEN),(FR,GREEN)],              False),  # 25
    ([(FL,GREEN),(BL,GREEN),(FR,BLUE)],             False),  # 26
    ([(BL,GREEN),(BR,BLUE),(FR,BLUE)],              False),  # 27
    ([(FL,RED),(BR,GREEN),(FR,RED)],                False),  # 28
    ([(FL,RED),(BL,GREEN),(BR,GREEN)],              False),  # 29
    ([(FL,GREEN),(BR,BLUE),(FR,GREEN)],             False),  # 30
    ([(FL,GREEN),(BL,BLUE),(BR,BLUE)],              False),  # 31
    ([(FL,RED),(BL,GREEN),(FR,RED)],                False),  # 32
    ([(BL,GREEN),(BR,GREEN),(FR,RED)],              False),  # 33
    ([(FL,GREEN),(BL,BLUE),(FR,GREEN)],             False),  # 34
    ([(BL,BLUE),(BR,BLUE),(FR,GREEN)],              False),  # 35
    ([(BL,RED),(BR,RED),(FR,GREEN)],                False),  # 36
    ([(FL,GREEN),(BL,RED),(FR,GREEN)],              False),  # 37
    ([(BL,GREEN),(BR,GREEN),(FR,BLUE)],             False),  # 38
    ([(FL,BLUE),(BL,GREEN),(FR,BLUE)],              False),  # 39
    ([(FL,GREEN),(BL,RED),(BR,RED)],                False),  # 40
    ([(FL,GREEN),(BR,RED),(FR,GREEN)],              False),  # 41
    ([(FL,BLUE),(BL,GREEN),(BR,GREEN)],             False),  # 42
    ([(FL,BLUE),(BR,GREEN),(FR,BLUE)],              False),  # 43
    ([(BL,GREEN),(BR,RED),(FR,GREEN)],              False),  # 44
    ([(FL,RED),(BL,GREEN),(FR,GREEN)],              False),  # 45
    ([(BL,BLUE),(BR,GREEN),(FR,BLUE)],              False),  # 46
    ([(FL,GREEN),(BL,BLUE),(FR,BLUE)],              False),  # 47
    ([(FL,GREEN),(BR,GREEN),(FR,RED)],              False),  # 48
    ([(FL,GREEN),(BL,RED),(BR,GREEN)],              False),  # 49
    ([(FL,BLUE),(BR,BLUE),(FR,GREEN)],              False),  # 50
    ([(FL,BLUE),(BL,GREEN),(BR,BLUE)],              False),  # 51
    ([(FL,GREEN),(BL,RED),(FR,RED)],                False),  # 52
    ([(BL,RED),(BR,GREEN),(FR,RED)],                False),  # 53
    ([(FL,BLUE),(BL,GREEN),(FR,GREEN)],             False),  # 54
    ([(BL,GREEN),(BR,BLUE),(FR,GREEN)],             False),  # 55
    ([(FL,RED),(BR,RED),(FR,GREEN)],                False),  # 56
    ([(FL,RED),(BL,GREEN),(BR,RED)],                False),  # 57
    ([(FL,GREEN),(BR,GREEN),(FR,BLUE)],             False),  # 58
    ([(FL,GREEN),(BL,BLUE),(BR,GREEN)],             False),  # 59
    # 60-70: quads (all 4 points valid)
    ([(FL,GREEN),(BL,GREEN),(BR,RED),(FR,RED)],     False),  # 60
    ([(FL,BLUE),(BL,BLUE),(BR,GREEN),(FR,GREEN)],   False),  # 61
    ([(FL,RED),(BL,RED),(BR,GREEN),(FR,GREEN)],     False),  # 62
    ([(FL,GREEN),(BL,GREEN),(BR,BLUE),(FR,BLUE)],   False),  # 63
    ([(FL,RED),(BL,GREEN),(BR,GREEN),(FR,RED)],     False),  # 64
    ([(FL,GREEN),(BL,BLUE),(BR,BLUE),(FR,GREEN)],   False),  # 65
    ([(FL,GREEN),(BL,RED),(BR,RED),(FR,GREEN)],     False),  # 66
    ([(FL,BLUE),(BL,GREEN),(BR,GREEN),(FR,BLUE)],   False),  # 67
    ([(FL,RED),(BL,RED),(BR,RED),(FR,RED)],         False),  # 68 flat floor (red)
    ([(FL,GREEN),(BL,GREEN),(BR,GREEN),(FR,GREEN)], False),  # 69 flat floor (green)
    ([(FL,BLUE),(BL,BLUE),(BR,BLUE),(FR,BLUE)],     False),  # 70 flat floor (blue)
    # 71-78: front-face walls (is_opposite=True)
    ([(FL,RED),(FL,GREEN),(FR,RED)],                True),   # 71
    ([(FL,GREEN),(FR,GREEN),(FR,RED)],              True),   # 72
    ([(FL,GREEN),(FL,BLUE),(FR,GREEN)],             True),   # 73
    ([(FL,BLUE),(FR,BLUE),(FR,GREEN)],              True),   # 74
    ([(FL,RED),(FR,GREEN),(FR,RED)],                True),   # 75
    ([(FL,RED),(FL,GREEN),(FR,GREEN)],              True),   # 76
    ([(FL,GREEN),(FR,BLUE),(FR,GREEN)],             True),   # 77
    ([(FL,GREEN),(FL,BLUE),(FR,BLUE)],              True),   # 78
    # 79-86: left-edge walls (is_opposite=False)
    ([(FL,RED),(BL,GREEN),(BL,RED)],                False),  # 79
    ([(FL,RED),(FL,GREEN),(BL,GREEN)],              False),  # 80
    ([(FL,GREEN),(BL,BLUE),(BL,GREEN)],             False),  # 81
    ([(FL,GREEN),(FL,BLUE),(BL,BLUE)],              False),  # 82
    ([(FL,RED),(FL,GREEN),(BL,RED)],                False),  # 83
    ([(FL,GREEN),(BL,GREEN),(BL,RED)],              False),  # 84
    ([(FL,GREEN),(FL,BLUE),(BL,GREEN)],             False),  # 85
    ([(FL,BLUE),(BL,BLUE),(BL,GREEN)],              False),  # 86
    # 87-94: diagonal/corner walls
    ([(FR,RED),(BL,RED),(BL,GREEN)],                True),   # 87
    ([(FR,RED),(BL,GREEN),(FR,GREEN)],              False),  # 88
    ([(FR,GREEN),(BL,GREEN),(BL,BLUE)],             True),   # 89
    ([(FR,GREEN),(BL,BLUE),(FR,BLUE)],              True),   # 90
    ([(FR,RED),(BL,RED),(FR,GREEN)],                True),   # 91
    ([(FR,GREEN),(BL,RED),(BL,GREEN)],              False),  # 92
    ([(FR,GREEN),(BL,GREEN),(FR,BLUE)],             False),  # 93
    ([(FR,BLUE),(BL,GREEN),(BL,BLUE)],              False),  # 94
    # 95-102: right-edge walls
    ([(FL,RED),(BR,GREEN),(BR,RED)],                False),  # 95
    ([(FL,RED),(FL,GREEN),(BR,GREEN)],              False),  # 96
    ([(FL,GREEN),(BR,BLUE),(BR,GREEN)],             False),  # 97
    ([(FL,GREEN),(FL,BLUE),(BR,BLUE)],              False),  # 98
    ([(FL,RED),(FL,GREEN),(BR,RED)],                False),  # 99
    ([(FL,GREEN),(BR,GREEN),(BR,RED)],              False),  # 100
    ([(FL,GREEN),(FL,BLUE),(BR,GREEN)],             False),  # 101
    ([(FL,BLUE),(BR,BLUE),(BR,GREEN)],              False),  # 102
    # 103-110: quad walls
    ([(FR,RED),(FR,GREEN),(BL,GREEN),(BL,RED)],     False),  # 103
    ([(FR,GREEN),(FR,BLUE),(BL,BLUE),(BL,GREEN)],   False),  # 104
    ([(FL,RED),(FL,GREEN),(BR,GREEN),(BR,RED)],     False),  # 105
    ([(FL,GREEN),(FL,BLUE),(BR,BLUE),(BR,GREEN)],   False),  # 106
    ([(FL,RED),(FL,GREEN),(FR,GREEN),(FR,RED)],     True),   # 107
    ([(FL,RED),(FL,GREEN),(BL,GREEN),(BL,RED)],     False),  # 108
    ([(FL,GREEN),(FL,BLUE),(FR,BLUE),(FR,GREEN)],   True),   # 109
    ([(FL,GREEN),(FL,BLUE),(BL,BLUE),(BL,GREEN)],   False),  # 110
    # 111-127: empty tiles
] + [None] * 17  # types 111-127 are empty

_TAG_SECT = 0x53656374  # "Sect" stored as "tceS" LE
_SAMPLE_H = 1.0 / 32.0  # height scale: signed byte / 32


def _inner_tags(raw):
    tags = {}
    p = 0
    while p + 8 <= len(raw):
        tag = struct.unpack_from("<I", raw, p)[0]
        sz  = struct.unpack_from("<I", raw, p + 4)[0]
        if sz < 8 or p + sz > len(raw):
            break
        tags[tag] = raw[p + 8 : p + sz]
        p += sz
    return tags


def _vertex_pos(tx, ty, fd, ch, heightmap):
    """World position for a tile vertex.

    tx, ty    — tile grid coordinates (0-15)
    fd        — facing direction (FL/FR/BL/BR = 0-3)
    ch        — heightmap channel (RED/GREEN/BLUE = 0-2)
    heightmap — list[17][17] of (ch0, ch1, ch2) signed tuples
    """
    px = tx + (fd & 1)          # heightmap column: tx or tx+1
    py = ty + ((fd >> 1) & 1)   # heightmap row:    ty or ty+1
    world_x = py - 8.5
    world_y = heightmap[px][py][ch] * _SAMPLE_H
    world_z = px - 8.5
    return (world_x, world_y, world_z)


def extract_ctil(mis_path, out_dir):
    with open(mis_path, "rb") as f:
        data = f.read()

    terrain_dir = os.path.join(out_dir, "terrain")
    os.makedirs(terrain_dir, exist_ok=True)

    max_cbmp_id = max((res_id for rt, res_id, _ in iter_iff_resources(data) if rt == "Cbmp"), default=0)

    count = 0
    for res_type, res_id, raw in iter_iff_resources(data):
        if res_type != "Ctil":
            continue

        tags = _inner_tags(raw)
        if _TAG_SECT not in tags:
            continue

        sect = tags[_TAG_SECT]
        tris = _tris_from_sect(sect, _slfx_light(tags))

        if not tris:
            continue

        # ── Deduplicate & write OBJ ───────────────────────────────────────
        verts_list, vert_idx = [], {}
        uv_list, uv_idx = [], {}

        def _vi(v):
            if v not in vert_idx:
                vert_idx[v] = len(verts_list) + 1
                verts_list.append(v)
            return vert_idx[v]

        def _ti(uv):
            if uv not in uv_idx:
                uv_idx[uv] = len(uv_list) + 1
                uv_list.append(uv)
            return uv_idx[uv]

        # key = (wx, wy, wz) + (r, g, b) so vertices at same position but different
        # shading produce separate OBJ vertex entries (different vertex colors).
        tri_objs = [(_vi(t[0] + t[7]), _vi(t[1] + t[8]), _vi(t[2] + t[9]),
                     _ti(t[3]), _ti(t[4]), _ti(t[5]),
                     min(t[6], max_cbmp_id) if max_cbmp_id else t[6]) for t in tris]

        used_bmps = sorted(set(t[6] for t in tri_objs))

        stem     = f"ctil{res_id:03d}"
        obj_path = os.path.join(terrain_dir, f"{stem}.obj")
        mtl_path = os.path.join(terrain_dir, f"{stem}.mtl")

        with open(mtl_path, "w") as mf:
            for cbid in used_bmps:
                mf.write(f"newmtl cbmp{cbid:03d}\n")
                mf.write(f"map_Kd ../textures/cbmp{cbid:03d}.png\n\n")

        with open(obj_path, "w") as of:
            of.write(f"# Ctil {res_id} from {os.path.basename(mis_path)}\n")
            of.write(f"# Vertex colours (v r g b) encode PS1 shading; multiply texture\n")
            of.write(f"# by vertex colour in Blender to reproduce the original lighting.\n")
            of.write(f"mtllib {stem}.mtl\n")
            for v in verts_list:
                of.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f} {v[3]:.4f} {v[4]:.4f} {v[5]:.4f}\n")
            for u, v in uv_list:
                of.write(f"vt {u/255.0:.6f} {1.0 - v/255.0:.6f}\n")
            current_mtl = None
            for vi0, vi1, vi2, ti0, ti1, ti2, cbid in sorted(tri_objs, key=lambda t: t[6]):
                mtl = f"cbmp{cbid:03d}"
                if mtl != current_mtl:
                    of.write(f"usemtl {mtl}\n")
                    current_mtl = mtl
                of.write(f"f {vi0}/{ti0} {vi1}/{ti1} {vi2}/{ti2}\n")

        print(f"  terrain/{stem}.obj  ({len(tris)} tris, {len(verts_list)} verts)")
        count += 1

    return count


_GRDB_TAG = 0x47524442  # "GRDB"


def _parse_cptc(data):
    """Return (grid_width, grid_height, grid) from the Cptc resource.

    grid[row][col] = ctil_resource_id  (0 = empty cell)
    World offset for cell (col, row): x = (col - W/2)*16, z = (row - H/2)*16
    """
    for res_type, res_id, raw in iter_iff_resources(data):
        if res_type != "Cptc":
            continue
        if len(raw) < 8:
            continue
        tag = struct.unpack_from("<I", raw, 0)[0]
        sz  = struct.unpack_from("<I", raw, 4)[0]
        if tag != _GRDB_TAG or sz > len(raw):
            continue
        payload = raw[8:sz]
        # offset 4: tile_amount, 8: width, 12: height, 36: grid values
        width  = struct.unpack_from("<I", payload, 8)[0]
        height = struct.unpack_from("<I", payload, 12)[0]
        grid = []
        pp = 36
        for _r in range(height):
            row = []
            for _c in range(width):
                row.append(struct.unpack_from("<I", payload, pp)[0] // 4)
                pp += 4
            grid.append(row)
        return width, height, grid
    return None


def write_map(mis_path, out_dir):
    """Write terrain/map.obj — all Ctil chunks stitched with correct world offsets."""
    with open(mis_path, "rb") as f:
        data = f.read()

    result = _parse_cptc(data)
    if result is None:
        return False
    grid_w, grid_h, grid = result

    # Collect all Ctil payloads indexed by resource ID, and the highest Cbmp ID present.
    ctil_raw = {}
    max_cbmp_id = 0
    for res_type, res_id, raw in iter_iff_resources(data):
        if res_type == "Ctil":
            ctil_raw[res_id] = raw
        elif res_type == "Cbmp":
            max_cbmp_id = max(max_cbmp_id, res_id)

    terrain_dir = os.path.join(out_dir, "terrain")
    os.makedirs(terrain_dir, exist_ok=True)

    # Accumulate triangles from every grid cell
    all_tris = []   # (v0, v1, v2, uv0, uv1, uv2, cbmp_id)
    used_bmps = set()
    border_tile_id = None  # kept for header comment only

    for row in range(grid_h):
        for col in range(grid_w):
            ctil_id = grid[row][col]
            if ctil_id == 0 or ctil_id not in ctil_raw:
                continue

            # World offset for this grid cell (from PTCResource::writeEntireMap)
            wx_off = (col - grid_w / 2.0) * 16.0
            wz_off = (row - grid_h / 2.0) * 16.0

            raw = ctil_raw[ctil_id]
            tags = _inner_tags(raw)
            if _TAG_SECT not in tags:
                continue

            tris = _tris_from_sect(tags[_TAG_SECT], _slfx_light(tags))
            for t in tris:
                v0, v1, v2 = t[0], t[1], t[2]
                cbmp_id = min(t[6], max_cbmp_id) if max_cbmp_id else t[6]
                # Apply world offset: x shifts by wx_off, z shifts by wz_off
                all_tris.append((
                    (v0[0] + wx_off, v0[1], v0[2] + wz_off),
                    (v1[0] + wx_off, v1[1], v1[2] + wz_off),
                    (v2[0] + wx_off, v2[1], v2[2] + wz_off),
                    t[3], t[4], t[5], cbmp_id,
                    t[7], t[8], t[9], t[10], t[11],
                ))
                used_bmps.add(cbmp_id)

    if not all_tris:
        return False

    # Deduplicate vertices and UVs
    verts_list, vert_idx = [], {}
    uv_list, uv_idx = [], {}

    def _vi(v):
        if v not in vert_idx:
            vert_idx[v] = len(verts_list) + 1
            verts_list.append(v)
        return vert_idx[v]

    def _ti(uv):
        if uv not in uv_idx:
            uv_idx[uv] = len(uv_list) + 1
            uv_list.append(uv)
        return uv_idx[uv]

    # key = position + vertex-color so shading differences create separate vertices
    # Material names: cbmpNNN, then "_mix" for semi-transparent polygons (the game's
    # 50/50 blend), "_s<u>_<v>" for UV-scrolling ones (the chunk's scroll speeds),
    # "_see" for polygons the camera's line of sight passes (surface bit 0x80) and
    # "_pass" (always last) for floors the game walks through, which the Godot
    # collision leaves out (terrain_collision.gd).
    tri_objs = [(_vi(t[0] + t[7]), _vi(t[1] + t[8]), _vi(t[2] + t[9]),
                 _ti(t[3]), _ti(t[4]), _ti(t[5]),
                 f"cbmp{t[6]:03d}" + ("_mix" if t[11][0] else "")
                 + (f"_s{t[11][1][0]}_{t[11][1][1]}" if t[11][1] else "")
                 + ("_see" if t[10][0] & _SEE_THROUGH else "")
                 + ("_pass" if _passes_through(t[10]) else "")) for t in all_tris]
    mtls = sorted(set(t[6] for t in tri_objs))

    _write_surfaces(all_tris, os.path.join(terrain_dir, "surfaces.json"))

    map_obj = os.path.join(terrain_dir, "map.obj")
    map_mtl = os.path.join(terrain_dir, "map.mtl")

    with open(map_mtl, "w") as mf:
        for mtl in mtls:
            mf.write(f"newmtl {mtl}\n")
            mf.write(f"map_Kd ../textures/{mtl[:7]}.png\n\n")

    with open(map_obj, "w") as of:
        content_ids = set(grid[r][c] for r in range(grid_h) for c in range(grid_w)
                          if grid[r][c] not in (0, border_tile_id))
        of.write(f"# Stitched terrain from {os.path.basename(mis_path)}\n")
        of.write(f"# Grid {grid_w}×{grid_h}, {len(content_ids)} content chunks (border tile {border_tile_id} excluded)\n")
        of.write(f"# Vertex colours (v r g b) encode PS1 shading; multiply texture\n")
        of.write(f"# by vertex colour in Blender to reproduce the original lighting.\n")
        of.write("mtllib map.mtl\n")
        for v in verts_list:
            of.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f} {v[3]:.4f} {v[4]:.4f} {v[5]:.4f}\n")
        for u, v in uv_list:
            of.write(f"vt {u/255.0:.6f} {1.0 - v/255.0:.6f}\n")
        current_mtl = None
        for vi0, vi1, vi2, ti0, ti1, ti2, mtl in sorted(tri_objs, key=lambda t: t[6]):
            if mtl != current_mtl:
                of.write(f"usemtl {mtl}\n")
                current_mtl = mtl
            of.write(f"f {vi0}/{ti0} {vi1}/{ti1} {vi2}/{ti2}\n")

    print(f"  terrain/map.obj  ({len(all_tris)} tris, {len(verts_list)} verts, grid {grid_w}×{grid_h})")
    return True


def _write_surfaces(all_tris, path):
    """surfaces.json: the floor triangles whose surface byte does something to the
    player (low six bits nonzero), in map.obj coordinates:
      {"tris": [[action, x0, y0, z0, x1, y1, z1, x2, y2, z2], ...]}"""
    out = []
    for t in all_tris:
        action, _index, is_floor = t[10]
        if is_floor and action & 0x3F and not _passes_through(t[10]):
            out.append([action] + [round(c, 4) for v in t[:3] for c in v])
    with open(path, "w") as f:
        json.dump({"tris": out}, f, separators=(",", ":"))
    print(f"  surfaces.json  ({len(out)} special floor tris)")


# Surface byte (polygon_action_types), decoded from SLUS_007.39 (ground probe
# 0x80066f78, handler 0x80053188). 0x80: camera sees through it (no physics);
# 0x40: a floor the ground probes ignore (unless its action index is 0); the low six
# bits are an enumeration, see godot/scripts/surfaces.gd.
_FIRST_WALL = 0x47      # mesh types >= 0x47 are walls
_PASS_THROUGH = 0x40
_SEE_THROUGH = 0x80   # surface bit: the camera sees through it (0x80055ee8)


def _passes_through(surface):
    action, index, is_floor = surface
    return is_floor and index != 0 and action & _PASS_THROUGH


_TAG_SLFX = 0x534C4658


def _slfx_light(tags):
    """PS1 colour (128 = x1) of the chunk's type-3 tiles, from its SLFX tag.

    The game rebuilds a 16x16 per-cell light table from the SLFX word every frame
    (0x800654f8) and type-3 tiles (water) take their corner colours from it
    (0x800648e8). Noise mode (w >> 28 == 1): each cell runs a triangle wave
    0..p..0, p random 64..127, light = (bright << k + wave) >> k with bright =
    (w >> 8) & 0xff, k = w & 3; this returns its mean. Wave mode: background
    (w >> 8) & 0xff plus a sine of about +-1. No SLFX: 128.
    """
    s = tags.get(_TAG_SLFX)
    if not s:
        return 0x80
    w = struct.unpack_from("<I", s, 0)[0]
    if w >> 28 == 1:
        return ((w >> 8) & 0xFF) + (48 >> (w & 3))
    return (w >> 8) & 0xFF


def _tris_from_sect(sect, slfx_light=0x80):
    """Extract triangle list from a parsed SECT payload (shared by extract_ctil and write_map).

    Each triangle is a 12-tuple:
      (v0, v1, v2, uv0, uv1, uv2, cbmp_id, vc0, vc1, vc2, surface, blend)
    where vN = (wx, wy, wz), uvN = (u, v), vcN = (r, g, b) vertex-color in [0, 1] and
    surface = (action byte, action index, is_floor): the tile's polygon_action_types
    entry (see SURFACES below), the 2-bit index that picked it, and mesh type < 0x47.
    blend = (semi, scroll): tile graphics bit 12 (semi-transparent, the GPU's ABR 0
    50/50 blend) and, when bit 11 is set, the chunk's (u, v) scroll speeds (else None).

    Vertex colors encode the original PS1 shading so Blender can reproduce the lit
    appearance with: Base Color = texture × vertex_color (no extra ×2 needed, since the
    OBJ values are pre-scaled: vc = min(shading / 64.0, 1.0) for solid-mono tiles).
    """
    p = 0
    color_amount = struct.unpack_from("<H", sect, p)[0]; p += 2
    tc_amount    = struct.unpack_from("<H", sect, p)[0]; p += 2

    heightmap = []
    for _y in range(17):
        row = []
        for _x in range(17):
            c0, c1, c2 = struct.unpack_from("bbb", sect, p); p += 3
            row.append((c0, c1, c2))
        heightmap.append(row)

    p += 1
    action_types = sect[p:p + 4]   # polygon_action_types: surface byte per action index
    # The two signed bytes after the 84 are the chunk's UV scroll speeds (0x80065744):
    # offset = (s * clock(300 Hz) >> 6) % 28 texels, u and v.
    uv_scroll = struct.unpack_from("bb", sect, p + 4 + 84)
    p += 4 + 84 + 2
    mesh_lib_size = struct.unpack_from("<H", sect, p)[0]; p += 2

    mesh_ref = []
    for _x in range(16):
        col = []
        for _y in range(16):
            bf = struct.unpack_from("<H", sect, p)[0]; p += 2
            col.append((bf & 0x3F, (bf >> 6) & 0x3FF))
        mesh_ref.append(col)

    p += 4
    mesh_tiles = []
    for _ in range(mesh_lib_size):
        bf = struct.unpack_from("<I", sect, p)[0]; p += 4
        mesh_tiles.append(((bf >> 1) & 0x3FF, (bf >> 15) & 0x7F, (bf >> 22) & 0x3FF, (bf >> 13) & 3))

    texture_cords = []
    for _ in range(tc_amount):
        texture_cords.append((sect[p], sect[p + 1])); p += 2

    # Parse Dynamic Color vertex-color palette (Colorizer.cpp channel order:
    # B=bits[0-4], G=bits[5-9], R=bits[10-14], STP=bit15 doubles brightness).
    # Effective shader multiplier = 4 × colorizer_output → OBJ vc = val / (8 or 16).
    colors_arr = []
    for _ in range(color_amount):
        w = struct.unpack_from("<H", sect, p)[0]; p += 2
        b5 = w & 0x1f
        g5 = (w >>  5) & 0x1f
        r5 = (w >> 10) & 0x1f
        stp = (w >> 15) & 1
        div = 8.0 if stp else 16.0
        colors_arr.append((min(r5 / div, 1.0), min(g5 / div, 1.0), min(b5 / div, 1.0)))

    tg_bitfield = []
    while p + 2 <= len(sect):
        tg_bitfield.append(struct.unpack_from("<H", sect, p)[0]); p += 2

    _WHITE = (1.0, 1.0, 1.0)

    tris = []
    for tx in range(16):
        for ty in range(16):
            tile_amount, tiles_start = mesh_ref[tx][ty]
            for t in range(tile_amount):
                tile_idx = (tiles_start + t) % len(mesh_tiles)
                tc_idx, mesh_type, gfx_idx, act_idx = mesh_tiles[tile_idx]
                surface = (action_types[act_idx], act_idx, mesh_type < _FIRST_WALL)

                if mesh_type >= len(_TILESET) or _TILESET[mesh_type] is None:
                    continue
                pts, is_opp = _TILESET[mesh_type]
                if not pts:
                    continue

                tex_idx = 0
                vc = [_WHITE] * 4  # per-corner (FL=0, FR=1, BL=2, BR=3)
                if gfx_idx < len(tg_bitfield):
                    bf      = tg_bitfield[gfx_idx]
                    tex_idx = (bf >>  8) & 0x7
                    shading = (bf >>  0) & 0xFF
                    typ     = (bf >> 14) & 0x3
                    rect    = (bf >> 13) & 0x1

                    if typ == 0:   # Solid Mono: uniform brightness
                        f = min(shading / 64.0, 1.0)
                        vc = [(f, f, f)] * 4

                    elif typ == 3:   # SLFX light table (water); shading only picks the layout
                        f = min(slfx_light / 256.0, 1.0)
                        vc = [(f, f, f)] * 4

                    elif typ == 1:   # Dynamic Mono: per-vertex 6-bit brightness
                        b0 = min(((shading & 0xFC) >> 2) / 16.0, 1.0)
                        vc[0] = (b0, b0, b0)
                        if gfx_idx + 1 < len(tg_bitfield):
                            sec          = tg_bitfield[gfx_idx + 1]
                            second_lower = (sec >> 12) & 0xF
                            third        = (sec >>  6) & 0x3F
                            forth        = (sec >>  0) & 0x3F
                            second = ((shading & 0x03) << 4) | second_lower
                            b1 = min(second / 16.0, 1.0)
                            b2 = min(third  / 16.0, 1.0)
                            b3 = min(forth  / 16.0, 1.0)
                            vc[1] = (b1, b1, b1)
                            vc[2] = (b2, b2, b2)
                            vc[3] = (b3, b3, b3)

                    elif typ == 2:   # Dynamic Color: vertex RGB from colors_arr
                        if shading < len(colors_arr):
                            vc[0] = colors_arr[shading]
                        if gfx_idx + 1 < len(tg_bitfield):
                            sec    = tg_bitfield[gfx_idx + 1]
                            second = (sec >> 8) & 0xFF
                            third  = sec & 0xFF
                            if second < len(colors_arr):
                                vc[1] = colors_arr[second]
                            if third < len(colors_arr):
                                vc[2] = colors_arr[third]
                            if rect and gfx_idx + 2 < len(tg_bitfield):
                                ter_sec = (tg_bitfield[gfx_idx + 2] >> 8) & 0xFF
                                if ter_sec < len(colors_arr):
                                    vc[3] = colors_arr[ter_sec]

                cbmp_id = tex_idx + 1
                blend = (False, None)
                if gfx_idx < len(tg_bitfield):
                    blend = (bool((bf >> 12) & 1), uv_scroll if (bf >> 11) & 1 else None)

                def _uv(off, _tc=tc_idx, _tc_list=texture_cords):
                    if not _tc_list:
                        return (0, 0)
                    return _tc_list[(_tc + off) % len(_tc_list)]

                def _v(pt, _tx=tx, _ty=ty, _hm=heightmap):
                    fd, ch = pt
                    return _vertex_pos(_tx, _ty, fd, ch, _hm)

                # fc-mit createPartial always uses front=True (reverse all vertices)
                # regardless of is_opposite, so all tiles use the same CCW winding.
                # is_opposite only affects the culled rendering path which we don't use.
                if len(pts) == 4:
                    tris.append((_v(pts[3]), _v(pts[1]), _v(pts[0]),
                                 _uv(3), _uv(1), _uv(0), cbmp_id,
                                 vc[3], vc[1], vc[0], surface, blend))
                    tris.append((_v(pts[3]), _v(pts[2]), _v(pts[1]),
                                 _uv(3), _uv(2), _uv(1), cbmp_id,
                                 vc[3], vc[2], vc[1], surface, blend))
                else:
                    tris.append((_v(pts[2]), _v(pts[1]), _v(pts[0]),
                                 _uv(2), _uv(1), _uv(0), cbmp_id,
                                 vc[2], vc[1], vc[0], surface, blend))
    return tris


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root  = os.path.dirname(script_dir)
    parsed     = os.path.join(repo_root, "parsed")

    mis_files = find_game_files("MIS")

    total = 0
    for mis_path in mis_files:
        level   = os.path.splitext(os.path.basename(mis_path))[0]
        out_dir = os.path.join(parsed, level)
        n = extract_ctil(mis_path, out_dir)
        if n:
            print(f"{level}.MIS → {n} terrain chunks")
        write_map(mis_path, out_dir)
        total += n

    print(f"\nDone — {total} terrain chunks extracted.")


if __name__ == "__main__":
    main()
