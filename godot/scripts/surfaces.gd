## The level's special floors (assets/<LEVEL>/terrain/surfaces.json, written by
## extract_terrain.py) and what each does to the player, decoded from SLUS_007.39.
##
## Every terrain polygon has a surface byte (its Ctil's polygon_action_types entry).
## The ground probe (0x80066f78) returns the byte of the highest floor under the body
## when the body is within 0x1134 (0.54) of it; the handler (0x80053188) runs every
## physics step on (byte & 0x3F):
##   1        deep water: a walker sinks (0xC0/step) and is lost 1.75 under the surface;
##            a hovercraft can't turn back into a walker over it
##   2        instant kill (10000 damage)
##   3, 4, 5  low traction for the walker, level 2 / 1 / 3 (see traction())
##   6..13    damage per step, walker / hover: see DAMAGE
##   14, 15   1 damage per step to player 1 (14) or player 2 (15): Precinct Assault
##            team zones
##   32..39   conveyor, moves the body 0x200 or 0x400 per step (2.34 / 4.69 u/s)
##   40..47   slow conveyor, 0x92 or 0x124 per step (0.67 / 1.34 u/s)
## Bit 0x80 only makes the polygon see-through for the camera's line of sight; bit
## 0x40 makes a floor non-solid (it is left out of the collision, see
## terrain_collision.gd). Game x/z are the map's z/x: conveyor directions are swapped
## accordingly.
extends RefCounted

const CELL := 1.0           # spatial hash cell (Godot units)
const PROBE_UP := 0xECC / 8192.0      # floors up to this far above the feet count
const REACH := 0x1134 / 8192.0        # the body must be within this far above the floor

const WATER := 1
const KILL := 2
const KILL_DAMAGE := 10000
# value -> [walker, hover] damage per step (0x8009acee); KILL_DAMAGE where the game kills
const DAMAGE := {6: [16, 16], 7: [32, 32], 8: [16, 8], 9: [32, 16], 10: [KILL_DAMAGE, 8],
	11: [KILL_DAMAGE, 16], 12: [8, 0], 13: [16, 0]}
const TEAM_DAMAGE := {14: 0, 15: 1}   # value -> player index it hurts (1 per step)
# conveyors: value -> game (dx, dz) per step
const CONVEYORS := {
	0x20: [0, 0x200], 0x21: [0, 0x400], 0x22: [0, -0x200], 0x23: [0, -0x400],
	0x24: [0x200, 0], 0x25: [0x400, 0], 0x26: [-0x200, 0], 0x27: [-0x400, 0],
	0x28: [0, 0x92], 0x29: [0, -0x92], 0x2a: [0x92, 0], 0x2b: [-0x92, 0],
	0x2c: [0, 0x124], 0x2d: [0, -0x124], 0x2e: [0x124, 0], 0x2f: [-0x124, 0]}
const TRACTION := {3: 2, 4: 1, 5: 3}  # value -> level s (PS+0x147)

var _cells := {}            # Vector2i -> [triangle index]
var _tris: Array = []       # [action, PackedVector3Array(3)]


func _init(json_text: String) -> void:
	var j: Variant = JSON.parse_string(json_text) if json_text != "" else null
	if not j is Dictionary:
		return
	for t: Array in j.get("tris", []):
		var v := PackedVector3Array([Vector3(t[1], t[2], t[3]), Vector3(t[4], t[5], t[6]), Vector3(t[7], t[8], t[9])])
		var i := _tris.size()
		_tris.append([int(t[0]), v])
		var lo := Vector2i(floori(minf(minf(v[0].x, v[1].x), v[2].x) / CELL), floori(minf(minf(v[0].z, v[1].z), v[2].z) / CELL))
		var hi := Vector2i(floori(maxf(maxf(v[0].x, v[1].x), v[2].x) / CELL), floori(maxf(maxf(v[0].z, v[1].z), v[2].z) / CELL))
		for x in range(lo.x, hi.x + 1):
			for z in range(lo.y, hi.y + 1):
				var c := Vector2i(x, z)
				if not _cells.has(c):
					_cells[c] = []
				_cells[c].append(i)


func is_empty() -> bool:
	return _tris.is_empty()


func action_at(feet: Vector3, ground_y: float) -> int:
	## The surface byte (& 0x3F) under the feet, or 0. ground_y is the height of the
	## solid floor the walker stands over (a raycast), so a plain floor above a special
	## one (a bridge over water) wins as in the game, which keeps the highest floor.
	if feet.y - ground_y > REACH:
		return 0
	var best := 0
	var best_y := -INF
	for i: int in _cells.get(Vector2i(floori(feet.x / CELL), floori(feet.z / CELL)), []):
		var v: PackedVector3Array = _tris[i][1]
		var y: Variant = _height_in(v, feet.x, feet.z)
		if y == null or y > feet.y + PROBE_UP or y < ground_y - 0.05 or y <= best_y:
			continue
		best_y = y
		best = _tris[i][0] & 0x3F
	return best


func _height_in(v: PackedVector3Array, x: float, z: float) -> Variant:
	## Height of triangle v at (x, z), or null if (x, z) is outside it.
	var a := Vector2(v[0].x, v[0].z)
	var b := Vector2(v[1].x, v[1].z)
	var c := Vector2(v[2].x, v[2].z)
	var den := (b - a).cross(c - a)
	if absf(den) < 1e-9:
		return null
	var p := Vector2(x, z)
	var u := (p - a).cross(c - a) / den
	var w := (b - a).cross(p - a) / den
	if u < -1e-4 or w < -1e-4 or u + w > 1.0001:
		return null
	return v[0].y + u * (v[1].y - v[0].y) + w * (v[2].y - v[0].y)


static func conveyor(action: int) -> Vector3:
	## Godot units moved per step (game x -> map z, game z -> map x).
	if not CONVEYORS.has(action):
		return Vector3.ZERO
	var d: Array = CONVEYORS[action]
	return Vector3(d[1], 0.0, d[0]) / 8192.0


static func damage(action: int, hover: bool, player_index := 0) -> int:
	if action == KILL:
		return KILL_DAMAGE
	if DAMAGE.has(action):
		return DAMAGE[action][1 if hover else 0]
	if TEAM_DAMAGE.get(action, -1) == player_index:
		return 1
	return 0


static func traction(action: int) -> int:
	## Walker traction level s (1..3) on a low-traction floor, 0 elsewhere.
	return TRACTION.get(action, 0)


static func describe(action: int) -> String:
	if action == 0:
		return ""
	if action == WATER:
		return "deep water"
	if action == KILL:
		return "deadly"
	if TRACTION.has(action):
		return "low traction %d" % TRACTION[action]
	if DAMAGE.has(action):
		return "damaging %d/%d" % DAMAGE[action]
	if TEAM_DAMAGE.has(action):
		return "team %d zone" % (TEAM_DAMAGE[action] + 1)
	if CONVEYORS.has(action):
		return "conveyor %.2f u/s" % (conveyor(action).length() * 37.5)
	return ""
