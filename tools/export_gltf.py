#!/usr/bin/env python3
"""Export Cobj 3D models (with skeletons and animation) as glTF 2.0 binaries.

For every Cobj in every MIS this writes one self-contained GLB file:

    parsed/<LEVEL>/models/cobjNNN.glb     (NNN = Cobj resource id, same numbering
                                           as parsed/<LEVEL>/objects/cobjNNN.obj)

Textures are read from parsed/<LEVEL>/textures/cbmpNNN.png (run
extract_visuals.py first) and embedded, so each .glb stands on its own.

Much of the animation decoding is ported from fc-mit ObjResource.cpp and
ModelBuilder.cpp (Future Cop Mission Integration Tool, MIT licence).

Coordinate convention
---------------------
Identical to extract_objects.py, so a static .glb lines up with its .obj:
    position = (-x, y, z) / 512        normal = normalise(-x, y, z)
    uv       = (u / 255, v / 255)      (glTF has a top-left UV origin, so the
                                        OBJ's "1 - v" flip is not needed)
No axis conversion is applied. With this convention, static and morph
models already stand upright in a Y-up viewer: 785 of 838 clearly
one-sided static models sit at y >= 0, and the vehicles, sharks and
soldiers were checked visually in Blender.

The "PS1 models are Y-inverted" note in CLAUDE.md comes from the SKINNED
models. Their 4DVL vertices are in bone-local space, and the root bone
(3DHY bone 0) carries a 180-degree rotation about (1, 0, 1)
(q = (-0.707, 0, -0.707, 0)). That rotation flips Y and swaps X/Z, plus a
Y offset (0.496 for the CONFT mech legs). The raw, unbaked .obj of the
mech parts (cobj015/016) therefore comes out upside down, which is why
Godot applies scale (1, -1, 1) and a +0.146 raise to them. In the .glb the
root bone is applied, so DO NOT flip the .glb models: all of them are
upright as exported. Every material is doubleSided, because the game's
winding is not reliably consistent and Godot already disables culling.

Rest pose. Static models use the single 4DVL frame. Morph models use the
first frame listed in 3DRF. extract_objects.py keeps whichever 4DVL comes
last, so its .obj shows the LAST morph frame (checked: base + last morph
target matches the .obj bounds for all 73 morph models). Skinned models
store each vertex in its bone's local space, and extract_objects.py writes
those raw positions. Here they are baked into the frame-0 pose (fc-mit does
the same via applyJointTransforms(0)). The inverse bind matrices are the
inverse frame-0 globals, so the bind pose is frame 0.

Usage: export_gltf.py [LEVEL ...]  (no arguments = every level)

Cobj inner chunks
-----------------
Each chunk is a little-endian u32 tag (FourCC, byte-reversed in the file),
then a u32 total_size that includes the 8-byte header. Tags that occur
several times (4DVL/4DNL/3DRL, one per morph frame) are all kept, in file
order.

  4DGI  header, always 0x3C bytes. Payload offsets:
          +0x00 u32 1
          +0x04 u16 num_frames   (bone frames, morph frames, or 1)
          +0x06 u8  id
          +0x07 u8  bitfield: 0x02 has skeleton, 0x08 always set,
                    0x20 semi-transparent env-map, 0x40 environment map,
                    0x80 animated
          +0x08 12 zero bytes
          +0x14 u32 1, 2, 1, 1, 3
          +0x28 u8[4] position_indexes. These are 4DVL vertex indices of
                attachment points (muzzles, mounts); 0xFF means unused.
                Skinned models use 3DHS for these instead.
          +0x2C u32 4, u32 5
  3DTL  face-type list: u32(1), then entries keyed by their byte offset
        from after the u32 (3DQL refers to them by that offset):
          u8 op[4]. op[0]=1: untextured (4 bytes). op[0]=2/3: textured,
          then u8vec2 uv[4] and u32 bmp_index (Cbmp resource id - 1),
          16 bytes in total. op[1..3] is the face colour (R, G, B), with
          0x80 as neutral; the game doubles it and clamps at 255.
  3DQL  primitive list: u32(1), u32 count, then 12 bytes per face:
          u8 op0, u8 op1, u16 face_type_offset, u8 v[4], u8 n[4]
          op0 bit 7: textured. Bits 3-6 are the shading/blend mode (see
          _BLEND_TABLE): flat or normal-lit, monochrome or RGB colour,
          and OPAQUE, MIX (50% blend) or ADDITION (additive).
          op1 bits 0-2 primitive type: 0 STAR, 3 triangle, 4 quad,
          5 billboard, 7 line. Bit 7 marks an environment-mapped
          (reflective) face, but only if 4DGI bit 0x40 is set.
          A triangle is emitted as (v2, v1, v0) and a quad as two
          triangles (QUAD_TABLE {0,1,3} {2,3,1}), each reversed the same way.
          This matches extract_objects.py and fc-mit.
  3DRF  frame reference table, one per data tag:
          u32 ref_no (1=4DVL, 2=4DNL, 3=3DRL), u32 tag, u32 count,
          u32 ids[count]. ids[k] is the frame_id of morph frame k. count > 1
          means vertex (morph-target) animation.
  4DVL  vertex positions: u32 frame_id, u32 count, then count x i16[4]
        (w is ignored).
  4DNL  vertex normals:   u32 frame_id, u32 count, then count x i16[4]
        (scale 4096).
  3DRL  lengths (star/billboard radii): u32 frame_id, u32 count, u16[count].
  3DHY  bones: u32(1), then 20 bytes per bone:
          u8 depth (1 = root; the parent is the nearest preceding bone
          with depth - 1), u8 normal_start, u8 normal_count,
          u8 vertex_start, u8 vertex_count, u8 unk, u8 unk,
          u8 opcode: bits 5..0 = constant flags for pos.x, pos.y, pos.z,
          rot.x, rot.y, rot.z (bits 7-6 unknown),
          i16 pos[3], i16 rot[3]. A constant channel holds its value
          directly. An animated channel holds an index into 3DMI, and its
          frame f value is 3DMI[index + f].
        Vertices vertex_start..+count belong to the bone, rigidly with
        weight 1, and are stored in the bone's local space.
        Decode (fc-mit Bone::decode), with angle unit pi/2048:
          q = Rz(-rot.z) * Ry(-rot.x) * Rx(rot.y)
          t = (-pos.x, pos.y, pos.z) / 512   (both local to the parent)
  3DMI  bone animation data: u32(1), then i16 values that 3DHY indexes.
  3DHS  attachment-point tracks for skinned models: u32 channels, then
        frames x channels x i16[4] (index = frame*channels + channel).
        These are the 4DGI position_indexes vertices, pre-transformed into
        model space for every frame, which makes them a ground truth for the
        3DHY decode. Skinning those vertices with the decode above matches
        3DHS within 0.004 units on every frame of 42 of the 54 models that
        have it. The other 12 are 12-bone humanoids that deviate by 0.06 to
        0.84 units, on bones whose 3DMI rotation values sit at or near the
        int16 limit (-32768). That looks like clamping in the original
        tool; no other Euler order or sign convention does better.
        The 3DHS tracks are exported as-is on the attach_I nodes.
  3DTA  UV animation: u32 count, then count x 16 bytes:
          u8 frames, u8 0, u8 1, u8 flags, u16 frame_duration
          (x 0.001652018 s), u16 0, u32 uv_data_offset, and
          u32 3DTL_offset + 4. A pool of u8vec2 (4 per frame) follows.
        Core glTF cannot express this, so it is stored decoded in
        mesh.extras.fc_uv_animations.
  3DAL  star colour animation: u32(1), then 8 bytes each: u8 face index,
        u8 speed, rgb0, rgb1. It only applies to STAR primitives, which
        are not exported, so it is not used here.
  AnmD  animation track table: u32(1), then 16 bytes per track:
          u8 next_track, u8 mode, u8 end_mode, u8 flags,
          u16 from_frame, u16 to_frame, u8 alt_next_1, u8 alt_next_2,
          u16 frame_event, u32 frame_time.
        Playback (SLUS_007.39, confirmed by decompilation: track start
        0x800448d8, per-tick advance 0x80044b34; the Cobj loader 0x80012e48
        keeps the records raw). from > to plays backwards; from == to is a
        held pose. mode 0 = static pose (only the frame event fires);
        mode 1 = timed: the accumulator gains the tick delta in game clock
        units (300 per second, 5 per NTSC VSync) and steps one frame each
        time it exceeds frame_time, so frame_time 30 = 10 fps (263 of the
        290 mode-1 tracks); mode 2 = the same stepping, but pathed actors
        (class 5 handler 0x800382b4) multiply the delta by their current
        speed, so frame_time is a distance per frame (walk cycles); mode 3 =
        the caller sets the frame directly, from + (to - from) * t / 65536.
        end_mode at the last frame: 0 = go to next_track (restart when that
        is the track itself; with flags & 0x40 next_track / alt_next_1 /
        alt_next_2 are picked at random 22:21:21), 1 = ping-pong, 2 = hold
        the last frame. flags & 0x20 = random +-25 % speed jitter per step.
        frame_event: bits 0-9 a frame number, bits 10-15 an event code
        returned to the actor when that frame is reached (footsteps etc.).
        In the CONFT mech legs (cobj015), tracks 38->39->40->41->38 cover
        frames 11-14, 15-24, 25-28 and 29-39, a closed loop that renders as
        the walk cycle. All raw fields are copied into animation.extras
        (raw = [end_mode, alt_next_1, alt_next_2, frame_event, frame_time]).
  3DBB  per-frame bounding boxes: u32 boxes_per_frame, u32 total, then
        i16 xyz, u16 len_xyz, u16 len3d, u16 len2d per box. Not exported.

glTF output
-----------
Node 0 "cobjNNN" is the root. Its children are the mesh node, the
"armature" (skinned models: joint nodes "bone_K" in 3DHY order) and the
attachment empties "attach_I" (4DGI slot I).
Materials are one per (texture, blend mode, env-map flag). They are named
cbmpNNN or untextured, plus a suffix: _mix for 3DQL MIX (alphaMode BLEND,
extras.fc_blend = "mix") or _add for ADDITION (alphaMode BLEND,
extras.fc_blend = "additive", since glTF has no additive mode), and _env
for env-mapped faces (extras.fc_environment_map). The blend strength comes
from alpha. Textured MIX uses the texture's alpha, as on the PS1, where only
STP pixels (alpha 128 in cbmpNNN.png) blend. A textured semi-transparent
face whose UV rectangle covers no STP texel would render opaque anyway, so
it is exported as opaque. Untextured faces use COLOR_0
alpha: max(rgb) for additive, max(rgb)/2 for semi-transparent colour, and
128 for plain MIX. Opaque textured faces use alphaMode MASK, because texture
alpha 0 is PS1 colour-key transparency. Texture samplers use nearest-
neighbour filtering. COLOR_0 is the 3DTL face colour, doubled and clamped
(fc-mit FaceType::getColor). Faces with no 3DTL entry get extract_objects'
grey. Morph models have N-1 morph targets (deltas from frame 0) and a
"weights" animation. LINE/STAR-only models get extract_objects.py's
placeholder box or cube, which is morphed when the source vertices are.
Whether a face is lit (fc-mit's normal_shading) is not exported: every face
uses the 4DNL normals exactly as the .obj does.

Animations (all at _FPS = 10, the rate of the game's standard timed track,
frame_time 30 clock units = 0.1 s; see AnmD. Tracks with another
frame_time, and speed-driven mode-2 tracks, play at a different rate in
the game; their frame_time is in extras.raw[4]. fc-mit used 24 fps):
  "all_frames"            every frame, 0..N-1, in order
  "trackKK_fA-B"          one per AnmD track with a distinct (A, B) range.
                          Later tracks with the same range are listed in
                          extras.duplicate_tracks instead of being exported
                          again.
"""

import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import iter_iff_resources, find_game_files
from extract_objects import _gen_placeholder_tris
from PIL import Image

_TAG_4DGI = 0x34444749
_TAG_3DTL = 0x3344544C
_TAG_3DQL = 0x3344514C
_TAG_3DHY = 0x33444859
_TAG_3DHS = 0x33444853
_TAG_3DMI = 0x33444D49
_TAG_3DTA = 0x33445441
_TAG_3DRF = 0x33445246
_TAG_4DVL = 0x3444564C
_TAG_4DNL = 0x34444E4C
_TAG_3DRL = 0x3344524C
_TAG_AnmD = 0x416E6D44

_FIXED     = 1.0 / 512.0
_ANGLE     = math.pi / 2048.0
_FPS       = 10.0               # AnmD frame_time 30 / 300 clock units per s
_QUAD      = [[0, 1, 3], [2, 3, 1]]
_UV_UNITS  = 0.001652018           # 3DTA frame_duration → seconds (fc-mit)

_OPAQUE, _ADD, _MIX = 0, 1, 2
_BLACK, _MONO, _FULL = 0, 1, 2
# op0 bits 3-6 → (vertex colour mode, visibility, colour semi-transparent)
_BLEND_TABLE = {
    0b0000: (_MONO, _OPAQUE, False), 0b0001: (_MONO, _MIX, False),
    0b0010: (_FULL, _OPAQUE, False), 0b0011: (_FULL, _MIX, True),
    0b0100: (_MONO, _OPAQUE, False), 0b0101: (_MONO, _MIX, False),
    0b0110: (_FULL, _OPAQUE, False), 0b0111: (_FULL, _MIX, True),
    0b1000: (_MONO, _OPAQUE, False), 0b1001: (_MONO, _MIX, False),
    0b1010: (_FULL, _OPAQUE, False), 0b1011: (_FULL, None, True),  # None: OPAQUE if textured else MIX
    0b1100: (_FULL, _ADD, True),     0b1101: (_FULL, _ADD, True),
    0b1110: (_BLACK, _MIX, False),   0b1111: (_BLACK, _MIX, False),
}
_VIS_SUFFIX = {_OPAQUE: "", _MIX: "_mix", _ADD: "_add"}

# glTF constants
_FLOAT, _UBYTE, _USHORT, _UINT = 5126, 5121, 5123, 5125
_NEAREST, _REPEAT = 9728, 10497


# ── chunk parsing ──────────────────────────────────────────────────────────

def _inner_chunks(raw):
    """Return [(tag, payload)] for every chunk in file order."""
    out = []
    p = 0
    while p + 8 <= len(raw):
        tag, sz = struct.unpack_from("<II", raw, p)
        if sz < 8 or p + sz > len(raw):
            break
        out.append((tag, raw[p + 8 : p + sz]))
        p += sz
    return out


def _vec4_list(d):
    """Parse a 4DVL/4DNL payload → (frame_id, [(x, y, z) raw i16])."""
    frame_id, count = struct.unpack_from("<II", d, 0)
    count = min(count, (len(d) - 8) // 8)
    return frame_id, [struct.unpack_from("<3h", d, 8 + i * 8) for i in range(count)]


def _pos(v):
    return (-v[0] * _FIXED, v[1] * _FIXED, v[2] * _FIXED)


def _nrm(v):
    x, y, z = -float(v[0]), float(v[1]), float(v[2])
    l = math.sqrt(x * x + y * y + z * z)
    return (x / l, y / l, z / l) if l > 0 else (0.0, 1.0, 0.0)


def _parse_cobj(raw):
    """Decode every chunk we understand into a plain dict."""
    m = {"frames_4dgi": 1, "bits": 0, "pos_idx": [], "face_types": {},
         "prims": [], "rf": {}, "vl": {}, "nl": {}, "bones": [], "mi": [],
         "hs": None, "ta": [], "anmd": []}
    for tag, d in _inner_chunks(raw):
        if tag == _TAG_4DGI and len(d) >= 0x2C:
            m["frames_4dgi"] = struct.unpack_from("<H", d, 4)[0]
            m["bits"] = d[7]
            m["pos_idx"] = list(d[0x28:0x2C])
        elif tag == _TAG_3DTL:
            p = 4
            while p + 4 <= len(d):
                off = p - 4
                op = tuple(d[p : p + 4]); p += 4
                if op[0] == 1:
                    m["face_types"][off] = (op, None, None)
                elif op[0] in (2, 3) and p + 12 <= len(d):
                    coords = [(d[p + i * 2], d[p + i * 2 + 1]) for i in range(4)]
                    bmp = struct.unpack_from("<I", d, p + 8)[0] + 1
                    p += 12
                    m["face_types"][off] = (op, bmp, coords)
                else:
                    break
        elif tag == _TAG_3DQL:
            n = struct.unpack_from("<I", d, 4)[0]
            for k in range(min(n, (len(d) - 8) // 12)):
                b = 8 + k * 12
                op0, op1, fto = struct.unpack_from("<BBH", d, b)
                m["prims"].append((op0, op1, fto, list(d[b + 4 : b + 8]), list(d[b + 8 : b + 12])))
        elif tag == _TAG_3DRF:
            _, rtag, cnt = struct.unpack_from("<III", d, 0)
            m["rf"][rtag] = list(struct.unpack_from(f"<{cnt}I", d, 12))
        elif tag == _TAG_4DVL:
            fid, vs = _vec4_list(d)
            m["vl"][fid] = vs
        elif tag == _TAG_4DNL:
            fid, ns = _vec4_list(d)
            m["nl"][fid] = ns
        elif tag == _TAG_3DHY:
            for k in range((len(d) - 4) // 20):
                e = struct.unpack_from("<8B6h", d, 4 + k * 20)
                op = e[7]
                m["bones"].append({
                    "depth": e[0], "n_start": e[1], "n_count": e[2],
                    "v_start": e[3], "v_count": e[4],
                    "const": [(op >> s) & 1 for s in (5, 4, 3, 2, 1, 0)],
                    "unk_bits": op >> 6, "vals": list(e[8:14]), "parent": -1})
        elif tag == _TAG_3DMI:
            m["mi"] = list(struct.unpack_from(f"<{(len(d) - 4) // 2}h", d, 4))
        elif tag == _TAG_3DHS:
            ch = struct.unpack_from("<I", d, 0)[0]
            cnt = (len(d) - 4) // 8
            vals = [struct.unpack_from("<3h", d, 4 + i * 8) for i in range(cnt)]
            m["hs"] = (ch, vals)
        elif tag == _TAG_3DTA:
            m["ta"] = _parse_3dta(d)
        elif tag == _TAG_AnmD:
            for k in range((len(d) - 4) // 16):
                m["anmd"].append(struct.unpack_from("<4BHH2BHI", d, 4 + k * 16))

    # Parent links: nearest preceding bone with depth - 1 (fc-mit 3DHY).
    bones = m["bones"]
    for i, b in enumerate(bones):
        if i == 0 or b["depth"] <= 1:
            continue
        for j in range(i - 1, -1, -1):
            if bones[j]["depth"] == b["depth"] - 1:
                b["parent"] = j
                break
    return m


def _parse_3dta(d):
    if len(d) < 4:
        return []
    n = struct.unpack_from("<I", d, 0)[0]
    entries = []
    for k in range(n):
        if 4 + k * 16 + 16 > len(d):
            break
        e = struct.unpack_from("<4BHHII", d, 4 + k * 16)
        entries.append(e)
    pool = d[4 + len(entries) * 16 :]
    out = []
    for frames, _, _, flags, dur, _, uv_off, tl_off in entries:
        need = uv_off + frames * 8
        if uv_off % 2 or need > len(pool):
            continue
        uvs = [[[pool[uv_off + f * 8 + c * 2] / 255.0, pool[uv_off + f * 8 + c * 2 + 1] / 255.0]
                for c in range(4)] for f in range(frames)]
        out.append({"face_type_offset": tl_off - 4, "flags": flags,
                    "frame_duration_s": round(dur * _UV_UNITS, 6), "frames": uvs})
    return out


# ── maths (quaternions are (x, y, z, w); matrices are 4x4 row-major lists) ──

def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def _qaxis(axis, ang):
    s = math.sin(ang / 2)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(ang / 2))


def _qmat(q):
    x, y, z, w = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def _trs(t, q):
    r = _qmat(q)
    return [r[0] + [t[0]], r[1] + [t[1]], r[2] + [t[2]], [0.0, 0.0, 0.0, 1.0]]


def _mmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _rigid_inv(m):
    r = [[m[j][i] for j in range(3)] for i in range(3)]
    t = [-sum(r[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [r[0] + [t[0]], r[1] + [t[1]], r[2] + [t[2]], [0.0, 0.0, 0.0, 1.0]]


def _xform(m, p, w=1.0):
    return tuple(m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] * w for i in range(3))


def _bone_local(bone, mi, frame):
    """Local (translation, quaternion) of one bone at one frame (fc-mit Bone::decode)."""
    v = []
    for c in range(6):
        if bone["const"][c]:
            v.append(bone["vals"][c])
        else:
            idx = bone["vals"][c] + frame
            v.append(mi[idx] if 0 <= idx < len(mi) else 0)
    px, py, pz, rx, ry, rz = v
    q = _qmul(_qaxis((0, 1, 0), -rx * _ANGLE), _qaxis((1, 0, 0), ry * _ANGLE))
    q = _qmul(_qaxis((0, 0, 1), -rz * _ANGLE), q)
    return (-px * _FIXED, py * _FIXED, pz * _FIXED), q


def _bone_frames(m):
    """Number of bone animation frames (fc-mit: len(3DMI) / animated channels)."""
    chans = sum(6 - sum(b["const"]) for b in m["bones"])
    if chans == 0:
        return 1
    return max(1, len(m["mi"]) // chans)


def _globals(m, frame):
    out = []
    for b in m["bones"]:
        t, q = _bone_local(b, m["mi"], frame)
        loc = _trs(t, q)
        out.append(_mmul(out[b["parent"]], loc) if b["parent"] >= 0 else loc)
    return out


def _vertex_bone(bones, vi):
    """Bone owning a 4DVL vertex index (last match, fc-mit search order)."""
    j = 0
    for k, b in enumerate(bones):
        if b["v_start"] > vi:
            break
        if b["v_start"] + b["v_count"] > vi:
            j = k
    return j


# ── geometry ────────────────────────────────────────────────────────────────

def _face_normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
    l = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
    return (n[0] / l, n[1] / l, n[2] / l) if l > 1e-12 else (0.0, 1.0, 0.0)


def _face_style(op0, ft):
    """→ (visibility, rgba) for a face (fc-mit 3DQL bitfield + FaceType::getColor)."""
    textured = ft is not None and ft[1] is not None
    mode, vis, semi = _BLEND_TABLE[(op0 >> 3) & 0x0F]
    if vis is None:
        vis = _OPAQUE if (op0 & 0x80) else _MIX
    if ft is None:
        return vis, (204, 204, 204, 255)          # extract_objects' grey "untextured" Kd
    op = ft[0]
    if mode == _FULL:
        r, g, b = (min(op[i] * 2, 255) for i in (1, 2, 3))
    elif mode == _MONO:
        r = g = b = min(op[1] * 2, 255)
    else:
        r = g = b = 0
    if textured or not semi:
        # Untextured MIX faces without their own colour alpha blend at 50%.
        a = 128 if (vis == _MIX and not textured) else 255
    else:
        a = max(r, g, b)
        if vis != _ADD:
            a //= 2
    return vis, (r, g, b, a)


def _build_triangles(m, stats):
    """Expand 3DQL into triangle corners, exactly as extract_objects.py does.

    Returns a list of (vis, bmp, rgba, reflective, [(vi, ni, uv), x3]).
    """
    tris = []
    env = bool(m["bits"] & 0x40)
    for op0, op1, fto, v, n in m["prims"]:
        kind = op1 & 0x07
        if kind not in (3, 4):
            continue
        ft = m["face_types"].get(fto)
        vis, rgba = _face_style(op0, ft)
        bmp = ft[1] if ft is not None else None
        if bmp is not None and not (op0 & 0x80):
            stats["tex_flag_mismatch"] = stats.get("tex_flag_mismatch", 0) + 1
        refl = bool(op1 & 0x80) and env
        if refl and not (m["bits"] & 0x20):
            vis = _OPAQUE          # fc-mit: env-mapped faces are opaque unless 4DGI 0x20
        coords = ft[2] if bmp is not None else None
        if kind == 3:
            order = [[2, 1, 0]]
        else:
            order = [[q[2], q[1], q[0]] for q in _QUAD]
        for o in order:
            corners = [(v[c], n[c], coords[c] if coords else None) for c in o]
            tris.append((vis, bmp, rgba, refl, corners))
    return tris


# ── GLB writer ──────────────────────────────────────────────────────────────

class _Gltf:
    def __init__(self):
        self.j = {"asset": {"version": "2.0", "generator": "fc-lapd tools/export_gltf.py"},
                  "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [], "buffers": [],
                  "bufferViews": [], "accessors": []}
        self.bin = bytearray()

    def view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        bv = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            bv["target"] = target
        self.bin += data
        self.j["bufferViews"].append(bv)
        return len(self.j["bufferViews"]) - 1

    def accessor(self, rows, ctype, atype, target=None, minmax=False, normalized=False):
        width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[atype]
        fmt = {_FLOAT: "f", _UBYTE: "B", _USHORT: "H", _UINT: "I"}[ctype]
        flat = []
        for r in rows:
            if width == 1 and not isinstance(r, (tuple, list)):
                flat.append(r)
            else:
                flat.extend(r)
        data = struct.pack(f"<{len(flat)}{fmt}", *flat)
        # Vertex attributes must have 4-byte-aligned strides.
        if target == 34962 and (width * struct.calcsize(fmt)) % 4:
            raise ValueError("unaligned vertex attribute")
        acc = {"bufferView": self.view(data, target), "componentType": ctype,
               "count": len(rows), "type": atype}
        if normalized:
            acc["normalized"] = True
        if minmax and rows:
            cols = list(zip(*[(r,) if width == 1 and not isinstance(r, (tuple, list)) else r for r in rows]))
            acc["min"] = [min(c) for c in cols]
            acc["max"] = [max(c) for c in cols]
        self.j["accessors"].append(acc)
        return len(self.j["accessors"]) - 1

    def node(self, **kw):
        self.j["nodes"].append(kw)
        return len(self.j["nodes"]) - 1

    def write(self, path):
        self.j["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(self.j, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        bn = bytes(self.bin) + b"\0" * (-len(self.bin) % 4)
        total = 12 + 8 + len(js) + 8 + len(bn)
        with open(path, "wb") as f:
            f.write(struct.pack("<4sII", b"glTF", 2, total))
            f.write(struct.pack("<I4s", len(js), b"JSON") + js)
            f.write(struct.pack("<I4s", len(bn), b"BIN\0") + bn)


def _has_stp(tex_dir, bmp, uvs, cache):
    """True if the texels under a face's UV rectangle include STP (alpha 128) pixels."""
    if bmp not in cache:
        png = os.path.join(tex_dir, f"cbmp{bmp:03d}.png")
        cache[bmp] = Image.open(png).convert("RGBA").getchannel("A") if os.path.exists(png) else None
    alpha = cache[bmp]
    if alpha is None:
        return True
    us = [uv[0] for uv in uvs]
    vs = [uv[1] for uv in uvs]
    key = (bmp, min(us), min(vs), max(us), max(vs))
    if key not in cache:
        crop = alpha.crop((key[1], key[2], key[3] + 1, key[4] + 1))
        cache[key] = crop.histogram()[128] > 0
    return cache[key]


def _material(g, key, tex_dir, tex_cache, warn):
    """Create (or reuse) a glTF material for (bmp, vis, refl)."""
    mats = g.j.setdefault("materials", [])
    bmp, vis, refl = key
    name = (f"cbmp{bmp:03d}" if bmp else "untextured") + _VIS_SUFFIX[vis] + ("_env" if refl else "")
    pbr = {"baseColorFactor": [1.0, 1.0, 1.0, 1.0],
           "metallicFactor": 0.0, "roughnessFactor": 1.0}
    mat = {"name": name, "pbrMetallicRoughness": pbr, "doubleSided": True}
    if bmp:
        if bmp not in tex_cache:
            png = os.path.join(tex_dir, f"cbmp{bmp:03d}.png")
            if os.path.exists(png):
                with open(png, "rb") as f:
                    bv = g.view(f.read())
                imgs = g.j.setdefault("images", [])
                imgs.append({"name": f"cbmp{bmp:03d}", "mimeType": "image/png", "bufferView": bv})
                if "samplers" not in g.j:
                    g.j["samplers"] = [{"magFilter": _NEAREST, "minFilter": _NEAREST,
                                        "wrapS": _REPEAT, "wrapT": _REPEAT}]
                texs = g.j.setdefault("textures", [])
                texs.append({"sampler": 0, "source": len(imgs) - 1})
                tex_cache[bmp] = len(texs) - 1
            else:
                warn.append(f"missing texture cbmp{bmp:03d}.png")
                tex_cache[bmp] = None
        if tex_cache[bmp] is not None:
            pbr["baseColorTexture"] = {"index": tex_cache[bmp]}
    if vis == _OPAQUE:
        if bmp:
            mat["alphaMode"] = "MASK"
            mat["alphaCutoff"] = 0.25
    else:
        mat["alphaMode"] = "BLEND"
    extras = {}
    if vis == _ADD:
        extras["fc_blend"] = "additive"
    elif vis == _MIX:
        extras["fc_blend"] = "mix"
    if refl:
        extras["fc_environment_map"] = True
    if extras:
        mat["extras"] = extras
    mats.append(mat)
    return len(mats) - 1


def _clips(m, nframes):
    """→ [(name, [frame indices], extras)] following AnmD (see docstring)."""
    out = []
    if nframes > 1:
        out.append(("all_frames", list(range(nframes)), {}))
    seen = {}
    for k, t in enumerate(m["anmd"]):
        nxt, mode, z0, flags, a, b, u0, u1, u16, u32 = t
        if a >= nframes or b >= nframes:
            continue
        if nframes == 1 and a == b == 0:
            continue
        if (a, b) in seen:
            out[seen[(a, b)]][2].setdefault("duplicate_tracks", []).append(k)
            continue
        step = 1 if b >= a else -1
        frames = list(range(a, b + step, step))
        ex = {"anmd_track": k, "from_frame": a, "to_frame": b, "next_track": nxt,
              "mode": mode, "flags": flags, "raw": [z0, u0, u1, u16, u32]}
        seen[(a, b)] = len(out)
        out.append((f"track{k:02d}_f{a}-{b}", frames, ex))
    return out


def _export_one(m, res_id, level, tex_dir, out_path, stats):
    warn = []
    rf_v = m["rf"].get(_TAG_4DVL) or sorted(m["vl"])
    rf_n = m["rf"].get(_TAG_4DNL) or sorted(m["nl"])
    vframes = [[_pos(p) for p in m["vl"][i]] for i in rf_v if i in m["vl"]]
    if not vframes:
        return None
    nframes_morph = len(vframes)
    nframes_n = [m["nl"].get(i) for i in rf_n]
    base_v = list(vframes[0])
    base_n_raw = nframes_n[0] if nframes_n and nframes_n[0] else []
    base_n = [_nrm(p) for p in base_n_raw]
    nverts = len(base_v)

    skinned = bool(m["bones"])
    if skinned and nframes_morph > 1:
        warn.append("both skeleton and morph frames; morphs ignored")
        nframes_morph = 1
    morph = nframes_morph > 1

    tris = _build_triangles(m, stats)
    # Drop faces that index past the vertex list (fc-mit isWithinBounds).
    good = [t for t in tris if all(c[0] < nverts for c in t[4])]
    if len(good) != len(tris):
        warn.append(f"{len(tris) - len(good)} out-of-range triangles skipped")
    tris = good
    # Textured semi-transparent faces only blend their STP texels on the PS1.
    # If a face samples none, it renders opaque, so export it that way to
    # avoid needless alpha sorting (e.g. the player mech body).
    stp_cache = {}
    for k, t in enumerate(tris):
        if t[1] is not None and t[0] != _OPAQUE and \
                not _has_stp(tex_dir, t[1], [c[2] for c in t[4]], stp_cache):
            tris[k] = (_OPAQUE,) + t[1:]

    placeholder = False
    if not tris:
        verts = list(base_v)
        ptris = []
        _gen_placeholder_tris(verts, ptris)
        base_v = verts
        nverts = len(base_v)
        tris = [(_OPAQUE, None, (204, 204, 204, 255), False,
                 [(t[0], None, None), (t[1], None, None), (t[2], None, None)]) for t in ptris]
        placeholder = True
        skinned = False
        if morph:
            # Rebuild the same placeholder box from every morph frame's vertices.
            for fv in vframes[:nframes_morph]:
                _gen_placeholder_tris(fv, [])

    # Skinning: bake vertices into the frame-0 pose.
    bones = m["bones"] if skinned else []
    nbf = _bone_frames(m) if skinned else 1
    g0 = _globals(m, 0) if skinned else []
    vjoint = [_vertex_bone(bones, i) for i in range(nverts)] if skinned else []

    def world_v(vi):
        p = base_v[vi]
        return _xform(g0[vjoint[vi]], p) if skinned else p

    def world_n(vi, nrm):
        return _xform(g0[vjoint[vi]], nrm, 0.0) if skinned else nrm

    # ── unroll + dedupe vertices per material ──
    g = _Gltf()
    tex_cache = {}
    groups = {}
    for t in tris:
        key = (t[1], t[0], t[3])
        groups.setdefault(key, []).append(t)
    # Sort like extract_objects (by bmp), then visibility.
    order = sorted(groups, key=lambda k: (k[0] or 0, k[1], k[2]))

    prims = []
    morph_targets_all = []
    has_normals_src = bool(base_n)
    for key in order:
        mat = _material(g, key, tex_dir, tex_cache, warn)
        pos, nor, uvs, col, jnt, idx = [], [], [], [], [], []
        mp = [[] for _ in range(nframes_morph - 1)]
        mn = [[] for _ in range(nframes_morph - 1)]
        seen = {}
        use_uv = key[0] is not None and key[0] in tex_cache and tex_cache[key[0]] is not None
        for ti, (vis, bmp, rgba, refl, corners) in enumerate(groups[key]):
            p3 = [base_v[c[0]] for c in corners]
            flat = _face_normal(*p3)
            for vi, ni, uv in corners:
                use_flat = ni is None or ni >= len(base_n)
                nk = ("f", ti) if use_flat else ni
                k = (vi, nk, uv, rgba)
                if k in seen:
                    idx.append(seen[k]); continue
                seen[k] = len(pos)
                idx.append(len(pos))
                pos.append(world_v(vi))
                nor.append(world_n(vi, flat if use_flat else base_n[ni]))
                uvs.append((uv[0] / 255.0, uv[1] / 255.0) if uv else (0.0, 0.0))
                col.append(rgba)
                if skinned:
                    jnt.append(vjoint[vi])
                for f in range(1, nframes_morph):
                    fv = vframes[f]
                    q = fv[vi] if vi < len(fv) else base_v[vi]
                    b = base_v[vi]
                    mp[f - 1].append((q[0] - b[0], q[1] - b[1], q[2] - b[2]))
                    fnl = nframes_n[f] if f < len(nframes_n) else None
                    if use_flat or not fnl or ni >= len(fnl):
                        fp = [(vframes[f][c[0]] if c[0] < len(vframes[f]) else base_v[c[0]])
                              for c in corners]
                        qn = _face_normal(*fp)
                    else:
                        qn = _nrm(fnl[ni])
                    bn = nor[-1]
                    mn[f - 1].append((qn[0] - bn[0], qn[1] - bn[1], qn[2] - bn[2]))
        attrs = {"POSITION": g.accessor(pos, _FLOAT, "VEC3", 34962, minmax=True),
                 "NORMAL": g.accessor(nor, _FLOAT, "VEC3", 34962),
                 "COLOR_0": g.accessor(col, _UBYTE, "VEC4", 34962, normalized=True)}
        if use_uv:
            attrs["TEXCOORD_0"] = g.accessor(uvs, _FLOAT, "VEC2", 34962)
        if skinned:
            attrs["JOINTS_0"] = g.accessor([(j, 0, 0, 0) for j in jnt], _UBYTE, "VEC4", 34962)
            attrs["WEIGHTS_0"] = g.accessor([(1.0, 0.0, 0.0, 0.0)] * len(jnt), _FLOAT, "VEC4", 34962)
        ictype = _USHORT if len(pos) < 65536 else _UINT
        prim = {"attributes": attrs, "indices": g.accessor(idx, ictype, "SCALAR", 34963),
                "material": mat, "mode": 4}
        if morph:
            prim["targets"] = [{"POSITION": g.accessor(mp[f], _FLOAT, "VEC3", 34962, minmax=True),
                                "NORMAL": g.accessor(mn[f], _FLOAT, "VEC3", 34962)}
                               for f in range(nframes_morph - 1)]
        prims.append(prim)

    mesh = {"name": f"cobj{res_id:03d}", "primitives": prims}
    mextras = {"fc_level": level, "fc_cobj": res_id, "fc_4dgi_bits": m["bits"],
               "fc_4dgi_frames": m["frames_4dgi"]}
    if placeholder:
        mextras["fc_placeholder"] = True
    if m["ta"]:
        mextras["fc_uv_animations"] = m["ta"]
    if morph:
        mesh["weights"] = [0.0] * (nframes_morph - 1)
        mesh["extras"] = dict(mextras, targetNames=[f"frame{f}" for f in range(1, nframes_morph)])
    else:
        mesh["extras"] = mextras
    g.j["meshes"] = [mesh]

    root = g.node(name=f"cobj{res_id:03d}", children=[])
    mesh_node = g.node(name=f"cobj{res_id:03d}_mesh", mesh=0)
    g.j["nodes"][root]["children"].append(mesh_node)

    # ── skeleton ──
    joint_nodes = []
    if skinned:
        arm = g.node(name="armature", children=[])
        g.j["nodes"][root]["children"].append(arm)
        for bi, b in enumerate(bones):
            t, q = _bone_local(b, m["mi"], 0)
            joint_nodes.append(g.node(name=f"bone_{bi}", translation=list(t), rotation=list(q),
                                      extras={"vertex_start": b["v_start"], "vertex_count": b["v_count"],
                                              "normal_start": b["n_start"], "normal_count": b["n_count"],
                                              "opcode_unknown_bits": b["unk_bits"]}))
        for bi, b in enumerate(bones):
            parent = joint_nodes[b["parent"]] if b["parent"] >= 0 else arm
            g.j["nodes"][parent].setdefault("children", []).append(joint_nodes[bi])
        ibm = []
        for gm in g0:
            inv = _rigid_inv(gm)
            ibm.append([inv[r][c] for c in range(4) for r in range(4)])   # column-major
        g.j["skins"] = [{"name": "skeleton", "joints": joint_nodes,
                         "inverseBindMatrices": g.accessor(ibm, _FLOAT, "MAT4"),
                         "skeleton": arm}]
        g.j["nodes"][mesh_node]["skin"] = 0

    # ── attachment points (4DGI position_indexes / 3DHS) ──
    nframes = nbf if skinned else (nframes_morph if morph else 1)
    attach = []   # (node, [per-frame translation])
    hs = m["hs"]
    for slot, pi in enumerate(m["pos_idx"]):
        if pi == 0xFF or placeholder:
            continue
        track = None
        if skinned and hs and slot < hs[0]:
            ch, vals = hs
            track = [_pos(vals[f * ch + slot]) for f in range(len(vals) // ch)]
        elif skinned and pi < nverts:
            track = [_xform(_globals(m, f)[vjoint[pi]], base_v[pi]) for f in range(nframes)]
        elif pi < nverts:
            track = [vf[pi] if pi < len(vf) else base_v[pi] for vf in vframes[:nframes_morph]]
        if not track:
            continue
        n = g.node(name=f"attach_{slot}", translation=list(track[0]),
                   extras={"fc_vertex_index": pi})
        g.j["nodes"][root]["children"].append(n)
        attach.append((n, track))

    # ── animations ──
    anims = []
    clips = _clips(m, nframes) if (skinned or morph or attach) else []
    if nframes > 1 and clips:
        bone_tr = [_bone_local_all(b, m["mi"], nframes) for b in bones] if skinned else []
        time_cache = {}
        for name, frames, ex in clips:
            nfr = len(frames)
            if nfr not in time_cache:
                time_cache[nfr] = g.accessor([i / _FPS for i in range(nfr)], _FLOAT, "SCALAR", minmax=True)
            tacc = time_cache[nfr]
            samplers, channels = [], []

            def chan(node, path, rows, atype, interp="LINEAR"):
                out = g.accessor(rows, _FLOAT, atype)
                samplers.append({"input": tacc, "output": out, "interpolation": interp})
                channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": path}})

            for bi in range(len(bones)):
                ts, qs = bone_tr[bi]
                chan(joint_nodes[bi], "translation", [ts[f] for f in frames], "VEC3")
                chan(joint_nodes[bi], "rotation", _fix_quat_signs([qs[f] for f in frames]), "VEC4")
            if morph:
                rows = []
                for f in frames:
                    w = [0.0] * (nframes_morph - 1)
                    if f > 0:
                        w[f - 1] = 1.0
                    rows.extend(w)
                out = g.accessor(rows, _FLOAT, "SCALAR")
                samplers.append({"input": tacc, "output": out, "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1, "target": {"node": mesh_node, "path": "weights"}})
            for n, track in attach:
                chan(n, "translation", [track[min(f, len(track) - 1)] for f in frames], "VEC3")
            a = {"name": name, "samplers": samplers, "channels": channels}
            if ex:
                a["extras"] = ex
            anims.append(a)
    if anims:
        g.j["animations"] = anims

    if warn:
        g.j["extras"] = {"warnings": warn}
    g.write(out_path)
    return {"skinned": skinned, "bones": len(bones), "morph": morph,
            "targets": nframes_morph - 1 if morph else 0, "frames": nframes,
            "anims": len(anims), "attach": len(attach), "warn": warn,
            "tris": sum(len(v) for v in groups.values()), "uvanim": len(m["ta"])}


def _bone_local_all(bone, mi, nframes):
    ts, qs = [], []
    for f in range(nframes):
        t, q = _bone_local(bone, mi, f)
        ts.append(t); qs.append(q)
    return ts, qs


def _fix_quat_signs(qs):
    """Keep consecutive quaternions in the same hemisphere so LINEAR lerp is short."""
    out = []
    for q in qs:
        if out and sum(a * b for a, b in zip(out[-1], q)) < 0:
            q = tuple(-c for c in q)
        out.append(tuple(q))
    return out


def export_level(mis_path, out_dir, level):
    with open(mis_path, "rb") as f:
        data = f.read()
    model_dir = os.path.join(out_dir, "models")
    tex_dir = os.path.join(out_dir, "textures")
    results = {}
    stats = {}
    for res_type, res_id, raw in iter_iff_resources(data):
        if res_type != "Cobj":
            continue
        m = _parse_cobj(raw)
        if _TAG_3DQL not in {t for t, _ in _inner_chunks(raw)}:
            continue          # extract_objects.py skips these too
        os.makedirs(model_dir, exist_ok=True)
        out = os.path.join(model_dir, f"cobj{res_id:03d}.glb")
        r = _export_one(m, res_id, level, tex_dir, out, stats)
        if r is not None:
            results[res_id] = r
    return results, stats


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    parsed = os.path.join(repo_root, "parsed")
    mis_files = find_game_files("MIS")
    total = 0
    for mis_path in mis_files:
        level = os.path.splitext(os.path.basename(mis_path))[0]
        if sys.argv[1:] and level not in sys.argv[1:]:
            continue
        res, stats = export_level(mis_path, os.path.join(parsed, level), level)
        sk = [i for i, r in res.items() if r["skinned"]]
        mo = [i for i, r in res.items() if r["morph"]]
        an = sum(1 for r in res.values() if r["anims"])
        nw = sum(1 for r in res.values() if r["warn"])
        print(f"{level}: {len(res)} models, {len(sk)} skinned {sk}, {len(mo)} morph {mo}, "
              f"{an} animated, {nw} with warnings")
        for i, r in res.items():
            for w in r["warn"]:
                print(f"  cobj{i:03d}: {w}")
        if stats.get("tex_flag_mismatch"):
            print(f"  {stats['tex_flag_mismatch']} faces have a 3DTL texture but op0 bit 7 clear "
                  "(textured anyway, as in extract_objects.py)")
        total += len(res)
    print(f"\nDone: {total} models exported.")


if __name__ == "__main__":
    main()
