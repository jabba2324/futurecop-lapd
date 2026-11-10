#!/usr/bin/env python3
"""Extract PS1 SPU ADPCM audio from Future Cop: L.A.P.D. MIS archives.

Three audio block types:
  BGAV — voice clips (dialogue/VO). Outer 16-byte header + 8-byte inner header +
          raw SPU ADPCM. Channels field: 1=mono, 2=dual (two sequential mono clips
          packed split-half). End-of-sample flag: SPU block byte[1] bit 0 (0x01).
          Output: voice_NNN.wav (mono) or voice_NNNa.wav + voice_NNNb.wav (dual)

  MGAV — music tracks (looping samples loaded into PS1 SPU RAM). Outer 16-byte
          header + 12-byte inner header + raw SPU ADPCM. Sequential slot index stored
          in inner header bytes 6-7. All blocks use flag 0x02 (loop-start) throughout.
          Stereo: split-half (first half = L, second half = R). Rate: 14256 Hz
          (SPU pitch 0x52c). The game streams the slots in order and loops to slot 0.
          Output: sfx_NNN.wav

  Cvkb — PS1 SPU ADPCM sound effect banks in the IFF DATA section. Each Cvkb
          resource is a VAB Voice Bank containing multiple individual SFX samples
          separated by end-of-sample flags. Mono. Each sample is written at the
          native rate of the first Cvkh tone that plays it (centre note / fine tune,
          see extract_sfx_meta.py): 11025, 16000 or 22050 Hz mostly, and 11025 Hz
          if no tone uses it. Confirmed against the game's own SPU pitch (Cvkh +18):
          1211 of the 1701 tones play exactly at their native rate, at every rate.
          Also confirmed by ear (2026-11-10): M2C vkb01_s012 and vkb50_s024 (16 kHz)
          and vkb50_s022 and vkb13_s000 (22 kHz) sound right at their native rate and
          too slow at 11025 Hz.
          About 10% of samples are shared by tones with different native rates (the
          game plays them both ways), so no single rate is right for those; the
          per-tone renders in sfx_tones/ (extract_sfx_meta.py) are what the game plays.
          Output: vkbNN_sMMM.wav
"""

import array
import os
import struct
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import find_game_files, iter_iff_resources

VOICE_RATE = 11025   # BGAV voice clips
SFX_RATE   = 14256   # MGAV music: 0x800244f8 passes 0x37b4 (14260) -> SPU pitch 0x52c;
                     # a 0x2fc0-byte channel block is then exactly 1.5 s
VKB_RATE   = 11025   # Cvkb samples no Cvkh tone references

_IFF_BLOCK_SIZE = 0x6000  # IFF DATA section block alignment

_F0 = [0.0, 60.0, 115.0, 98.0, 122.0]
_F1 = [0.0,  0.0, -52.0, -55.0, -60.0]


def _decode_channel(data, end_on_loop_end=True):
    """Decode PS1 SPU ADPCM blocks. Returns (samples, bytes_consumed)."""
    samples = array.array("h")
    prev1, prev2 = 0.0, 0.0
    pos = 0
    while pos + 16 <= len(data):
        hdr = data[pos]
        flags = data[pos + 1]
        shift = hdr & 0x0F
        filt = (hdr >> 4) & 0x07
        if filt > 4:
            filt = 0
        for byte in data[pos + 2 : pos + 16]:
            for nibble in (byte & 0x0F, (byte >> 4) & 0x0F):
                s = nibble if nibble < 8 else nibble - 16
                s = s * (1 << max(0, 12 - shift))
                s += (prev1 * _F0[filt] + prev2 * _F1[filt] + 32.0) / 64.0
                s = max(-32768.0, min(32767.0, s))
                samples.append(int(s))
                prev2, prev1 = prev1, s
        pos += 16
        if end_on_loop_end and (flags & 1):  # bit 0 = end-of-sample (stop)
            break
    return samples, pos


def decode_spu_adpcm(data, channels=1, end_on_loop_end=True):
    if channels == 1:
        return _decode_channel(data, end_on_loop_end)[0]
    # Stereo BGAV: two sequential mono clips packed into one block (split-half).
    # Callers should use decode_bgav_dual() instead to get both clips separately.
    half = len(data) // 2
    return _decode_channel(data[:half], end_on_loop_end)[0]


def decode_bgav_dual(data):
    """Return (clip_a, clip_b) — two sequential mono clips from a split-half BGAV block."""
    half = len(data) // 2
    return _decode_channel(data[:half])[0], _decode_channel(data[half:])[0]


def _rvws_name(data, pos):
    """Return the stream name from a RVWS block at pos, or None.

    Block layout: 4-byte magic + 4-byte size + 8-byte outer tail + null-terminated name.
    Name starts at pos+16.
    """
    if data[pos:pos+4] != b"RVWS":
        return None
    sz = struct.unpack_from("<I", data, pos+4)[0]
    if sz != 36:
        return None
    raw = data[pos+16:pos+sz]
    null = raw.find(b"\x00")
    if null != -1:
        raw = raw[:null]
    try:
        name = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    if name.startswith("ELIF") and all(0x20 <= ord(c) < 0x7f for c in name):
        name = name[4:]  # strip "ELIF" prefix
        # strip full or partial ".stream" suffix (name field is only 20 bytes so may be truncated)
        for n in range(len(".stream"), 0, -1):
            if name.endswith(".stream"[:n]):
                name = name[:-n]
                break
        return name
    return None


def extract_bgav_blocks(data):
    """Yield (audio_bytes, channels, stream_name) for every BGAV block.

    stream_name comes from the RVWS block that immediately preceded this BGAV;
    None if no RVWS was seen.  Multiple consecutive BGAVs can share a stream name.
    """
    pos = 0
    current_stream = None
    while pos < len(data) - 4:
        tag = data[pos:pos+4]
        if tag == b"RVWS":
            sz = struct.unpack_from("<I", data, pos+4)[0]
            name = _rvws_name(data, pos)
            if name is not None:
                current_stream = name
            # All real RVWS are exactly 36 bytes. Only skip a block whose name parses:
            # LLIF fill can end in a stray 8-byte "RVWS" + size copy right before the
            # real header (M4A1 7_06_01), and skipping 36 bytes from there jumps past it.
            pos += 36 if sz == 36 and name is not None else 1
        elif tag == b"BGAV":
            outer_sz = struct.unpack_from("<I", data, pos + 4)[0]
            if outer_sz > 24 and pos + outer_sz <= len(data):
                inner = data[pos + 16 : pos + 24]
                if inner[:4] == b"BGAV":
                    channels = struct.unpack_from("<H", inner, 6)[0]
                    if channels not in (1, 2):
                        channels = 1
                    audio = data[pos + 24 : pos + outer_sz]
                    if len(audio) % 16 == 0 and len(audio) >= 16:
                        yield audio, channels, current_stream
                pos += max(1, outer_sz)
            else:
                pos += 1
        else:
            pos += 1


def extract_mgav_blocks(data):
    """Yield (audio_bytes, slot_idx, stream_name) for every MGAV block.

    Outer header: 16 bytes. Inner header: 12 bytes (MGAV magic + count + slot_idx + pitch).
    Audio starts at outer_offset + 28.  stream_name from the preceding RVWS block.
    """
    pos = 0
    current_stream = None
    while pos < len(data) - 4:
        tag = data[pos:pos+4]
        if tag == b"RVWS":
            sz = struct.unpack_from("<I", data, pos+4)[0]
            name = _rvws_name(data, pos)
            if name is not None:
                current_stream = name
            # All real RVWS are exactly 36 bytes. Only skip a block whose name parses:
            # LLIF fill can end in a stray 8-byte "RVWS" + size copy right before the
            # real header (M4A1 7_06_01), and skipping 36 bytes from there jumps past it.
            pos += 36 if sz == 36 and name is not None else 1
        elif tag == b"MGAV":
            outer_sz = struct.unpack_from("<I", data, pos + 4)[0]
            if outer_sz > 100 and pos + outer_sz <= len(data):
                inner = data[pos + 16 : pos + 28]
                if inner[:4] == b"MGAV":
                    slot_idx = struct.unpack_from("<H", inner, 6)[0]
                    audio = data[pos + 28 : pos + outer_sz]
                    if len(audio) % 16 == 0 and len(audio) >= 16:
                        yield audio, slot_idx, current_stream
                pos += max(1, outer_sz)
            else:
                pos += 1
        else:
            pos += 1


def decode_mgav_stereo(data):
    """MGAV stereo: first half = L channel, second half = R channel. Each 1.5 s block
    is decoded from a zero predictor: the blocks were encoded independently (carrying
    the history over puts clicks at a quarter of the joins; resetting leaves none)."""
    half = len(data) // 2
    L = _decode_channel(data[:half], end_on_loop_end=False)[0]
    R = _decode_channel(data[half:], end_on_loop_end=False)[0]
    out = array.array("h")
    for l, r in zip(L, R):
        out.append(l)
        out.append(r)
    return out


def write_wav(path, pcm, channels, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def extract_cvkb_samples(data):
    """Parse the IFF DATA section and yield (res_id, sample_idx, pcm) for each Cvkb sample.

    IFF DATA layout: SHOC blocks at _IFF_BLOCK_SIZE (0x6000) boundaries.
    FILL blocks (tag 'LLIF') advance to the next boundary.
    Each resource has a SHDR block (sub-tag 'RDHS') followed by SDAT blocks (sub-tag 'TADS').
    Cvkb resources contain PS1 SPU ADPCM Voice Bank data: 16-byte IFF prefix + raw VB blocks.
    Individual samples are delimited by SPU end-of-sample flags (block byte[1] bit 0).
    """
    if len(data) < 0x1c:
        return

    def _ru32(off):
        return struct.unpack_from("<I", data, off)[0]

    size_of_data = _ru32(0x0c)  # CTRL field: file offset of SWVR section start
    if size_of_data > len(data):
        size_of_data = len(data)

    # Collect SDAT payloads per resource
    resources = {}
    current_res = None
    pos = 0x18

    while pos < size_of_data:
        if data[pos:pos+4] == b"LLIF":
            pos = ((pos // _IFF_BLOCK_SIZE) + 1) * _IFF_BLOCK_SIZE
            continue
        if pos + 20 > size_of_data:
            break
        chunk_size = _ru32(pos + 4)
        if chunk_size < 8 or chunk_size > _IFF_BLOCK_SIZE:
            break
        sub_tag = data[pos+16:pos+20]
        if sub_tag == b"RDHS":  # SHDR: resource header
            res_type = data[pos+24:pos+28][::-1].decode("ascii", errors="?")
            res_id   = _ru32(pos + 28)
            res_size = _ru32(pos + 32)
            current_res = (res_type, res_id, res_size)
            resources[f"{res_type}_{res_id}"] = {"type": res_type, "id": res_id,
                                                  "size": res_size, "payloads": []}
        elif sub_tag == b"TADS" and current_res:  # SDAT: resource payload
            payload = data[pos+20 : pos+chunk_size]
            key = f"{current_res[0]}_{current_res[1]}"
            if key in resources:
                resources[key]["payloads"].append(payload)
        pos += chunk_size

    for key, r in resources.items():
        if r["type"] != "Cvkb":
            continue
        raw = b"".join(r["payloads"])
        # First 16 bytes are IFF metadata (all zeros); VB data follows
        vb = raw[16 : r["size"]]
        i = 0
        sample_idx = 0
        while i < len(vb):
            pcm, consumed = _decode_channel(vb[i:])
            if len(pcm) > 56:  # skip null-separator blocks (≤ 1 ADPCM block = 28 samples)
                yield r["id"], sample_idx, i, pcm
                sample_idx += 1
            i += consumed


def cvkh_native_rates(data):
    """{(bank id, VB offset): native Hz} from the first Cvkh tone using each sample.

    Tone layout in extract_sfx_meta.py: +7 centre note, +8 fine tune, +20 sample
    offset into the Cvkb payload (its 16-byte prefix included, except that an offset
    of 0 means the first sample).
    """
    rates = {}
    for rtype, rid, h in iter_iff_resources(data):
        if rtype != "Cvkh" or len(h) < 16:
            continue
        n = h[4]
        for off in struct.unpack_from("<%dI" % n, h, 16):
            e = h[off:off + 24]
            if len(e) < 24:
                continue
            note, fine = e[7], e[8]
            sample_off = struct.unpack_from("<I", e, 20)[0]
            key = (rid, max(sample_off - 16, 0))  # offset 0 = the bank's first sample
            rates.setdefault(key, round(11025 * 2 ** ((84 - (note - fine / 128)) / 12)))
    return rates


def process_mis(mis_path, out_dir):
    with open(mis_path, "rb") as f:
        data = f.read()

    voices_dir = os.path.join(out_dir, "voices")
    music_dir  = os.path.join(out_dir, "music")
    sfx_dir    = os.path.join(out_dir, "sfx")
    for d in (voices_dir, music_dir, sfx_dir):
        os.makedirs(d, exist_ok=True)

    voice_count = 0
    sfx_count = 0

    # Voice clips (BGAV) — all BGAVs under the same RVWS stream name are one clip.
    # Accumulate PCM chunks per stream, flush when the stream name changes.
    fallback_count = 0
    current_stream = None
    accumulated: list[array.array] = []

    def flush_stream():
        nonlocal voice_count, current_stream, accumulated
        if not accumulated:
            return
        combined = array.array("h")
        for chunk in accumulated:
            combined.extend(chunk)
        name = f"{current_stream}.wav" if current_stream else f"voice_{fallback_count:03d}.wav"
        write_wav(os.path.join(voices_dir, name), combined, 1, VOICE_RATE)
        print(f"  voices/{name}  {len(combined)/VOICE_RATE:.2f}s")
        voice_count += 1
        accumulated = []

    for audio, channels, stream in extract_bgav_blocks(data):
        if stream != current_stream:
            flush_stream()
            if not stream:
                fallback_count += 1
            current_stream = stream

        if channels == 2:
            clip_a, clip_b = decode_bgav_dual(audio)
            if clip_a:
                accumulated.append(clip_a)
            if clip_b:
                accumulated.append(clip_b)
        else:
            pcm = decode_spu_adpcm(audio, 1, end_on_loop_end=True)
            if pcm:
                accumulated.append(pcm)

    flush_stream()  # write the final stream

    # Music tracks (MGAV) — group by RVWS stream name, assemble in slot order.
    # Split-half stereo (L=first half, R=second half) at SFX_RATE, block by block.
    streams: dict[str | None, list[tuple[int, bytes]]] = {}
    stream_order: list[str | None] = []
    for audio, slot_idx, stream in extract_mgav_blocks(data):
        if stream not in streams:
            streams[stream] = []
            stream_order.append(stream)
        streams[stream].append((slot_idx, audio))

    for stream in stream_order:
        slots = sorted(streams[stream], key=lambda x: x[0])
        combined = array.array("h")
        for _, audio in slots:
            combined.extend(decode_mgav_stereo(audio))
        if not combined:
            continue
        name = f"{stream}.wav" if stream else f"music_{sfx_count:03d}.wav"
        write_wav(os.path.join(music_dir, name), combined, 2, SFX_RATE)
        dur = len(combined) // 2 / SFX_RATE
        print(f"  music/{name}  2ch  {dur:.2f}s")
        sfx_count += 1

    # SFX samples (Cvkb) — PS1 SPU ADPCM sound effects in the IFF DATA section.
    vkb_count = 0
    rates = cvkh_native_rates(data)
    for res_id, sample_idx, vb_off, pcm in extract_cvkb_samples(data):
        name = f"vkb{res_id:02d}_s{sample_idx:03d}.wav"
        write_wav(os.path.join(sfx_dir, name), pcm, 1,
                  rates.get((res_id, vb_off), VKB_RATE))
        vkb_count += 1

    if voice_count == 0 and sfx_count == 0 and vkb_count == 0:
        print("  (no audio blocks found)")
    else:
        print(f"  → {voice_count} voice clips, {sfx_count} music tracks, {vkb_count} SFX samples")
    return voice_count, sfx_count, vkb_count


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    parsed = os.path.join(repo_root, "parsed")

    mis_files = find_game_files("MIS")

    total_voice = total_sfx = total_vkb = 0
    for mis_path in mis_files:
        level = os.path.splitext(os.path.basename(mis_path))[0]
        out_dir = os.path.join(parsed, level)
        print(f"\n{level}.MIS → parsed/{level}/")
        v, s, k = process_mis(mis_path, out_dir)
        total_voice += v
        total_sfx += s
        total_vkb += k

    print(f"\nDone — {total_voice} voice clips + {total_sfx} music tracks + {total_vkb} SFX samples across {len(mis_files)} levels.")


if __name__ == "__main__":
    main()
