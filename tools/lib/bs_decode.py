"""Shared PS1 MDEC "BS v2" still-frame decoder for Future Cop: L.A.P.D.

Used by extract_frontend.py (Cmdc), extract_lsc.py (.LSC loading screens) and
extract_canm.py (CANM talking-panel animations). Pure stdlib; slow but small images.

BS v2 frame layout (all fields little-endian):
  +0  u16  number of 32-bit MDEC words / 2 rounded (ignored here)
  +2  u16  0x3800 magic
  +4  u16  quantisation scale (1 or 2 in this game)
  +6  u16  version (2 everywhere in Future Cop)
  +8  bitstream, read as consecutive u16 LE words, bits MSB-first within each word.

Bitstream: macroblocks of 16x16 pixels in column-major order (top->bottom, then
left->right). Each macroblock = 6 blocks: Cr, Cb, Y(top-left), Y(top-right),
Y(bottom-left), Y(bottom-right). Each block:
  DC  10-bit signed (version 2 = raw, no VLC)
  AC  MPEG-1 table B.14 run/level VLC + sign bit ('11s' = run 0 level 1, since the DC
      is coded separately), '000001' escape = 6-bit run + 10-bit signed level,
      '10' = end of block.
Dequantisation (psx-spx MDEC rules): DC = dc * qt[0]; AC = (level * qt[k] * qscale + 4) / 8
with the MPEG-1 default intra matrix (qt[0] = 2). Inverse DCT is the orthonormal 8x8
float IDCT; colour conversion is JFIF YCbCr with Y/Cb/Cr centred on 0 (+128 after).

Confirmed: every Cmdc/LSC/CANM frame in the game decodes without a VLC error and the
results are clean pictures (title screens, loading screens, mech portraits, animated
"WEAPONS UPGRADE" panels).
"""

import math
import struct

_AC_TABLE = """
0 1 11|1 1 011|0 2 0100|2 1 0101|0 3 00101|3 1 00111|4 1 00110|1 2 000110|5 1 000111
6 1 000101|7 1 000100|0 4 0000110|2 2 0000100|8 1 0000111|9 1 0000101
0 5 00100110|0 6 00100001|1 3 00100101|3 2 00100100|10 1 00100111|11 1 00100011
12 1 00100010|13 1 00100000|0 7 0000001010|1 4 0000001100|2 3 0000001011|4 2 0000001111
5 2 0000001001|14 1 0000001110|15 1 0000001101|16 1 0000001000
0 8 000000011101|0 9 000000011000|0 10 000000010011|0 11 000000010000|1 5 000000011011
2 4 000000010100|3 3 000000011100|4 3 000000010010|6 2 000000011110|7 2 000000010101
8 2 000000010001|17 1 000000011111|18 1 000000011010|19 1 000000011001|20 1 000000010111
21 1 000000010110
0 12 0000000011010|0 13 0000000011001|0 14 0000000011000|0 15 0000000010111
1 6 0000000010110|1 7 0000000010101|2 5 0000000010100|3 4 0000000010011|5 3 0000000010010
9 2 0000000010001|10 2 0000000010000|22 1 0000000011111|23 1 0000000011110
24 1 0000000011101|25 1 0000000011100|26 1 0000000011011
0 16 00000000011111|0 17 00000000011110|0 18 00000000011101|0 19 00000000011100
0 20 00000000011011|0 21 00000000011010|0 22 00000000011001|0 23 00000000011000
0 24 00000000010111|0 25 00000000010110|0 26 00000000010101|0 27 00000000010100
0 28 00000000010011|0 29 00000000010010|0 30 00000000010001|0 31 00000000010000
0 32 000000000011000|0 33 000000000010111|0 34 000000000010110|0 35 000000000010101
0 36 000000000010100|0 37 000000000010011|0 38 000000000010010|0 39 000000000010001
0 40 000000000010000|1 8 000000000011111|1 9 000000000011110|1 10 000000000011101
1 11 000000000011100|1 12 000000000011011|1 13 000000000011010|1 14 000000000011001
1 15 0000000000010011|1 16 0000000000010010|1 17 0000000000010001|1 18 0000000000010000
6 3 0000000000010100|11 2 0000000000011010|12 2 0000000000011001|13 2 0000000000011000
14 2 0000000000010111|15 2 0000000000010110|16 2 0000000000010101|27 1 0000000000011111
28 1 0000000000011110|29 1 0000000000011101|30 1 0000000000011100|31 1 0000000000011011
"""

_VLC = {}
for _item in _AC_TABLE.replace("\n", "|").split("|"):
    if _item.strip():
        _r, _l, _c = _item.split()
        _VLC[_c] = (int(_r), int(_l))

_ZIGZAG = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48,
           41, 34, 27, 20, 13, 6, 7, 14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22,
           15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55,
           62, 63]

# MPEG-1 default intra quantiser matrix (raster order) with the PS1 DC entry of 2.
_QT = [2, 16, 19, 22, 26, 27, 29, 34, 16, 16, 22, 24, 27, 29, 34, 37,
       19, 22, 26, 27, 29, 34, 34, 38, 22, 22, 26, 27, 29, 34, 37, 40,
       22, 26, 27, 29, 32, 35, 40, 48, 26, 27, 29, 32, 35, 40, 48, 58,
       26, 27, 29, 34, 38, 46, 56, 69, 27, 29, 35, 38, 46, 56, 69, 83]

_COS = [[(math.sqrt(0.5) if u == 0 else 1.0) * math.cos((2 * x + 1) * u * math.pi / 16) / 2
         for u in range(8)] for x in range(8)]

BS_MAGIC = 0x3800


class _BitReader:
    def __init__(self, data):
        n = len(data) // 2
        self.bits = "".join(format(w, "016b") for w in struct.unpack_from("<%dH" % n, data, 0))
        self.pos = 0

    def read(self, n):
        v = int(self.bits[self.pos:self.pos + n], 2)
        self.pos += n
        return v


def _signed10(v):
    return v - 1024 if v >= 512 else v


def _decode_block(br, qscale):
    coef = [0] * 64
    coef[0] = _signed10(br.read(10)) * _QT[0]
    bits = br.bits
    k = 0
    while True:
        p = br.pos
        if bits[p:p + 2] == "10":                       # end of block
            br.pos += 2
            break
        if bits[p:p + 6] == "000001":                   # escape
            br.pos += 6
            run = br.read(6)
            level = _signed10(br.read(10))
        else:
            for ln in range(2, 17):
                code = bits[p:p + ln]
                if code in _VLC:
                    break
            else:
                raise ValueError("invalid AC code at bit %d" % p)
            run, level = _VLC[code]
            br.pos += ln
            if br.read(1):
                level = -level
        k += run + 1
        if k > 63:
            raise ValueError("AC coefficient index overflow")
        z = _ZIGZAG[k]
        mag = (abs(level) * _QT[z] * qscale + 4) // 8
        coef[z] = mag if level >= 0 else -mag

    tmp = [0.0] * 64
    for y in range(8):
        row = coef[y * 8:y * 8 + 8]
        nz = [(u, c) for u, c in enumerate(row) if c]
        for x in range(8):
            cx = _COS[x]
            tmp[y * 8 + x] = sum(c * cx[u] for u, c in nz)
    out = [0.0] * 64
    for x in range(8):
        col = [tmp[v * 8 + x] for v in range(8)]
        for y in range(8):
            cy = _COS[y]
            out[y * 8 + x] = sum(col[v] * cy[v] for v in range(8))
    return out


def _clamp(v):
    v = int(round(v + 128))
    return 0 if v < 0 else 255 if v > 255 else v


def decode_bs(frame, width, height):
    """Decode one BS v2 frame (starting at its 8-byte header) to a list of RGB tuples.

    Returns (pixels_row_major, info) where info = {"qscale", "version", "mdec_words"}.
    """
    words, magic, qscale, version = struct.unpack_from("<4H", frame, 0)
    if magic != BS_MAGIC:
        raise ValueError("not a BS frame (magic 0x%04x)" % magic)
    if version != 2:
        raise ValueError("only BS version 2 is supported (got %d)" % version)
    br = _BitReader(frame[8:])
    mbw, mbh = (width + 15) // 16, (height + 15) // 16
    stride = mbw * 16
    rgb = [(0, 0, 0)] * (stride * mbh * 16)
    for mx in range(mbw):
        for my in range(mbh):
            cr = _decode_block(br, qscale)
            cb = _decode_block(br, qscale)
            ys = [_decode_block(br, qscale) for _ in range(4)]
            for j in range(16):
                base = (my * 16 + j) * stride + mx * 16
                yrow = ys[(j // 8) * 2:(j // 8) * 2 + 2]
                crow = (j // 2) * 8
                for i in range(16):
                    y = yrow[i // 8][(j % 8) * 8 + (i % 8)]
                    r_ = cr[crow + i // 2]
                    b_ = cb[crow + i // 2]
                    rgb[base + i] = (_clamp(y + 1.402 * r_),
                                     _clamp(y - 0.344136 * b_ - 0.714136 * r_),
                                     _clamp(y + 1.772 * b_))
    if stride != width or mbh * 16 != height:
        rgb = [rgb[y * stride + x] for y in range(height) for x in range(width)]
    return rgb, {"qscale": qscale, "version": version, "mdec_words": words}


def decode_bs_image(frame, width, height):
    """Like decode_bs() but returns a Pillow RGB Image (Pillow imported lazily)."""
    from PIL import Image
    px, info = decode_bs(frame, width, height)
    im = Image.new("RGB", (width, height))
    im.putdata(px)
    return im, info
