#!/usr/bin/env python3
"""Extract the text strings embedded in the PS1 executable (SLUS_007.39).

The executable holds no images or audio (no TIM/VAB/VAG/SEQ data), but it does
hold hard-coded English text that isn't in FE.MIS's 3-language Ctrs string
tables: the end-of-match stats labels, sound options, "Game Paused", camera
names, the "press … to play the next level" prompts, plus the names of every
file the game loads, and the Sony SDK's debug/CD-driver messages.

PS-X EXE layout: 0x800-byte header; +0x18 u32 text load address (0x80010000),
+0x1c u32 text size. RAM address = file offset - 0x800 + load address.

A string is a NUL-terminated run of printable ASCII (plus \\n, \\t) containing at
least two letters, that starts 4-byte aligned or right after a NUL. Instruction
bytes in the code section sometimes look like short text ("LQB4<", "#Hb"): real
strings sit in tables, so a string shorter than 6 chars is kept only if a string
of 6+ chars lies within 256 bytes (every real short string — "Jets", "Quit",
"slim", "CW\\" — passes; every code false positive is isolated).

Classification is a heuristic:
  file_ref   ends in a disc-file extension (.mis .lsc .dem .wve .fsv); each gets
             `on_disc`, checked case-insensitively against the extracted disc.
             Name templates ("%s_%c%d.dem", ".mis") get `template: true` instead.
  sdk        Sony PsyQ library data: CD/graphics/MDEC function and status names,
             the library copyright line (0x80012240-0x80012d60 and 0x8009cc30)
  debug      contains a printf % format, a newline, or an RCS "$Id:" tag
  game_text  everything else: player-facing labels and prompts (stats screen,
             sound options, pause, camera names), the memory-card file name
             BASLUS-00739FCOPLAPD, and level/folder names used to build paths

Findings: brfe3.wve / poe3.wve (an "E3" level's briefing and post-mission
movies) and alang.lsc (a language-select loading screen) are referenced but
not on the retail disc.

Output: parsed/FE/exe_strings.json (FE holds the game-wide front-end data).
"""

import json
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import ps1_dir

_HEADER = 0x800
_STRING_RE = re.compile(rb"[\x20-\x7e\n\t]{3,}\x00")
_FILE_RE = re.compile(r"\.(mis|lsc|dem|wve|fsv)$", re.I)
_DEBUG_RE = re.compile(r"%|\n|\$Id:")
_SDK_RE = re.compile(r"^(Cdl|CD[_ ]|CDROM|CD001|ClearImage|LoadImage|StoreImage|MoveImage|"
                     r"MDEC_|wait \(|Library Programs|0123456789|DiskError|DataEnd|"
                     r"Acknowledge|Complete$|DataReady|NoIntr|none$|C:\\PSX)")
_TABLE_REACH = 256    # bytes; see "code false positives" above


def _strings(exe):
    found = []
    for m in _STRING_RE.finditer(exe, _HEADER):
        off, text = m.start(), m.group()[:-1]
        if not (off % 4 == 0 or exe[off - 1] == 0):
            continue
        if not re.search(rb"[A-Za-z]{2}", text):
            continue
        found.append((off, text.decode("ascii")))
    long_offs = [o for o, t in found if len(t) >= 6]
    for off, text in found:
        if len(text) >= 6 or any(abs(off - o) <= _TABLE_REACH for o in long_offs):
            yield off, text


def _disc_files(root):
    names = set()
    for _, _, files in os.walk(root):
        names.update(f.lower() for f in files)
    return names


def extract(exe_path, disc_root, out_path):
    with open(exe_path, "rb") as f:
        exe = f.read()
    if exe[:8] != b"PS-X EXE":
        raise ValueError(f"{exe_path} is not a PS-X EXE")
    load_addr = struct.unpack_from("<I", exe, 0x18)[0]
    on_disc = _disc_files(disc_root)

    records = []
    for off, text in _strings(exe):
        if _FILE_RE.search(text):
            kind = "file_ref"
        elif _SDK_RE.search(text):
            kind = "sdk"
        elif _DEBUG_RE.search(text):
            kind = "debug"
        else:
            kind = "game_text"
        rec = {"address": f"0x{off - _HEADER + load_addr:08x}", "offset": off,
               "kind": kind, "text": text}
        if kind == "file_ref":
            if "%" in text or text.startswith("."):
                rec["template"] = True
            else:
                rec["on_disc"] = os.path.basename(text.replace("\\", "/")).lower() in on_disc
        records.append(rec)

    counts = {}
    for r in records:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    missing = sorted({r["text"] for r in records if r.get("on_disc") is False})
    out = {"source": os.path.basename(exe_path), "load_address": f"0x{load_addr:08x}",
           "counts": counts, "missing_files": missing, "strings": records}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    return counts, missing


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    disc = ps1_dir()
    exe = os.path.join(disc, "SLUS_007.39")
    if not os.path.exists(exe):
        print(f"{exe} not found (extract the PS1 ISO there, or set FCOP_PS1_DIR)", file=sys.stderr)
        sys.exit(1)
    out = os.path.join(repo_root, "parsed", "FE", "exe_strings.json")
    counts, missing = extract(exe, disc, out)
    print(f"SLUS_007.39 → parsed/FE/exe_strings.json  {counts}")
    print(f"Referenced but not on the disc: {', '.join(missing) or 'none'}")


if __name__ == "__main__":
    main()
