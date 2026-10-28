#!/usr/bin/env python3
"""Extract Cobj 3D object models from Future Cop: L.A.P.D. MIS archives.

Each Cobj resource yields one OBJ + MTL file pair in parsed/<LEVEL>/objects/.
Texture materials reference the already-extracted cbmpNNN.png files in
../textures/ (run extract_visuals.py first).

Cobj inner tags (little-endian uint32 tag + uint32 total_size):
  4DGI — header: u32(1), u16 num_frames, u8 id, u8 bitfield, ...
  3DTL — face-type list: u32(1), then variable-length entries:
           opcode 1 (4 bytes total): untextured vertex-colour face
           opcode 2/3 (16 bytes total): 4× u8vec2 UV coords + u32 bmp_id
  3DQL — primitive list: u32(1), u32 num_faces, then per face:
           u8 op0, u8 op1, u16 face_type_offset, u8[4] v, u8[4] n
           face type from op1 bits[2:0]: 3=triangle, 4=quad (5/7/0 skipped)
  4DVL — vertex positions: u32 frame_id, u32 count, count × i16[4] (w ignored)
           world pos = (-x, y, z) × (1/512)
  4DNL — vertex normals: same layout, (-x, y, z) then normalise
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import iter_iff_resources, find_game_files

# Cobj inner tag IDs (little-endian uint32; appear byte-reversed in file)
_TAG_3DTL = 0x3344544C  # face/texture type list
_TAG_3DQL = 0x3344514C  # primitive (face) list
_TAG_4DVL = 0x3444564C  # vertex positions
_TAG_4DNL = 0x34444E4C  # vertex normals

_FIXED    = 1.0 / 512.0          # fixed-point unit for vertex coords
_QUAD     = [[0, 1, 3], [2, 3, 1]]  # quad → two triangles (matches ObjResource QUAD_TABLE)


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


def _gen_placeholder_tris(verts, tris):
    """Append placeholder triangles for objects with no polygon geometry.

    LINE objects (2 verts): thin flat box along the segment.
    STAR / single-point objects: small axis-aligned cube at the vertex.
    Appends into `tris` using the same 10-tuple format as _add_tri.
    """
    def _box(cx, cy, cz, hx, hy, hz):
        """Return (verts_list, tri_indices) for a box centred at (cx,cy,cz)."""
        bv = [
            (cx - hx, cy - hy, cz - hz), (cx + hx, cy - hy, cz - hz),
            (cx + hx, cy + hy, cz - hz), (cx - hx, cy + hy, cz - hz),
            (cx - hx, cy - hy, cz + hz), (cx + hx, cy - hy, cz + hz),
            (cx + hx, cy + hy, cz + hz), (cx - hx, cy + hy, cz + hz),
        ]
        faces = [
            [0,1,2,3], [5,4,7,6], [4,0,3,7],
            [1,5,6,2], [4,5,1,0], [3,2,6,7],
        ]
        tris_idx = []
        for f in faces:
            tris_idx.append((f[0], f[1], f[2]))
            tris_idx.append((f[0], f[2], f[3]))
        return bv, tris_idx

    def _emit(bv, bt, base):
        for vi0, vi1, vi2 in bt:
            tris.append((base + vi0, base + vi1, base + vi2,
                         0, 0, 0, None, None, None, None))

    base = len(verts)

    if len(verts) >= 2:
        # LINE: thin box along the segment between first two vertices
        x0, y0, z0 = verts[0]
        x1, y1, z1 = verts[1]
        cx, cy, cz = (x0+x1)/2, (y0+y1)/2, (z0+z1)/2
        dx, dy, dz = x1-x0, y1-y0, z1-z0
        seg_len = max((dx*dx + dy*dy + dz*dz) ** 0.5, 1e-6)
        # half-extents: long axis = segment/2, cross = 2% of segment
        r = seg_len * 0.02
        # Find which axis the segment is most aligned to, build box accordingly
        if abs(dx) >= abs(dy) and abs(dx) >= abs(dz):
            bv, bt = _box(cx, cy, cz, seg_len/2, r, r)
        elif abs(dz) >= abs(dy):
            bv, bt = _box(cx, cy, cz, r, r, seg_len/2)
        else:
            bv, bt = _box(cx, cy, cz, r, seg_len/2, r)
        verts.extend(bv)
        _emit(bv, bt, base)
    else:
        # STAR: small cube at the single vertex
        x, y, z = verts[0]
        r = 0.01
        bv, bt = _box(x, y, z, r, r, r)
        verts.extend(bv)
        _emit(bv, bt, base)


def extract_cobj(mis_path, out_dir):
    with open(mis_path, "rb") as f:
        data = f.read()

    obj_dir = os.path.join(out_dir, "objects")
    os.makedirs(obj_dir, exist_ok=True)

    count = 0
    for res_type, res_id, raw in iter_iff_resources(data):
        if res_type != "Cobj":
            continue

        inner = _inner_tags(raw)

        # Vertices (frame 0 only — frame_id is in d[0:4], ignored here)
        if _TAG_4DVL not in inner:
            continue
        d = inner[_TAG_4DVL]
        num_verts = struct.unpack_from("<I", d, 4)[0]
        verts = []
        for i in range(num_verts):
            x, y, z, _ = struct.unpack_from("<4h", d, 8 + i * 8)
            verts.append((-x * _FIXED, y * _FIXED, z * _FIXED))

        # Normals
        normals = []
        if _TAG_4DNL in inner:
            d = inner[_TAG_4DNL]
            num_norms = struct.unpack_from("<I", d, 4)[0]
            for i in range(num_norms):
                x, y, z, _ = struct.unpack_from("<4h", d, 8 + i * 8)
                nx, ny, nz = -float(x), float(y), float(z)
                length = (nx*nx + ny*ny + nz*nz) ** 0.5
                if length > 0:
                    nx /= length; ny /= length; nz /= length
                else:
                    ny = 1.0
                normals.append((nx, ny, nz))

        # Face types (3DTL)
        # face_type_offset in 3DQL is a byte offset from after the leading u32 in 3DTL.
        # opcode 1: untextured (4 bytes)
        # opcode 2/3: 4× u8vec2 UV coords + u32 bmp_id (16 bytes)
        face_types = {}
        if _TAG_3DTL in inner:
            d = inner[_TAG_3DTL]
            p2 = 4  # skip leading u32(1)
            while p2 < len(d):
                ft_off = p2 - 4
                op0 = d[p2]; p2 += 4
                if op0 == 1:
                    face_types[ft_off] = None  # untextured
                elif op0 in (2, 3):
                    coords = [(d[p2 + i*2], d[p2 + i*2 + 1]) for i in range(4)]
                    p2 += 8
                    bmp_res_id = struct.unpack_from("<I", d, p2)[0] + 1  # 0-indexed → resource ID
                    p2 += 4
                    face_types[ft_off] = (bmp_res_id, coords)
                else:
                    break

        # Primitives (3DQL)
        if _TAG_3DQL not in inner:
            continue
        d = inner[_TAG_3DQL]
        num_faces = struct.unpack_from("<I", d, 4)[0]
        p2 = 8

        # Each triangle: (vi0,vi1,vi2, ni0,ni1,ni2, uv0,uv1,uv2, bmp_res_id)
        tris = []

        def _add_tri(vi, ni, ci, ft):
            uv0 = uv1 = uv2 = None
            bmp = None
            if ft is not None:
                bmp_res_id, coords = ft
                uv0, uv1, uv2 = coords[ci[0]], coords[ci[1]], coords[ci[2]]
                bmp = bmp_res_id
            tris.append((vi[0], vi[1], vi[2], ni[0], ni[1], ni[2], uv0, uv1, uv2, bmp))

        for _ in range(num_faces):
            op1    = d[p2 + 1]
            ft_off = struct.unpack_from("<H", d, p2 + 2)[0]
            p2 += 4
            v = list(d[p2:p2 + 4]); p2 += 4
            n = list(d[p2:p2 + 4]); p2 += 4

            face_type = op1 & 0x07
            ft = face_types.get(ft_off)

            if face_type == 3:  # TRIANGLE — setTriangle reverses vertex order
                _add_tri([v[2], v[1], v[0]], [n[2], n[1], n[0]], [2, 1, 0], ft)
            elif face_type == 4:  # QUAD — split via QUAD_TABLE, then reverse per triangle
                for qt in _QUAD:
                    _add_tri(
                        [v[qt[2]], v[qt[1]], v[qt[0]]],
                        [n[qt[2]], n[qt[1]], n[qt[0]]],
                        [qt[2],    qt[1],    qt[0]],
                        ft,
                    )
            # STAR(0), BILLBOARD(5), LINE(7) — procedural, no triangle geometry

        if not tris:
            # Fall back: represent purely-procedural objects as placeholder geometry
            # so they appear in exports (e.g. Godot scene) rather than being silently absent.
            # LINE (7): flat box between v[0] and v[1]; STAR (0): small cube at v[0].
            if verts:
                _gen_placeholder_tris(verts, tris)

        # Write OBJ + MTL
        stem     = f"cobj{res_id:03d}"
        obj_path = os.path.join(obj_dir, f"{stem}.obj")
        mtl_path = os.path.join(obj_dir, f"{stem}.mtl")

        used_bmps = sorted(set(t[9] for t in tris if t[9] is not None))

        with open(mtl_path, "w") as mf:
            for bmp_id in used_bmps:
                mf.write(f"newmtl cbmp{bmp_id:03d}\n")
                mf.write(f"map_Kd ../textures/cbmp{bmp_id:03d}.png\n\n")
            mf.write("newmtl untextured\nKd 0.8 0.8 0.8\n\n")

        uv_list = []
        uv_idx  = {}
        for t in tris:
            for uv in t[6:9]:
                if uv is not None and uv not in uv_idx:
                    uv_idx[uv] = len(uv_list) + 1
                    uv_list.append((uv[0] / 255.0, 1.0 - uv[1] / 255.0))

        with open(obj_path, "w") as of:
            of.write(f"# Cobj {res_id} from {os.path.basename(mis_path)}\n")
            of.write(f"mtllib {stem}.mtl\n")
            for x, y, z in verts:
                of.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
            for nx, ny, nz in normals:
                of.write(f"vn {nx:.6f} {ny:.6f} {nz:.6f}\n")
            for u, v in uv_list:
                of.write(f"vt {u:.6f} {v:.6f}\n")

            current_mtl = None
            for t in sorted(tris, key=lambda t: (t[9] or 0)):
                bmp = t[9]
                mtl = f"cbmp{bmp:03d}" if bmp else "untextured"
                if mtl != current_mtl:
                    of.write(f"usemtl {mtl}\n")
                    current_mtl = mtl
                vi    = [t[0]+1, t[1]+1, t[2]+1]
                ni    = [t[3]+1, t[4]+1, t[5]+1]
                uvs   = [t[6], t[7], t[8]]
                has_n = bool(normals)
                has_uv = all(uv is not None for uv in uvs)
                if has_uv:
                    ti = [uv_idx[uv] for uv in uvs]
                    if has_n:
                        of.write(f"f {vi[0]}/{ti[0]}/{ni[0]} {vi[1]}/{ti[1]}/{ni[1]} {vi[2]}/{ti[2]}/{ni[2]}\n")
                    else:
                        of.write(f"f {vi[0]}/{ti[0]} {vi[1]}/{ti[1]} {vi[2]}/{ti[2]}\n")
                else:
                    if has_n:
                        of.write(f"f {vi[0]}//{ni[0]} {vi[1]}//{ni[1]} {vi[2]}//{ni[2]}\n")
                    else:
                        of.write(f"f {vi[0]} {vi[1]} {vi[2]}\n")

        count += 1

    return count


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root  = os.path.dirname(script_dir)
    parsed     = os.path.join(repo_root, "parsed")

    mis_files = find_game_files("MIS")

    total = 0
    for mis_path in mis_files:
        level   = os.path.splitext(os.path.basename(mis_path))[0]
        out_dir = os.path.join(parsed, level)
        n = extract_cobj(mis_path, out_dir)
        if n:
            print(f"{level}.MIS → {n} objects")
        total += n

    print(f"\nDone — {total} objects extracted.")


if __name__ == "__main__":
    main()
