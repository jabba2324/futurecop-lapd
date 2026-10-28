#!/usr/bin/env python3
"""Decode the sound-bank headers (Cvkh), the Csfx effect tables and the Cctr markers
of Future Cop: L.A.P.D. PS1 MIS archives.

Unlike iff_utils.iter_iff_resources() this script keeps resources that share a
(type, id) key apart (both Csfx resources have id 1, and iter_iff_resources would
concatenate them) and keeps the SHDR header extras.

SHDR chunk (SHOC chunk with sub-tag 'RDHS'), offsets from the chunk start:
  +0x08 u32 metadata  +0x14 u32 enum  +0x18 4s type (reversed)  +0x1c u32 id
  +0x20 u32 size      +0x24 3 x u32 RPNS offsets   +0x30 2 x u32 code amounts
  +0x38 code bytes (code_amount[0] bytes, then padding) - fc-mit naming.

──────────────────────────────────────────────────────────────────────────────
Cvkh - VAB-like header for the Cvkb sample bank with the SAME resource id.
  The executable stores the Cvkh and Cvkb pointers and pairs them only when the
  resource ids match (sound loader at 0x800236fc, up to 8 banks resident). Confirmed
  by structure too: header bank_size == Cvkb size for all 15 levels.
  +0x00 u32 version (2)
  +0x04 u8  tone count, +0x05 u8 count copy, +0x06 u8 count copy, +0x07 u8 master vol (0x7f)
  +0x08 u32 0
  +0x0c u32 bank_size (== size of the matching Cvkb resource)
  +0x10 count x u32 offset of each tone record (from Cvkh start; records are contiguous)
  "tone" record, 24 bytes. It is really a PROGRAM (the unit Cshd sound_id indexes):
  a 4-byte program header followed by tone-count 20-byte tones (the relocation loop
  0x800219ec walks +0 tones of 20 bytes each and adds the SPU base to every tone's
  sample address; one tone per program on disc). Confirmed by decompilation of the
  playback path: 0x80021f98 selects tone = program + 4 + layer * 20, 0x800223b0 scales
  the left/right volume by tone bytes +1/+2 (/128), 0x80021ad0 programs the SPU voice
  with ADSR1/ADSR2/pitch/start address (mask 0x70093).
    +0  u8  number of tones in the program (always 1)
    +1  u8  program volume (0-127)  - not read by the playback functions above
    +2  u8  program pan (always 64) - (VAB-style program attributes)
    +3  u8  0                       tone (program + 4):
    +4  u8  0                       tone +0
    +5  u8  left volume  (tone +1, x/128, confirmed)
    +6  u8  right volume (tone +2, x/128, confirmed)
    +7  u8  centre note   +8  u8 fine tune (1/128 semitone, subtracted)
    +9  u8  0             +10 u8 120 (constant)   +11..12 0   +13 u8 24 (constant)
        +7..+13 are not read by the playback code (0x80021ad0, 0x800222cc,
        0x800223b0); they look like authoring-tool data (+10 = 120 / +13 = 24 could be
        a VAB-style max key and pitch-bend range; unconfirmed).
    +14 u16 SPU ADSR1 (0x80ff, 0x9cff, ...)   +16 u16 SPU ADSR2 (0x1fc0, 0x5fc0, 0x5fcb, ...)
    +18 u16 SPU pitch used to play the tone (0x1000 = 44100 Hz); the default when the
            caller passes pitch -1 (0x80021f98)
    +20 u32 sample offset into the Cvkb payload (always lands on a 16-byte zero block,
            i.e. the leading block of a PS1 VAG body; confirmed for all 1701 tones)
  Centre note / fine tune encode the sample's native recording rate exactly:
    rate = 11025 * 2 ** ((84 - (note - fine/128)) / 12)
    note 84/0 -> 11025 Hz, 72/0 -> 22050 Hz, 78/57 -> 16000 Hz, 90/57 -> 8000 Hz
  (confirmed arithmetically: the four common note/fine pairs give those rates to
  within 0.01 %). The pitch field is then the default playback rate and often
  differs (pitch-shifted reuse of a sample: several tones may share one offset).
  NOTE: extract_sfx.py decodes every Cvkb sample at 11025 Hz; tones with note 72 or
  78/57 are really 22050 / 16000 Hz recordings (plausible, not yet confirmed by ear).
  SHDR load conditions of banks 2-16 (the "10 header code bytes", decoded with the
  RPN VM in extract_rpns.py; output "load_condition"): bank 1 and 50 load always;
  banks 2-6 load when (v[10] == k || v[13] == k) for k = 0,1,2,3,5, banks 7-11 when
  (v[11] == k || v[14] == k) for k = 16,18,19,20,21 and banks 12-16 when
  (v[12] == k || v[15] == k) for k = 32..36. v[10..12] / v[13..15] are player 1 / 2's
  gun / heavy / special weapon choice (0x800573a4 copies them into the player), so
  banks 2-16 are per-weapon sound banks loaded only when a player carries that weapon.
  Loop info comes from the SPU block flags: end block flag bit 1 set = looping
  sample, loop start = last block with flag bit 2 before the end.

Csfx - Global effect-definition tables (2 per level, both id 1, in the shared
  segment before Cctr id 0; identical in every level except JOKE).
  Payload = a list of 'TCAt' (tACT) records exactly like the Cact actor records:
    +0 4s 'TCAt'  +4 u32 record size (incl. 8-byte header), body follows.
  Parser (0x8003bfd0, confirmed) switches on the class byte: class 86 = table header,
  its u32 at body+20 becomes the "current table" (0 or 1) for the records that
  follow; classes 87-94 call their registration routine (87 0x800767e4, 88 0x80073f88,
  89 0x800703a8, 90 0x8004c884, 91 0x8003bc7c, 92 0x8006eaf0, 93 0x80040dcc,
  94 0x80044770). Placed Cact actors of classes 87-94 are the same records: the actor
  loader 0x8004d6d4 sends them to the same routines (0x80076878 etc.), into table 0.

  WHAT THEY ARE (confirmed unless marked): one effect system. Classes 87-90 and 92-94
  are effect DEFINITIONS: each record is copied whole into a heap block and stored in
  a 2 x 256 registry 0x800a7040[table][id] (0x8003c404), so the id byte is unique per
  table across these classes (verified: no duplicates in any level). Table 1 holds
  the player's variants: 0x8003bf50 picks table 1 when the effect's owner is a player
  (class 1 / 18), table 0 otherwise. An effect is started by 0x800402dc(owner, id,
  ..., table) -> 0x8003d680 / 0x8003e984, which creates an instance through the
  class's create function and runs it through its per-class handler:
    87 particle emitter (create 0x800762b0, handler 0x80075698): a pool of Cpyr
       sprite particles (draw 0x800752e0 -> Cpyr lookup 0x8005a6a4) with a 3-key RGB
       colour ramp and an emission direction/speed.
    88 beam (0x80074024 / 0x80073bbc): a line between two attachment points drawn by
       0x8007377c / 0x80070e48 that applies damage (method 0xe, amount = damage
       field) to actors it touches - lasers / electric arcs.
    89 colour-ramped primitive (0x8007043c / 0x8006ff44, draw 0x8006f898): two
       RGB ramps with their own times, direction/speed - flashes, rings (guessed).
    90 debris / fragment emitter (0x8004c920 / 0x8004c324): spawns child effects
       (0x8003e984, 0x8003d680) and can break up the owner's model (0x8003a8c0)
       (purpose of the fields guessed).
    91 DAMAGE / EXPLOSION SET, the value of BaseEntity.explosion (actor +0x20). Not
       an effect: 0x8003bc7c stores a 28-byte entry in its own 256-slot table
       0x800a0c70[id]. Readers: 0x8003b898 (death explosion), 0x8003bae4 (damage
       stage changes), 0x8003fa34 / 0x8003b838 (created with the actor), 0x8003b7d8
       (class 26), 0x8003b4e0, 0x8003b5ac.
    92 trail / ribbon (0x8006eb80 / 0x8006e814): flag 0x40 allocates a 256-point
       trail buffer (0x8006c84c) (guessed).
    93 single sprite (0x80040e60 / 0x80040874, draw 0x80040734): one Cpyr sprite
       animation (Cpyr id at +0x34, looked up by 0x8005a654) with velocity; at most
       351 alive.
    94 point-particle burst (0x800447f0, init 0x80043430 / 0x80044610): up to 700
       28-byte particles (count halved on low memory) with two RGB colours.

  Common definition header (body offsets; 0x8003c14c copies it into every instance):
    +0   u32 word0 (0 in table 0, 0x02bdd2ec in table 1 - stale tool value)
    +4   u8  class (86..94); bytes 5-7 are 0 in table 0 and junk in table 1
    +8   i32 x, +12 i32 y, +16 i32 z (16.16) - only used for placed effects (flag 1:
         0x8003c2a4 starts the effect there at load); stale tool values in Csfx
    +20  u8  id                 +21 u8 next_id (instance +6; often id + 1: a chain)
    +22  u8  unknown_22 (instance +0x11)   +23 u8 unknown_23
    +24  i16 yaw, +26 i16 pitch (BAM) and +28 i16 distance: offset from the spawn
         point / attachment (0x8003c2a4, 0x8003d9f8)
    +30  i8  attachment point (-1 = owner's default)
    +32  i32 duration, fx16 seconds (instance +0x16 = v * 300 / 65536 clock units;
         0 = class default, e.g. a sprite's animation length)
    +36  i32 second time, fx16 seconds (instance +4; forced >= 0 when next_id is set,
         probably the delay before next_id starts - guessed)
    +40  i16 repeat count (-1 = none; 1 is normalised to "no repeat")
    +42  i16 repeat interval, fx12 seconds (instance +0x14 = v * 300 / 4096)
    +44  u16 unknown_44 (instance +0x18)
    +46  i16 damage, +48 i16 radius: 0x8003e984 applies splash damage (0x800410bc)
         when damage != 0 and radius > 0, else direct damage to the owner (method 9)
    +50  u16 flags: 0x1 placed at load, 0x8 inherit the parent's transform,
         0x10 small / 0x2000 big camera shake within 0x20000 of a player (0x80057358,
         300 / 1000), 0x20 screen effect at the position (0x80025d6c), others unknown
    +52  class-specific size: 87/88/89 i16 at +52, 92 at +76, 93 at +54, 94 i8 at +80
         (negative = relative to the owner, 0x8003db70)
  Class-specific fields beyond +52 are decoded where the create function makes the
  meaning clear ("fields"), the rest stays in "body" (hex).

  Class 91 entry (body offsets; runtime entry layout in brackets):
    +20 u8 id   +21 u8 alt_effect [0x1a]: 50 % replacement for the death effect when
    flags bit 0 is clear, and the only effect of "mode 0" calls
    +22 u16 flags [0x18]   +24 u8 created_effect [0]: started when the actor is
    created   +25 u8 class26_effect [1]   +26/27 u8 unknown
    +28 5 x u8[4] groups [4..23]: group k is used when bit k of the caller's mask is
    set; byte 0 = effect on destruction, bytes 1/2/3 = effect while health is below
    75 % / 50 % / 25 % of the maximum (0x8003b65c stages, 0x8003b5ac). All values
    are effect ids in table 0 (or 1 for the player), 0 = none.

Cctr - Loader control markers (2 per level, 20 bytes each).
  Handler at 0x80025230 (confirmed by disassembly) ignores the payload and only looks
  at the resource id: id 0 sets the loader state global (gp+0x7ec) to 2, id 1 sets
  it to 4, and state 4 makes the resource loop stop. Cctr id 0 sits right after the
  shared segment (RPNS, font, Cshd, Cvkh/Cvkb 1-16, the two Csfx), Cctr id 1 is the
  last resource of every MIS. The 20-byte payload is constant and big-endian (copied
  verbatim from the Mac build): id 0 = (2, 0, 43, 35, 5), id 1 = (1, 0, 43,
  0x01641b38, 0x012b6fe6). The PS1 never reads it: the resource loop 0x80024ec8 only
  looks at the id and then frees the buffer (0x800181d4), so its meaning is lost
  (Mac-build data). id 1's SHOC metadata is 0x280000.

Output in parsed/<LEVEL>/:
  sfx_banks.json         Cvkh banks (+ decoded SHDR load_condition) + decoded program
                         records ("tones"; + the extract_sfx.py file name)
  sfx_tones/vkbNN_tTT.wav every program decoded from its Cvkb offset at its play pitch
  csfx.json              Csfx tables: records with class_name, decoded "fields"
                         (common header + class_fields, or the class-91 set) and the
                         raw body hex
  cctr.json              Cctr markers
"""

import array
import json
import os
import struct
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import find_game_files


_BLOCK = 0x6000
_F0 = [0.0, 60.0, 115.0, 98.0, 122.0]
_F1 = [0.0, 0.0, -52.0, -55.0, -60.0]


# ── IFF walking with SHDR details ─────────────────────────────────────────

def _iter_resources(data):
    """Yield dicts {type,id,enum,metadata,size,rpns_offsets,code_amounts,header_extra,
    data,index,file_offset} for every SHDR resource, in file order, duplicates kept."""
    data_end = min(len(data), struct.unpack_from("<I", data, 0x14)[0] or len(data))
    pos = struct.unpack_from("<I", data, 4)[0]
    cur = None
    index = 0
    while pos + 20 <= data_end:
        tag = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if tag == b"LLIF" or size < 8 or (pos % _BLOCK) + size > _BLOCK:
            pos = (pos // _BLOCK + 1) * _BLOCK
            continue
        if tag == b"COHS":
            sub = data[pos + 16:pos + 20]
            if sub == b"RDHS":
                if cur:
                    cur["data"] = bytes(cur["data"])
                    yield cur
                enum, rtype, rid, rsize = struct.unpack_from("<I4sII", data, pos + 20)
                cur = {"type": rtype[::-1].decode("ascii", "replace"), "id": rid, "enum": enum,
                       "metadata": struct.unpack_from("<I", data, pos + 8)[0], "size": rsize,
                       "rpns_offsets": list(struct.unpack_from("<3I", data, pos + 36)),
                       "code_amounts": list(struct.unpack_from("<2I", data, pos + 48)),
                       "header_extra": data[pos + 56:pos + size],
                       "data": bytearray(), "index": index, "file_offset": pos}
                index += 1
            elif sub == b"TADS" and cur is not None:
                cur["data"] += data[pos + 20:pos + size]
        pos += size
    if cur:
        cur["data"] = bytes(cur["data"])
        yield cur


def _shdr_info(r):
    code = r["header_extra"][:r["code_amounts"][0]]
    return {"index": r["index"], "file_offset": hex(r["file_offset"]), "enum": r["enum"],
            "metadata": r["metadata"], "rpns_offsets": r["rpns_offsets"],
            "code_amounts": r["code_amounts"], "code_bytes": code.hex(),
            "header_extra": r["header_extra"].hex()}


# ── SPU ADPCM ─────────────────────────────────────────────────────────────

def _decode_vag(buf, start):
    """Decode from `start` to the first end-flag block. Returns (pcm, n_bytes, loop)."""
    pcm = array.array("h")
    p1 = p2 = 0.0
    pos = start
    loop_start = None
    flags = 0
    while pos + 16 <= len(buf):
        hdr, flags = buf[pos], buf[pos + 1]
        shift, filt = hdr & 0x0F, (hdr >> 4) & 0x07
        if filt > 4:
            filt = 0
        if flags & 4:
            loop_start = pos - start
        for byte in buf[pos + 2:pos + 16]:
            for nib in (byte & 0x0F, byte >> 4):
                s = (nib - 16 if nib >= 8 else nib) * (1 << max(0, 12 - shift))
                s += (p1 * _F0[filt] + p2 * _F1[filt] + 32.0) / 64.0
                s = max(-32768.0, min(32767.0, s))
                pcm.append(int(s))
                p2, p1 = p1, s
        pos += 16
        if flags & 1:
            break
    looping = bool(flags & 2)
    return pcm, pos - start, {"looping": looping,
                              "loop_start_byte": loop_start if looping else None,
                              "loop_start_sample": (loop_start // 16 * 28)
                              if (looping and loop_start is not None) else None}


def _extract_sfx_segments(vkb):
    """Replicate extract_sfx.py's sample split: [(vb_start, sample_index)]."""
    vb = vkb[16:]
    out = []
    i = idx = 0
    while i < len(vb):
        start = i
        n = 0
        while i + 16 <= len(vb):
            n += 28
            flag = vb[i + 1]
            i += 16
            if flag & 1:
                break
        else:
            if i == start:
                break
        if n > 56:
            out.append((start, idx))
            idx += 1
    return dict(out)


def _write_wav(path, pcm, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def _load_condition(r):
    """Pseudo code of the two SHDR load-condition scripts (see extract_rpns.py)."""
    from extract_rpns import script_record
    extra = r["header_extra"]
    s0, s1 = r["code_amounts"]
    out = []
    for code in (extra[:s0], extra[s0:s0 + s1]):
        out.append(script_record(code, 0, None, end=len(code), stop_at_end=False)["pseudo"]
                   if code else [])
    return out


# ── Cvkh ──────────────────────────────────────────────────────────────────

def _parse_cvkh(r, vkb, tone_dir):
    h = r["data"]
    version, n, n2, n3, master, zero, bank_size = struct.unpack_from("<I4BII", h, 0)
    offs = struct.unpack_from("<%dI" % n, h, 16)
    segs = _extract_sfx_segments(vkb) if vkb is not None else {}
    tones = []
    for t, off in enumerate(offs):
        e = h[off:off + 24]
        adsr1, adsr2, pitch = struct.unpack_from("<3H", e, 14)
        sample_off = struct.unpack_from("<I", e, 20)[0]
        note, fine = e[7], e[8]
        native = 11025 * 2 ** ((84 - (note - fine / 128)) / 12)
        play = pitch * 44100 / 4096
        tone = {"tone": t, "raw": e.hex(), "tone_count": e[0], "volume": e[1], "pan": e[2],
                "b3": e[3], "b4": e[4], "left_volume": e[5], "right_volume": e[6],
                "centre_note": note, "fine_tune": fine, "b9": e[9], "b10": e[10],
                "b11": e[11], "b12": e[12], "b13": e[13],
                "adsr1": "0x%04x" % adsr1, "adsr2": "0x%04x" % adsr2,
                "adsr": {"attack_exp": adsr1 >> 15, "attack_shift": (adsr1 >> 10) & 31,
                         "attack_step": (adsr1 >> 8) & 3, "decay_shift": (adsr1 >> 4) & 15,
                         "sustain_level": adsr1 & 15, "sustain_exp": adsr2 >> 15,
                         "sustain_decrease": (adsr2 >> 14) & 1,
                         "sustain_shift": (adsr2 >> 8) & 31, "sustain_step": (adsr2 >> 6) & 3,
                         "release_exp": (adsr2 >> 5) & 1, "release_shift": adsr2 & 31},
                "spu_pitch": pitch, "play_rate_hz": round(play, 1),
                "native_rate_hz": round(native, 1), "sample_offset": sample_off}
        if vkb is not None and sample_off < len(vkb):
            pcm, nbytes, loop = _decode_vag(vkb, sample_off)
            tone.update(loop)
            tone["sample_bytes"] = nbytes
            seg = segs.get(sample_off - 16 if sample_off else 0)
            tone["extract_sfx_file"] = ("sfx/vkb%02d_s%03d.wav" % (r["id"], seg)
                                        if seg is not None else None)
            name = "vkb%02d_t%02d.wav" % (r["id"], t)
            _write_wav(os.path.join(tone_dir, name), pcm, int(round(play)))
            tone["wav"] = "sfx_tones/" + name
        tones.append(tone)
    return {"id": r["id"], "shdr": _shdr_info(r), "load_condition": _load_condition(r),
            "version": version, "tone_count": n,
            "count_copies": [n2, n3], "master_volume": master, "zero": zero,
            "bank_size": bank_size,
            "cvkb_size": len(vkb) if vkb is not None else None,
            "tones": tones}


# ── Csfx ──────────────────────────────────────────────────────────────────

_CSFX_CLASS_NAMES = {
    86: "table_header", 87: "particle_emitter", 88: "beam", 89: "colour_ramp_shape",
    90: "debris_emitter", 91: "damage_explosion_set", 92: "trail", 93: "sprite",
    94: "point_particles",
}

# (name, struct format, body offset); see the Csfx section of the docstring.
_CSFX_COMMON = [
    ("x", "<i", 8), ("y", "<i", 12), ("z", "<i", 16), ("id", "B", 20),
    ("next_id", "B", 21), ("unknown_22", "B", 22), ("unknown_23", "B", 23),
    ("yaw", "<h", 24), ("pitch", "<h", 26), ("distance", "<h", 28),
    ("attach_point", "b", 30), ("unknown_31", "B", 31), ("duration_fx16s", "<i", 32),
    ("time2_fx16s", "<i", 36), ("repeat_count", "<h", 40),
    ("repeat_interval_fx12s", "<h", 42), ("unknown_44", "<H", 44), ("damage", "<h", 46),
    ("damage_radius", "<h", 48), ("flags", "<H", 50),
]


def _rgb(o):
    return [("%s_r" % o[0], "B", o[1]), ("%s_g" % o[0], "B", o[1] + 1),
            ("%s_b" % o[0], "B", o[1] + 2)]


# Class-specific fields (names from the create functions; meanings beyond the
# offsets are guessed unless the docstring says otherwise).
_CSFX_CLASS_FIELDS = {
    87: [("size", "<h", 52), ("particle_count", "<h", 54)] + _rgb(("colour0", 56))
        + _rgb(("colour1", 59)) + _rgb(("colour2", 62))
        + [("unknown_65", "B", 65), ("phase_time_0", "<h", 66), ("phase_time_1", "<h", 68),
           ("phase_time_2", "<h", 70), ("unknown_72", "<h", 72), ("emit_yaw", "<h", 74),
           ("emit_pitch", "<h", 76), ("emit_speed", "<h", 78), ("unknown_80", "<h", 80),
           ("unknown_82", "<h", 82), ("unknown_84", "<h", 84), ("flags2", "<H", 86)],
    88: [("size", "<h", 52), ("unknown_54", "<h", 54), ("unknown_56", "<h", 56)]
        + _rgb(("colour", 59))
        + [("unknown_62", "<h", 62), ("unknown_64", "<h", 64), ("unknown_66", "<h", 66),
           ("unknown_68", "<h", 68), ("unknown_72", "<h", 72), ("flags2", "<H", 74)],
    89: [("size", "<h", 52), ("unknown_54", "B", 54), ("unknown_56", "<h", 56),
         ("ramp0_time", "<h", 58), ("ramp1_time", "<h", 60)]
        + _rgb(("ramp0_start", 62)) + _rgb(("ramp0_end", 65))
        + _rgb(("ramp1_start", 68)) + _rgb(("ramp1_end", 71))
        + [("emit_yaw", "<h", 74), ("emit_pitch", "<h", 76), ("emit_speed", "<h", 78),
           ("unknown_80", "<h", 80), ("unknown_82", "B", 82), ("unknown_84", "<h", 84),
           ("flags2", "<H", 86)],
    90: [("unknown_52", "B", 52), ("unknown_53", "B", 53), ("unknown_54", "B", 54),
         ("unknown_56", "<h", 56), ("unknown_58", "<h", 58), ("unknown_60", "<h", 60),
         ("unknown_62", "<h", 62), ("emit_yaw", "<h", 64), ("emit_pitch", "<h", 66),
         ("unknown_68", "<h", 68), ("unknown_70", "<h", 70), ("unknown_72", "B", 72),
         ("unknown_73", "B", 73), ("flags2", "B", 74)],
    92: [("unknown_52", "<h", 52), ("unknown_54", "<h", 54), ("unknown_56", "<h", 56),
         ("unknown_58", "<h", 58), ("unknown_60", "<h", 60), ("unknown_62", "<h", 62),
         ("segment_length", "<h", 64)] + _rgb(("colour0", 66)) + _rgb(("colour1", 69))
        + [("unknown_72", "<h", 72), ("unknown_74", "B", 74), ("size", "<h", 76),
           ("unknown_78", "<h", 78), ("unknown_80", "<h", 80), ("flags2", "<H", 82)],
    93: [("cpyr_id", "B", 52), ("size", "<h", 54), ("emit_yaw", "<h", 56),
         ("emit_pitch", "<h", 58), ("emit_speed", "<h", 60), ("unknown_62", "<h", 62)]
        + _rgb(("colour0", 65)) + _rgb(("colour1", 69))
        + [("unknown_72", "<h", 72), ("unknown_74", "<h", 74), ("anim_fps", "<h", 76),
           ("unknown_78", "<h", 78), ("unknown_80", "<h", 80), ("unknown_82", "<h", 82),
           ("unknown_84", "<h", 84), ("flags2", "<H", 86)],
    94: [("count", "<h", 52), ("max_count", "<h", 54), ("unknown_56", "<h", 56),
         ("unknown_58", "<h", 58), ("unknown_60", "<h", 60), ("emit_yaw", "<h", 62),
         ("emit_pitch", "<h", 64), ("emit_speed", "<h", 66), ("unknown_68", "<h", 68),
         ("unknown_70", "<h", 70), ("unknown_72", "<h", 72), ("unknown_74", "<h", 74),
         ("unknown_76", "<h", 76), ("unknown_78", "<h", 78), ("size", "b", 80)]
        + _rgb(("colour0", 81)) + _rgb(("colour1", 84))
        + [("unknown_88", "<h", 88), ("unknown_90", "B", 90), ("unknown_91", "B", 91),
           ("unknown_92", "<H", 92), ("unknown_94", "B", 94), ("unknown_95", "B", 95)],
}


def _unpack_fields(body, fields):
    out = {}
    for name, fmt, off in fields:
        if off + struct.calcsize(fmt) <= len(body):
            out[name] = struct.unpack_from(fmt, body, off)[0]
    return out


def _decode_csfx_body(cls, body):
    """Decoded fields of one Csfx / effect-actor body (see the docstring)."""
    if cls == 91 and len(body) >= 48:
        return {"id": body[20], "alt_effect": body[21],
                "flags": struct.unpack_from("<H", body, 22)[0],
                "created_effect": body[24], "class26_effect": body[25],
                "unknown_26": body[26], "unknown_27": body[27],
                "groups": [{"destroyed": body[28 + 4 * k], "below_75": body[29 + 4 * k],
                            "below_50": body[30 + 4 * k], "below_25": body[31 + 4 * k]}
                           for k in range(5)]}
    if cls not in _CSFX_CLASS_FIELDS:
        return None
    f = _unpack_fields(body, _CSFX_COMMON)
    for k in ("duration_fx16s", "time2_fx16s"):
        if k in f:
            f[k.replace("_fx16s", "_seconds")] = round(f[k] / 65536, 4)
    if "repeat_interval_fx12s" in f:
        f["repeat_interval_seconds"] = round(f["repeat_interval_fx12s"] / 4096, 4)
    f["class_fields"] = _unpack_fields(body, _CSFX_CLASS_FIELDS[cls])
    return f


def _parse_csfx(r):
    b = r["data"]
    recs = []
    table = None
    p = 0
    while p + 8 <= len(b):
        tag = b[p:p + 4][::-1].decode("ascii", "replace")
        size = struct.unpack_from("<I", b, p + 4)[0]
        if size < 8:
            break
        body = b[p + 8:p + size]
        w = struct.unpack_from("<5I", body, 0) if len(body) >= 20 else ()
        rec = {"offset": p, "tag": tag, "size": size, "class": body[4] if len(body) > 4 else None}
        rec["class_name"] = _CSFX_CLASS_NAMES.get(rec["class"])
        if w:
            rec.update(word0="0x%08x" % w[0], word1="0x%08x" % w[1], word2="0x%08x" % w[2],
                       word3="0x%08x" % w[3], word4="0x%08x" % w[4])
        if rec["class"] == 86 and len(body) >= 24:
            table = struct.unpack_from("<I", body, 20)[0]
            rec["table_index"] = table
        elif len(body) >= 22:
            rec["entry_id"] = body[20]
            rec["linked_id"] = body[21]
            dec = _decode_csfx_body(rec["class"], body)
            if dec is not None:
                rec["fields"] = dec
        rec["body"] = body.hex()
        recs.append(rec)
        p += size
    classes = {}
    for rec in recs:
        classes[str(rec["class"])] = classes.get(str(rec["class"]), 0) + 1
    return {"id": r["id"], "shdr": _shdr_info(r), "table_index": table,
            "record_count": len(recs), "class_counts": classes,
            "leftover_bytes": len(b) - p, "records": recs}


# ── Cctr ──────────────────────────────────────────────────────────────────

def _parse_cctr(r):
    vals = struct.unpack_from(">%dI" % (len(r["data"]) // 4), r["data"], 0)
    return {"id": r["id"], "shdr": _shdr_info(r),
            "loader_state_set": {0: 2, 1: 4}.get(r["id"]),
            "meaning": {0: "end of shared/global segment",
                        1: "end of mission resources (loader stops)"}.get(r["id"]),
            "values_be_u32": list(vals), "raw": r["data"].hex()}


# ── driver ────────────────────────────────────────────────────────────────

def process_mis(path, out_dir):
    with open(path, "rb") as f:
        data = f.read()
    res = list(_iter_resources(data))
    vkb = {r["id"]: r["data"][:r["size"]] for r in res if r["type"] == "Cvkb"}
    tone_dir = os.path.join(out_dir, "sfx_tones")
    os.makedirs(tone_dir, exist_ok=True)

    banks = [_parse_cvkh(r, vkb.get(r["id"]), tone_dir) for r in res if r["type"] == "Cvkh"]
    csfx = [_parse_csfx(r) for r in res if r["type"] == "Csfx"]
    cctr = [dict(_parse_cctr(r), resource_index=r["index"], resources_total=len(res))
            for r in res if r["type"] == "Cctr"]

    for name, obj in (("sfx_banks.json", banks), ("csfx.json", csfx), ("cctr.json", cctr)):
        with open(os.path.join(out_dir, name), "w") as f:
            json.dump(obj, f, indent=1)
    n_tones = sum(len(b["tones"]) for b in banks)
    print(f"  {len(banks)} Cvkh banks / {n_tones} tones, {len(csfx)} Csfx tables "
          f"({sum(c['record_count'] for c in csfx)} records), {len(cctr)} Cctr")


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    parsed = os.path.join(repo_root, "parsed")
    mis_files = find_game_files("MIS")
    for path in mis_files:
        level = os.path.splitext(os.path.basename(path))[0]
        out_dir = os.path.join(parsed, level)
        os.makedirs(out_dir, exist_ok=True)
        print(f"\n{level}.MIS -> parsed/{level}/")
        process_mis(path, out_dir)


if __name__ == "__main__":
    main()
