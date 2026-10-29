#!/usr/bin/env python3
"""Copy what godot/scripts/generate_scene.gd needs from parsed/<LEVEL>/ into godot/assets/<LEVEL>/.

The generated level scenes reference res://assets/<LEVEL>/..., so these copies stay
committed. This script makes them reproducible instead of hand-copied. For each level
it copies:
  terrain/map.obj + map.mtl          stitched terrain
  terrain/surfaces.json              special floors (water, conveyors, damage), player.gd
  objects/<cobj>.obj + .mtl          only models referenced by placed actors
  models/<cobj>.glb                  X1-Alpha (class 1) parts, assembled from glTF
  textures/*.png                     every texture an included .mtl references
  actors.json, heightmap.json, nets.json, music/*.wav
  loading/<LEVEL>LD.png              the level's loading screen (menu preview)
  actors_decoded.json, csfx.json,    effect definitions + placements and the particle
  particles/*.png                    sprites, for level_ambience.gd's placed effects
  models/<cobj>.glb (animated)       props whose model has animation clips or 3DTA
                                     texture animation (used instead of the OBJ)
  dcs.json + decal textures          class-97 decals (level_overlays.build_decals)
  rpns.json, fun.json                actor scripts and level functions (debug overlay labels)
  spawns.json                        Csac spawners present at the start (extract_spawns.py)
  shd.json + sfx_tones/*.wav         sounds: every "sound N" field (ambient, elevator,
                                     effect) → the Cshd entry with script_id N → its
                                     tone WAV; the walker's and the teleporters' sounds
Anything else under godot/assets/<LEVEL>/ (except Godot's *.import sidecars of
copied files) is removed, so the folder mirrors exactly what the generator reads.

Usage: sync_godot_assets.py [LEVEL ...]   (default: every parsed level with terrain, i.e. all but FE)
Then:  godot --headless --path godot --import
       godot --headless --path godot --script scripts/generate_scene.gd -- LEVEL
"""

import json
import os
import shutil
import struct
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARSED = os.path.join(_REPO_ROOT, "parsed")
_ASSETS = os.path.join(_REPO_ROOT, "godot", "assets")
# Mirrors level_layout.gd: weapon definitions are never placed, class 1 uses .glb parts.
_NOT_PLACED = {98, 99}
_X1_CLASS = 1
_TELEPORT_SOUND = 105   # played by every class-29 teleporter's on_damage script (level_ambience.gd)


def _mtl_textures(mtl_path):
    """Texture paths (relative to the level dir) referenced by an .mtl file."""
    out = set()
    with open(mtl_path) as f:
        for line in f:
            if line.startswith("map_Kd"):
                rel = os.path.normpath(os.path.join(os.path.dirname(mtl_path), line.split(None, 1)[1].strip()))
                out.add(os.path.relpath(rel, os.path.dirname(os.path.dirname(mtl_path))))
    return out


# The X1-Alpha's sounds, (Cshd script_id, positional), as godot/scripts/player.gd plays
# them: gait loops 0x22-0x25, steps 0x38/0x3a/0x3b/0x33, gait stops 0x32-0x35, jump
# 0x26/0x36 and landing 0x27/0x37, hover loops 0x28-0x2a, transform 0x15.
_PLAYER_SOUNDS = {(i, True) for i in (0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2a)} | \
                 {(i, False) for i in (0x38, 0x3a, 0x3b, 0x33, 0x32, 0x34, 0x35, 0x36, 0x37, 0x15, 0x2a, 0x41)}


def _glb_needed(path):
    """True when a prop needs its .glb rather than the OBJ: animation clips, 3DTA texture
    animation, MIX/additive blended materials (the OBJ has no blend modes, so e.g.
    CONFT's additive light beams would draw as solid dark slabs) or env-mapped faces."""
    if not os.path.exists(path):
        return False
    with open(path, "rb") as f:
        head = f.read(20)
        j = json.loads(f.read(struct.unpack_from("<I", head, 12)[0]))
    meshes = j.get("meshes", [])
    uv = meshes and meshes[0].get("extras", {}).get("fc_uv_animations")
    blend = any(m.get("extras", {}).get("fc_blend") or m.get("extras", {}).get("fc_environment_map")
                for m in j.get("materials", []))
    return bool(j.get("animations") or uv or blend)


def _wanted_files(level):
    src = os.path.join(_PARSED, level)
    files = {"terrain/map.obj", "terrain/map.mtl", "terrain/surfaces.json", "actors.json", "heightmap.json", "nets.json"}
    with open(os.path.join(src, "actors.json")) as f:
        actors = json.load(f)["actors"]
    for a in actors:
        if a["class"] in _NOT_PLACED:
            continue
        for obj in a.get("objects", []):
            name = os.path.splitext(obj)[0]
            if a["class"] == _X1_CLASS or _glb_needed(os.path.join(src, "models", name + ".glb")):
                files.add(f"models/{name}.glb")
            if a["class"] != _X1_CLASS:
                files.update({f"objects/{name}.obj", f"objects/{name}.mtl"})
    files.add(f"loading/{level}LD.png")
    files.update({"actors_decoded.json", "csfx.json", "dcs.json", "shd.json", "rpns.json", "fun.json"})
    if os.path.exists(os.path.join(src, "spawns.json")):
        files.add("spawns.json")
    files.add("textures/cbmp010.png")   # env-map sphere map (obj_loader.shine_texture)
    # Decal textures and ambient-sound WAVs actually referenced by the level.
    decoded = json.load(open(os.path.join(src, "actors_decoded.json"))).get("actors", [])
    dcs_ids = {r["fields"]["DCSQuad.dcs_id"]["value"] for r in decoded
               if r["class_id"] == 97 and "DCSQuad.dcs_id" in r["fields"]}
    if os.path.exists(os.path.join(src, "dcs.json")):
        for table in json.load(open(os.path.join(src, "dcs.json"))).get("cdcs", []):
            for e in table.get("entries", []):
                if e.get("dcs_id") in dcs_ids and e.get("texture"):
                    files.add(e["texture"])
    # Sounds by Cshd script id, resolved as level_ambience.gd's _sound_table: ids >= 70
    # from the mission table, below 70 from the global one by (script_id, positional).
    # The level's "sound N" fields (ambient, elevator, effect sounds), the teleporters'
    # script sound and the walker's sounds (player.gd: SOUNDS).
    wanted = set()
    def _refs(fields):
        for f in fields.values():
            v = str(f.get("value", "")) if isinstance(f, dict) else ""
            if v.startswith("sound "):
                yield int(v[6:])
    for r in decoded:
        wanted.update(_refs(r["fields"]))
    if os.path.exists(os.path.join(src, "csfx.json")):
        for t in json.load(open(os.path.join(src, "csfx.json"))):
            for r in t.get("records", []):
                for seg in r.get("fields", {}).values():
                    if isinstance(seg, dict):
                        wanted.update(_refs(seg))
    if any(r["class_id"] == 29 for r in decoded):
        wanted.add(_TELEPORT_SOUND)
    if os.path.exists(os.path.join(src, "shd.json")):
        for t in json.load(open(os.path.join(src, "shd.json"))).get("tables", []):
            for e in t.get("entries", []):
                n = e.get("script_id")
                if e.get("tone_wav") and (
                        (t.get("kind") == "mission" and n in wanted and n >= 70)
                        or (t.get("kind") != "mission" and n in wanted and n < 70)
                        or (n, bool(e.get("positional"))) in _PLAYER_SOUNDS):
                    files.add(e["tone_wav"])
    particles = os.path.join(src, "particles")
    if os.path.isdir(particles):
        files.update(f"particles/{f}" for f in os.listdir(particles) if f.endswith(".png"))
    music = os.path.join(src, "music")
    if os.path.isdir(music):
        files.update(f"music/{f}" for f in os.listdir(music) if f.endswith(".wav"))
    files = {f for f in files if os.path.exists(os.path.join(src, f))}
    for f in list(files):
        if f.endswith(".mtl"):
            files |= _mtl_textures(os.path.join(src, f))
    return files


def sync_level(level):
    src = os.path.join(_PARSED, level)
    dst = os.path.join(_ASSETS, level)
    wanted = _wanted_files(level)
    copied = 0
    for rel in sorted(wanted):
        s, d = os.path.join(src, rel), os.path.join(dst, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if not os.path.exists(d) or open(s, "rb").read() != open(d, "rb").read():
            shutil.copyfile(s, d)
            copied += 1
    removed = 0
    for root, dirs, fs in os.walk(dst, topdown=False):
        for f in fs:
            rel = os.path.relpath(os.path.join(root, f), dst)
            base = rel[:-7] if rel.endswith(".import") else rel
            # Godot's .glb importer extracts embedded textures next to the model
            # as <model>_<texture>.png; those belong to the copied .glb.
            extracted = base.startswith("models/") and any(
                base.startswith(w[:-4] + "_") for w in wanted if w.endswith(".glb"))
            keep = base in wanted or extracted
            if not keep:
                os.remove(os.path.join(root, f))
                removed += 1
        if root != dst and not os.listdir(root):
            os.rmdir(root)
    print(f"{level}: {len(wanted)} files ({copied} updated, {removed} removed)")


def main():
    levels = [l.upper() for l in sys.argv[1:]] or sorted(
        l for l in os.listdir(_PARSED) if os.path.exists(os.path.join(_PARSED, l, "terrain", "map.obj")))
    for level in levels:
        if not os.path.isdir(os.path.join(_PARSED, level)):
            print(f"{level}: no parsed/{level}/ — run the extractors first", file=sys.stderr)
            sys.exit(1)
        sync_level(level)


if __name__ == "__main__":
    main()
