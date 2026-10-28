#!/usr/bin/env python3
"""Copy what godot/scripts/generate_scene.gd needs from parsed/<LEVEL>/ into godot/assets/<LEVEL>/.

The generated level scenes reference res://assets/<LEVEL>/..., so these copies stay
committed. This script makes them reproducible instead of hand-copied. For each level
it copies:
  terrain/map.obj + map.mtl          stitched terrain
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
  shd.json + sfx_tones/*.wav         ambient sounds (BaseEntity.ambient_sound → the
                                     Cshd entry with that group_id → its tone WAV)
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


def _mtl_textures(mtl_path):
    """Texture paths (relative to the level dir) referenced by an .mtl file."""
    out = set()
    with open(mtl_path) as f:
        for line in f:
            if line.startswith("map_Kd"):
                rel = os.path.normpath(os.path.join(os.path.dirname(mtl_path), line.split(None, 1)[1].strip()))
                out.add(os.path.relpath(rel, os.path.dirname(os.path.dirname(mtl_path))))
    return out


def _glb_animated(path):
    """True when a .glb has animation clips or 3DTA texture animation."""
    if not os.path.exists(path):
        return False
    with open(path, "rb") as f:
        head = f.read(20)
        j = json.loads(f.read(struct.unpack_from("<I", head, 12)[0]))
    meshes = j.get("meshes", [])
    uv = meshes and meshes[0].get("extras", {}).get("fc_uv_animations")
    return bool(j.get("animations") or uv)


def _wanted_files(level):
    src = os.path.join(_PARSED, level)
    files = {"terrain/map.obj", "terrain/map.mtl", "actors.json", "heightmap.json", "nets.json"}
    with open(os.path.join(src, "actors.json")) as f:
        actors = json.load(f)["actors"]
    for a in actors:
        if a["class"] in _NOT_PLACED:
            continue
        for obj in a.get("objects", []):
            name = os.path.splitext(obj)[0]
            if a["class"] == _X1_CLASS or _glb_animated(os.path.join(src, "models", name + ".glb")):
                files.add(f"models/{name}.glb")
            if a["class"] != _X1_CLASS:
                files.update({f"objects/{name}.obj", f"objects/{name}.mtl"})
    files.add(f"loading/{level}LD.png")
    files.update({"actors_decoded.json", "csfx.json", "dcs.json", "shd.json"})
    # Decal textures and ambient-sound WAVs actually referenced by the level.
    decoded = json.load(open(os.path.join(src, "actors_decoded.json"))).get("actors", [])
    dcs_ids = {r["fields"]["DCSQuad.dcs_id"]["value"] for r in decoded
               if r["class_id"] == 97 and "DCSQuad.dcs_id" in r["fields"]}
    if os.path.exists(os.path.join(src, "dcs.json")):
        for table in json.load(open(os.path.join(src, "dcs.json"))).get("cdcs", []):
            for e in table.get("entries", []):
                if e.get("dcs_id") in dcs_ids and e.get("texture"):
                    files.add(e["texture"])
    groups = {int(str(r["fields"]["BaseEntity.ambient_sound"]["value"])[6:]) for r in decoded
              if str(r["fields"].get("BaseEntity.ambient_sound", {}).get("value", "")).startswith("sound ")}
    if os.path.exists(os.path.join(src, "shd.json")):
        for t in json.load(open(os.path.join(src, "shd.json"))).get("tables", []):
            for e in t.get("entries", []):
                if e.get("group_id") in groups and e.get("tone_wav"):
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
