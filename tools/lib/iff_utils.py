"""Shared IFF parser for Future Cop: L.A.P.D. MIS archives, and game-file lookup."""

import glob
import os
import struct
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools/lib/ -> repo


def ps1_dir():
    """The extracted PS1 disc: $FCOP_PS1_DIR, else ps1/ next to the repo (outside git)."""
    return os.environ.get("FCOP_PS1_DIR") or os.path.join(os.path.dirname(_REPO_ROOT), "ps1")


def find_game_files(ext="MIS"):
    """Sorted paths of <ps1_dir>/CW/*.<ext> and <ps1_dir>/PA/*.<ext> (exits if there are none)."""
    root = ps1_dir()
    files = sorted(glob.glob(os.path.join(root, "CW", "*." + ext)) +
                   glob.glob(os.path.join(root, "PA", "*." + ext)))
    if not files:
        print(f"No .{ext} files found in {root}/CW or {root}/PA "
              "(extract the PS1 ISO there, or set FCOP_PS1_DIR)", file=sys.stderr)
        sys.exit(1)
    return files

_IFF_BLOCK_SIZE = 0x6000


def iter_iff_resources(data):
    """Parse IFF DATA section; yield (res_type, res_id, raw_payload) for each resource."""
    def ru32(off):
        return struct.unpack_from("<I", data, off)[0]

    resources = {}
    current_res = None
    pos = 0x18

    while pos < len(data):
        if data[pos:pos+4] == b"LLIF":
            pos = ((pos // _IFF_BLOCK_SIZE) + 1) * _IFF_BLOCK_SIZE
            continue
        if pos + 20 > len(data):
            break
        chunk_size = ru32(pos + 4)
        if chunk_size < 8 or chunk_size > _IFF_BLOCK_SIZE:
            break
        sub_tag = data[pos+16:pos+20]
        if sub_tag == b"RDHS":
            res_type = data[pos+24:pos+28][::-1].decode("ascii", errors="?")
            res_id   = ru32(pos + 28)
            res_size = ru32(pos + 32)
            current_res = (res_type, res_id, res_size)
            key = f"{res_type}_{res_id}"
            if key not in resources:
                resources[key] = {"type": res_type, "id": res_id, "size": res_size, "payloads": []}
        elif sub_tag == b"TADS" and current_res:
            payload = data[pos+20 : pos+chunk_size]
            key = f"{current_res[0]}_{current_res[1]}"
            if key in resources:
                resources[key]["payloads"].append(payload)
        pos += chunk_size

    for r in resources.values():
        raw = b"".join(r["payloads"])
        yield r["type"], r["id"], raw
