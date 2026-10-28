#!/usr/bin/env python3
"""Extract visual assets from Future Cop: L.A.P.D. MIS archives.

Resource types handled:
  Cbmp — PS1 paletted texture (256×256 pixels, 8bpp indexed, 256-entry PLUT palette).
          Inner tags (little-endian tag+size pairs):
            CCB  (0x43434220) — 3DO Cell Control Block header, skipped
            PDAT (0x50444154) — 8-bit palette indices, 256×256 = 65536 bytes
            PLUT (0x504C5554) — palette: 12-byte prefix + 256 × uint16 LE (B5G5R5T1)
          Pixel format B5G5R5T1: bits[0-4]=R, bits[5-9]=G, bits[10-14]=B, bit[15]=STP.
          Black (R=G=B=0) → alpha=0 (transparent); STP=1 → alpha=128; else alpha=255.
          The bottom ~4 rows are VRAM fill/padding that the game never samples via UV
          coordinates. They appear as a solid colour band (red, green, etc.) depending
          on which palette index was used as the fill byte for that texture.
          Output: textures/cbmpNNN.png

  Cpyr — PS1 particle effect sprite sheets (explosions, smoke, fire, etc.).
          One Cpyr per level; contains up to ~10 particle types, each with multiple
          animation frames packed into a shared 4bpp 256×512 atlas.
          Inner tags:
            PYDT — particle descriptor list: num_particles, then per-particle:
                     id(u16), num_sprites(u8), sprite_size(u8 power-of-2),
                     per-sprite: atlas_x(u8), atlas_y(u8), offset_x(u8), offset_y(u8),
                                 w(u8), h(u8), y_ext(u8, extends atlas_y by 256), unk(u8)
            PYPL — 16-color palettes packed sequentially (one per sprite, PS1 order).
                     Each color: uint16 LE, bits[0-4]=R, bits[5-9]=G, bits[10-14]=B,
                     bit[15]=A (1=opaque, 0=transparent — plain alpha, not STP).
            PIX4 — 4bpp pixel atlas, 256×512 pixels (65536 bytes).
                     High nibble = even pixel (lower x), low nibble = odd pixel.
          Output: particles/cpyrNNN_pPP.png  (NNN=resource ID, PP=particle ID,
                  PNG is sprite_size × (sprite_size × num_sprites) vertical strip)

  Cfnt — Bitmap font (BMFont format). One shared game font; FE.MIS has two extras.
          Single FNTP block (magic "FNTP" + uint32 size) containing:
            Header (32 bytes): n_glyphs(u16 @10), platform(u32 @12, 9=PS1),
                               glyph_off(u32 @20), img_off(u32 @28)
            Glyph table: n_glyphs × 11 bytes —
                         glyphID(u8), unk(u8), w(u8), h(u8), atlas_x(u8), unk(u8),
                         atlas_y(u8), unk(u8), x_advance(u8), offset_x(i8), offset_y(i8)
            Image header (16 bytes at img_off): '@' byte, 3 unk, width(u16), height(u16)
            Image data: 4bpp, high nibble = left (even) pixel — same order as Cpyr PIX4.
                        16-color palette: indices 0-7 = transparent, 8-15 = opaque white.
          Output: fonts/cfntNNN.png (atlas) + fonts/cfntNNN.fnt (BMFont descriptor)
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import iter_iff_resources as _iter_iff_resources, find_game_files

try:
    from PIL import Image
except ImportError:
    print("PIL/Pillow required: pip install Pillow", file=sys.stderr)
    sys.exit(1)

_CCB_TAG  = 0x43434220  # "CCB "
_PDAT_TAG = 0x50444154  # "PDAT" — PS1 palette-indexed pixel data
_PLUT_TAG = 0x504C5554  # "PLUT" — palette

# Cpyr / PYRResource inner tags (stored reversed in the file)
_PYDT_TAG = 0x50594454  # "PYDT" stored as "TDYP"
_PYPL_TAG = 0x5059504C  # "PYPL" stored as "LPYP"
_PIX4_TAG = 0x50495834  # "PIX4" stored as "4XIP" (PS1 4bpp atlas)

# Cfnt / FontResource
_FNTP_MAGIC  = b'FNTP'   # first 4 bytes of Cfnt payload
_FNT_GLYPH_SIZE = 11     # bytes per glyph record

def _parse_inner_tags(raw):
    """Parse little-endian tag+size blocks inside a resource payload.

    Returns dict mapping tag_id (uint32) → (offset_after_header, payload_size).
    """
    tags = {}
    p = 0
    while p + 8 <= len(raw):
        tag = struct.unpack_from("<I", raw, p)[0]
        sz  = struct.unpack_from("<I", raw, p+4)[0]
        if sz < 8 or p + sz > len(raw):
            break
        tags[tag] = (p + 8, sz - 8)
        p += sz
    return tags


def _decode_plut(raw, offset):
    """Decode 256-entry B5G5R5T1 palette.  Returns list of 256 (R,G,B,A) tuples."""
    palette = []
    off = offset + 12  # skip 12-byte PLUT prefix
    for _ in range(256):
        w = struct.unpack_from("<H", raw, off)[0]
        r5 = (w      ) & 0x1f
        g5 = (w >>  5) & 0x1f
        b5 = (w >> 10) & 0x1f
        stp = (w >> 15) & 1
        r8 = (r5 << 3) | (r5 >> 2)
        g8 = (g5 << 3) | (g5 >> 2)
        b8 = (b5 << 3) | (b5 >> 2)
        if r5 == 0 and g5 == 0 and b5 == 0:
            a8 = 0          # pure black = transparent (PS1 convention)
        elif stp:
            a8 = 128        # semi-transparency bit set
        else:
            a8 = 255
        palette.append((r8, g8, b8, a8))
        off += 2
    return palette


def _expand5(v):
    return (v << 3) | (v >> 2)


def _decode_pypl(raw, offset, num_colors):
    """Decode num_colors consecutive B5G5R5A1 palette entries from raw[offset:].

    bit[15]=A: 1=opaque, 0=transparent (plain alpha — not STP semi-transparency).
    Returns list of (R, G, B, A) tuples.
    """
    palette = []
    off = offset
    for _ in range(num_colors):
        w = struct.unpack_from("<H", raw, off)[0]
        r5 = (w      ) & 0x1f
        g5 = (w >>  5) & 0x1f
        b5 = (w >> 10) & 0x1f
        a  = 255 if (w >> 15) else 0
        palette.append((_expand5(r5), _expand5(g5), _expand5(b5), a))
        off += 2
    return palette


def _pix4_get(atlas_bytes, atlas_x, atlas_y):
    """Return the 4-bit palette index for pixel (atlas_x, atlas_y) in the 256×512 atlas.

    PIX4 encoding: high nibble = even-column pixel, low nibble = odd-column pixel.
    Row stride = 128 bytes (256 pixels / 2 nibbles-per-byte).
    """
    byte_off = atlas_y * 128 + atlas_x // 2
    byte = atlas_bytes[byte_off]
    return (byte >> 4) & 0xF if (atlas_x & 1) == 0 else byte & 0xF


def extract_fnt(mis_path, out_dir):
    """Extract Cfnt bitmap fonts from a MIS file.  Returns count written."""
    with open(mis_path, "rb") as f:
        data = f.read()

    fonts_dir = os.path.join(out_dir, "fonts")
    os.makedirs(fonts_dir, exist_ok=True)

    count = 0
    for res_type, res_id, raw in _iter_iff_resources(data):
        if res_type != "Cfnt":
            continue
        if len(raw) < 32 or raw[0:4] != _FNTP_MAGIC:
            continue

        n_glyphs  = struct.unpack_from("<H", raw, 10)[0]
        glyph_off = struct.unpack_from("<I", raw, 20)[0]
        img_off   = struct.unpack_from("<I", raw, 28)[0]

        if glyph_off + n_glyphs * _FNT_GLYPH_SIZE > len(raw):
            continue
        if img_off + 16 > len(raw):
            continue
        if raw[img_off] != ord("@"):
            continue

        # Parse glyph descriptors
        glyphs = []
        p = glyph_off
        for _ in range(n_glyphs):
            gid, _u0, gw, gh, gl, _u1, gt, _u2, xadv, ox, oy = \
                struct.unpack_from("BBBBBBBBBbb", raw, p)
            glyphs.append((gid, gw, gh, gl, gt, xadv, ox, oy))
            p += _FNT_GLYPH_SIZE

        # Image dimensions from image header
        w = struct.unpack_from("<H", raw, img_off + 4)[0]
        h = struct.unpack_from("<H", raw, img_off + 6)[0]
        pix_start  = img_off + 16
        row_stride = w // 2

        if pix_start + row_stride * h > len(raw):
            continue

        # Decode 4bpp: high nibble = even-column pixel (same order as Cpyr PIX4)
        # Palette: nibble 0-7 → transparent, 8-15 → opaque white
        rgba = bytearray(w * h * 4)
        for y in range(h):
            for x in range(w):
                byte = raw[pix_start + y * row_stride + x // 2]
                nibble = (byte >> 4) & 0xF if (x & 1) == 0 else byte & 0xF
                a = 255 if nibble >= 8 else 0
                off = (y * w + x) * 4
                rgba[off:off + 4] = bytes([255, 255, 255, a])

        png_name = f"cfnt{res_id:03d}.png"
        fnt_name = f"cfnt{res_id:03d}.fnt"

        Image.frombytes("RGBA", (w, h), bytes(rgba)).save(
            os.path.join(fonts_dir, png_name)
        )

        with open(os.path.join(fonts_dir, fnt_name), "w") as fh:
            fh.write(
                f'info face="cfnt{res_id:03d}" size=24 bold=0 italic=0'
                f' charset="ASCII" unicode=0 stretchH=100 smooth=0 aa=1'
                f' padding=0,0,0,0 spacing=0,0\n'
            )
            fh.write(
                f'common lineHeight=24 base=19 scaleW={w} scaleH={h}'
                f' pages=1 packed=0\n'
            )
            fh.write(f'page id=0 file="{png_name}"\n')
            fh.write(f'chars count={len(glyphs)}\n')
            for gid, gw, gh, gl, gt, xadv, ox, oy in glyphs:
                fh.write(
                    f"char id={gid} x={gl} y={gt} width={gw} height={gh}"
                    f" xoffset={ox} yoffset={oy} xadvance={xadv} page=0 chnl=15\n"
                )

        print(f"  fonts/{png_name}  ({n_glyphs} glyphs, {w}×{h})")
        count += 1

    return count


def extract_pyr(mis_path, out_dir):
    """Extract Cpyr particle sprite strips from a MIS file.  Returns count written."""
    with open(mis_path, "rb") as f:
        data = f.read()

    particles_dir = os.path.join(out_dir, "particles")
    os.makedirs(particles_dir, exist_ok=True)

    count = 0
    for res_type, res_id, raw in _iter_iff_resources(data):
        if res_type != "Cpyr":
            continue

        tags = _parse_inner_tags(raw)

        if _PYDT_TAG not in tags or _PYPL_TAG not in tags or _PIX4_TAG not in tags:
            continue

        pydt_off, pydt_sz = tags[_PYDT_TAG]
        pypl_off, pypl_sz = tags[_PYPL_TAG]
        pix4_off, pix4_sz = tags[_PIX4_TAG]

        if pix4_sz < 65536:
            continue

        atlas = raw[pix4_off : pix4_off + 65536]

        # Parse particle descriptors from PYDT
        num_particles = struct.unpack_from("<I", raw, pydt_off)[0]
        pp = pydt_off + 4
        particles = []
        for _ in range(num_particles):
            pid      = struct.unpack_from("<H", raw, pp)[0]; pp += 2
            num_spr  = raw[pp]; pp += 1
            spr_size = raw[pp]; pp += 1
            sprites = []
            for _ in range(num_spr):
                ax   = raw[pp]; pp += 1
                ay   = raw[pp]; pp += 1
                ox   = raw[pp]; pp += 1
                oy   = raw[pp]; pp += 1
                sw   = raw[pp]; pp += 1
                sh   = raw[pp]; pp += 1
                yext = raw[pp]; pp += 1
                pp  += 1  # unknown byte, always 0 on PS1
                sprites.append((ax, ay | (256 if yext else 0), ox, oy, sw, sh))
            particles.append((pid, spr_size, sprites))

        # Parse PYPL palettes: 16 colors per sprite, sprites ordered particle0_spr0, p0_spr1, ...
        pal_off = pypl_off
        for pid, spr_size, sprites in particles:
            spr_palettes = []
            for _ in sprites:
                pal = _decode_pypl(raw, pal_off, 16)
                pal_off += 16 * 2
                spr_palettes.append(pal)

            if spr_size == 0 or not sprites:
                continue

            # Build vertical sprite strip: spr_size × (spr_size * num_frames)
            strip_w = spr_size
            strip_h = spr_size * len(sprites)
            rgba = bytearray(strip_w * strip_h * 4)

            for frame_idx, ((ax, ay, ox, oy, sw, sh), pal) in enumerate(zip(sprites, spr_palettes)):
                frame_y_base = frame_idx * spr_size
                for row in range(sh):
                    for col in range(sw):
                        dst_x = ox + col
                        dst_y = frame_y_base + oy + row
                        if dst_x >= spr_size or dst_y >= strip_h:
                            continue
                        idx = _pix4_get(atlas, ax + col, ay + row)
                        r, g, b, a = pal[idx]
                        off = (dst_y * strip_w + dst_x) * 4
                        rgba[off:off+4] = bytes([r, g, b, a])

            img = Image.frombytes("RGBA", (strip_w, strip_h), bytes(rgba))
            name = f"cpyr{res_id:03d}_p{pid:03d}.png"
            img.save(os.path.join(particles_dir, name))
            print(f"  particles/{name}  ({len(sprites)} frames @ {spr_size}px)")
            count += 1

    return count


_CSHD_TAG = b"Cshd"


def _parse_cshd_palette_types(data):
    """Parse Cshd resource to build a palette-source fallback map.

    Cshd stores render-mode metadata for each texture slot.  In practice all
    Cbmps carry their own TULP palette so this fallback is never exercised, but
    it is kept as a safety net for levels where a Cbmp has no TULP.

    Returns dict mapping cbmp_id -> slot_type (non-zero entries only).
    """
    for res_type, res_id, raw in _iter_iff_resources(data):
        if res_type != "Cshd":
            continue
        if len(raw) < 10:
            continue
        n_entries = struct.unpack_from("<H", raw, 2)[0]
        palette_type = {}
        p = 10  # entries start after 5-u16 header
        for _ in range(n_entries):
            if p + 6 > len(raw):
                break
            slot_id    = struct.unpack_from("<H", raw, p)[0]
            slot_type  = struct.unpack_from("<H", raw, p + 2)[0]
            p += 6
            if slot_type > 0:
                palette_type[slot_id] = slot_type
        return palette_type
    return {}


def extract_cbmp(mis_path, out_dir):
    """Extract all Cbmp textures from a MIS file.  Returns count written."""
    with open(mis_path, "rb") as f:
        data = f.read()

    textures_dir = os.path.join(out_dir, "textures")
    os.makedirs(textures_dir, exist_ok=True)

    # Pass 1: collect all palettes and pixel data indexed by resource ID.
    # Use a lenient inner-tag parse (truncated resources are clamped).
    cbmp_palettes = {}   # res_id -> palette list
    cbmp_pdat     = {}   # res_id -> raw pixel bytes (65536, zero-padded)
    file_order    = []   # res_ids in file order, for sequential fallback

    for res_type, res_id, raw in _iter_iff_resources(data):
        if res_type != "Cbmp":
            continue

        tags_lenient = {}
        p = 0
        while p + 8 <= len(raw):
            tag = struct.unpack_from("<I", raw, p)[0]
            sz  = struct.unpack_from("<I", raw, p + 4)[0]
            if sz < 8:
                break
            payload_sz = min(sz - 8, len(raw) - p - 8)
            tags_lenient[tag] = (p + 8, payload_sz)
            if p + sz > len(raw):
                break
            p += sz

        if _PLUT_TAG in tags_lenient:
            plut_off, plut_sz = tags_lenient[_PLUT_TAG]
            if plut_sz >= 12 + 256 * 2:
                cbmp_palettes[res_id] = _decode_plut(raw, plut_off)

        if _PDAT_TAG in tags_lenient:
            pdat_off, pdat_avail = tags_lenient[_PDAT_TAG]
            indices = bytes(raw[pdat_off : pdat_off + pdat_avail])
            if len(indices) < 256 * 256:
                indices += bytes(256 * 256 - len(indices))
            cbmp_pdat[res_id] = indices
            file_order.append(res_id)

    # Fallback palette map from Cshd (used only when a Cbmp has no TULP).
    cshd_types = _parse_cshd_palette_types(data)

    # Build final palette assignment: own TULP first, then Cshd fallback,
    # then sequential inheritance from the preceding cbmp that has a palette.
    last_pal_id = None
    palette_for = {}  # res_id -> which cbmp_id's palette to use
    for res_id in file_order:
        if res_id in cbmp_palettes:
            palette_for[res_id] = res_id
            last_pal_id = res_id
        else:
            # No own PLUT: check Cshd first, fall back to sequential.
            cshd_src = cshd_types.get(res_id, 0)
            if cshd_src > 0 and cshd_src in cbmp_palettes:
                palette_for[res_id] = cshd_src
            elif last_pal_id is not None:
                palette_for[res_id] = last_pal_id

    # Pass 2: render and save each Cbmp with its resolved palette.
    count = 0
    for res_id in file_order:
        if res_id not in palette_for:
            continue
        palette   = cbmp_palettes[palette_for[res_id]]
        indices   = cbmp_pdat[res_id]
        rgba      = bytes(c for idx in indices for c in palette[idx])
        img       = Image.frombytes("RGBA", (256, 256), rgba)
        name      = f"cbmp{res_id:03d}.png"
        pal_note  = f" (palette from cbmp{palette_for[res_id]:03d})" if palette_for[res_id] != res_id else ""
        img.save(os.path.join(textures_dir, name))
        print(f"  textures/{name}{pal_note}")
        count += 1

    return count


def process_mis(mis_path, out_dir):
    cbmp_count = extract_cbmp(mis_path, out_dir)
    pyr_count  = extract_pyr(mis_path, out_dir)
    fnt_count  = extract_fnt(mis_path, out_dir)
    total = cbmp_count + pyr_count + fnt_count
    if total == 0:
        print("  (no visual resources found)")
    else:
        print(f"  → {cbmp_count} textures, {pyr_count} particle strips, {fnt_count} fonts")
    return total


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    parsed = os.path.join(repo_root, "parsed")

    mis_files = find_game_files("MIS")

    total = 0
    for mis_path in mis_files:
        level = os.path.splitext(os.path.basename(mis_path))[0]
        out_dir = os.path.join(parsed, level)
        print(f"\n{level}.MIS → parsed/{level}/")
        total += process_mis(mis_path, out_dir)

    print(f"\nDone — {total} visual assets extracted across {len(mis_files)} levels.")


if __name__ == "__main__":
    main()
