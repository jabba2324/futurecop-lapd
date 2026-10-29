# Future Cop: L.A.P.D. — asset extraction & viewer

Reverse-engineering the PS1 game *Future Cop: L.A.P.D.* (EA, 1998). Python tools decode
every asset format on the disc, and a Godot 4 asset viewer shows all of it. The viewer
covers models with their animations, terrain, actors and AI paths, textures and UI art,
fonts, audio, FMV movies, and the decoded level data.

Format knowledge lives in each tool's module docstring, which is the source of truth for
that binary layout. `CLAUDE.md` holds the working notes: pipeline details, hard-won
format facts and open questions.

## Layout

```
parsed/    everything the tools extract, one folder per level (+ parsed/video/)
tools/     the extractors (Python) and lib/ shared helpers
godot/     Godot 4.6 project: the asset viewer and a walkable scene for each level
```

The game itself stays outside the repo, in its parent folder:

```
../Future Cop - L.A.P.D. (USA).iso       PS1 disc image
../future_cop_lapd.img/.ccd/.sub         PC disc image (CloneCD; a Windows + Mac hybrid)
../ps1/                                  PS1 disc contents: CW/ and PA/ level archives (.MIS),
                                         loading screens (.LSC), demos (.DEM), MOVIES/*.WVE,
                                         SLUS_007.39 (the executable)
../pc/windows/                           PC disc, ISO 9660 side (installer, MISSIONS/, …)
../pc/mac/                               PC disc, HFS side (Mac version); files with a
                                         resource fork are stored as MacBinary (.bin)
../ghidra/                               analysed Ghidra project of SLUS_007.39 (PS1 SDK
                                         functions named); decomp.sh decompiles headlessly
```

The tools read `../ps1/` (override with `FCOP_PS1_DIR=/path/to/ps1`). Only code is
committed: `parsed/` and `godot/assets/` are generated from the disc and gitignored.

## Quick start

```sh
python3 -m venv ~/.venvs/fc-lapd && ~/.venvs/fc-lapd/bin/pip install pillow fonttools
~/.venvs/fc-lapd/bin/python -I tools/run_all.py              # ~5 min; --no-videos to skip FMVs
godot --path godot res://scenes/asset_viewer.tscn            # browse everything
```

Run `run_all.py` once before opening the viewer; it fills `parsed/` and `godot/assets/`.
The video step also needs `java` and an `ffmpeg` built with libtheora.

Level scenes: every level except FE has `godot/scenes/<LEVEL>_level.tscn`. Each has the
terrain with collision, all props as static scenery, and the X1-Alpha walker at player
one's start, with the original game's movement physics and chase camera: W/S walk, A/D
turn, Q/E strafe, Shift slow walk, Space jump (brake when hovering) and T transforms
between walker and hovercraft. The game's special floors work too: conveyors, damaging
and deadly floors (a health readout shows; at zero the X1 explodes and restarts), slippery
floors, and deep water, where the walker turns into a hovercraft. Walls push back and
turn the X1 along them as in the game, Shift+Space crouches and hard landings cost
health. V cycles the game's seven camera views, and the camera backs off to its higher
views when terrain blocks the line of sight (see-through walls don't count). Elevators
run and carry the X1 (and stop when it is under them), pickups spin and power-ups flash, actors
cast the game's blob shadows, vehicles and pedestrians drive their routes (without reacting
to you), only the spawned actors the game has at the start appear, UN's teleporters work, and water is translucent,
scrolls and ripples or flickers with the game's light tables. Placed
effects, decals, animated props and ambient sounds are included, and K steps the nearest
prop through its damage states and destruction. L switches between the
original PS1 lighting (the default) and real-time lighting, G toggles fog, M the minimap
and O a debug overlay of trigger zones (labelled with what they trigger), paths and
special floors. Reflective surfaces
use the level's sphere map, and the level's loading screen shows while it loads. There are no enemies, scripts or
objectives; it's for walking around the level. The `.tscn` files are committed, but
they load `godot/assets/`, so run the pipeline first. Then run the project for a
level-select menu, or open a level directly:

```sh
godot --path godot                               # menu: pick a level, Esc returns to it
godot --path godot res://scenes/M2C_level.tscn   # one level directly
```

To regenerate a scene:

```sh
~/.venvs/fc-lapd/bin/python -I tools/sync_godot_assets.py    # parsed/ → godot/assets/ (run_all does this too)
godot --headless --path godot --import
godot --headless --path godot --script scripts/generate_scene.gd -- M2C
```

## From the disc images to `../ps1/` and `../pc/`

```sh
cd ..
7z x -tiso -ops1 "Future Cop - L.A.P.D. (USA).iso"
```

The PC `.img` holds raw 2352-byte Mode 1 sectors, so convert it to 2048-byte sectors first
by keeping bytes 16–2063 of each sector. Then extract each side of the hybrid:
- **Windows:** `7z x -tiso -opc/windows cooked.iso`
- **Mac:** the HFS partition starts at 512-byte block 765. Mount it with hfsutils
  (`hmount`), then copy files out with `hcopy` (`-m` for files with a resource fork).

Toast's "HFS Optimizer" filler files, which have tab and space names, are skipped.

## Credits

- [Future Cop: MIT](https://github.com/Ghoster738/Future-Cop-MIT) (MIT licence): the
  reference implementation that several parsers were ported from. Each docstring says
  which.
- [jPSXdec](https://github.com/m35/jpsxdec): decodes the EA `.WVE` movies. It's
  downloaded on first use and checksum-verified, not committed.
- [ghidra_psx_ldr](https://github.com/lab313ru/ghidra_psx_ldr): the PS1 loader that the
  Ghidra project in `../ghidra/` needs.
