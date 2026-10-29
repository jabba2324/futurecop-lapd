#!/usr/bin/env python3
"""Which Csac spawners have their actor in the level at the start (no script run).

Reads parsed/<LEVEL>/actors_decoded.json, rpns.json and fun.json (decode_actors.py,
extract_rpns.py, extract_fun.py) and writes parsed/<LEVEL>/spawns.json:
  {"start": [csac ids], "waiting": [csac ids]}

The game's spawner (SLUS_007.39 load 0x80060e74, tick 0x800616a0, spawn 0x8006125c,
messages 0x800614bc) creates its actor at the Csac's own position when its timer runs
out, if it isn't disabled (CASt flags bit 0), has budget left, fewer than max_alive are
alive, and its spawn condition script returns non-zero. Message 71 queues a spawn even
while disabled; message 70 with argument 1 enables it and spawns on the next frame.
A spawner is counted as present at the start when either
  - it is enabled and its spawn condition is the plain "return 1" (Crime War levels:
    the first spawn comes within a few seconds), or
  - a level function fires at once and spawns or enables it: a Cfun whose condition
    is "return (t[k] == 0)" for a timer an actor's on-load script sets to 0 (the
    Precinct Assault maps: Cact 12's on-load zeroes t[3], t[5], t[6] and Cfun 27/28/29
    spawn the starting turrets, pickups and neutral turrets).
Everything else (disabled, bought units, outposts, difficulty or counter conditions)
waits for a script; the walk scenes leave it out (generate_scene.gd). Respawning after
being destroyed or collected isn't modelled.
"""

import collections
import glob
import json
import os
import re

_MSG = re.compile(r"spawner(_group)?_msg\((\d+), (70|71):\w+, (-?\d+)\)")
_ZERO = re.compile(r"^t\[(\d+)\] = 0$")
_START_COND = re.compile(r"^return \(t\[(\d+)\] == 0\)$")


def _targets(lines, groups):
    """Csac ids a script spawns (71) or enables (70 with argument 1)."""
    out = set()
    for ln in lines:
        for grp, target, msg, arg in _MSG.findall(ln):
            if msg == "70" and int(arg) != 1:
                continue
            out.update(groups.get(int(target), []) if grp else [int(target)])
    return out


def classify(level_dir):
    dec = json.load(open(os.path.join(level_dir, "actors_decoded.json")))
    csac = {a["id"]: a for a in dec["actors"] if a.get("kind") == "Csac"}
    if not csac:
        return {"start": [], "waiting": []}
    groups = collections.defaultdict(list)
    for a in csac.values():
        groups[a["fields"].get("BaseEntity.group_id", {}).get("raw")].append(a["id"])
    rpns = json.load(open(os.path.join(level_dir, "rpns.json")))
    conds = {c["id"]: c for c in rpns.get("load_conditions", []) if c["type"] == "Csac"}
    zeroed = set()   # timers some actor's on-load script sets to 0
    for c in rpns.get("load_conditions", []):
        if c["type"] != "Csac":
            for ln in c["cond"][1]["pseudo"]:
                m = _ZERO.match(ln.strip())
                if m:
                    zeroed.add(int(m.group(1)))
    start = set()
    fun_path = os.path.join(level_dir, "fun.json")
    if os.path.exists(fun_path):
        for cf in json.load(open(fun_path)).get("cfun", []):
            for fn in cf["functions"]:
                cond = " ".join(fn["condition"]["pseudo"]).strip()
                m = _START_COND.match(cond)
                if fn.get("count", 0) != 0 and m and int(m.group(1)) in zeroed:
                    start |= _targets(fn["action"]["pseudo"], groups)
    for i, a in csac.items():
        c = conds.get(i)
        cond = " ".join(c["cond"][0]["pseudo"]).strip() if c else "return 1"
        if not a["spawn"]["starts_disabled"] and cond == "return 1":
            start.add(i)
    start &= set(csac)
    return {"start": sorted(start), "waiting": sorted(set(csac) - start)}


def main():
    parsed = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "parsed")
    for d in sorted(glob.glob(os.path.join(parsed, "*", "actors_decoded.json"))):
        level_dir = os.path.dirname(d)
        if not os.path.exists(os.path.join(level_dir, "rpns.json")):
            continue
        out = classify(level_dir)
        with open(os.path.join(level_dir, "spawns.json"), "w") as f:
            json.dump(out, f)
        print(f"{os.path.basename(level_dir):6s} spawners at start {len(out['start']):3d}, waiting {len(out['waiting']):3d}")


if __name__ == "__main__":
    main()
