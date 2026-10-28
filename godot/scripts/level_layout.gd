## Actor layout shared by generate_scene.gd and the asset viewer: heightmap
## sampling, game → Godot placement, per-class BAM rotation and how the parts
## of multi-object actors are stacked. Keep both callers on this one copy.
##
## Usage: var layout = LevelLayout.new(heightmap_json_text)
extends RefCounted

const FLOOR_FALLBACK := 0.5938  # flat training floor height

var tiles: Dictionary = {}
var grid_w: int = 0
var grid_h: int = 0
# Grid-size offset relative to CONFT baseline (gw=14, gh=16).
# Each extra column/row adds 8 world units to the actor origin.
var actor_x_off: float = 0.0
var actor_z_off: float = 0.0


func _init(heightmap_json: String = "") -> void:
	var hm: Variant = JSON.parse_string(heightmap_json) if heightmap_json != "" else null
	if hm:
		grid_w = int(hm.grid_w)
		grid_h = int(hm.grid_h)
		tiles = hm.tiles
	actor_x_off = (14 - grid_w) * 8.0
	actor_z_off = (16 - grid_h) * 8.0


func _valid_h(tile: Array, px: int, py: int, use_max: bool) -> float:
	# -128 is a sentinel meaning "no surface at this vertex/channel".
	# use_max=true: take the highest valid channel (actors on elevated platforms).
	# use_max=false: take the first valid channel R→G→B (default).
	var best: float = -1000.0
	for ch in [0, 1, 2]:
		var v: int = int(tile[px][py][ch])
		if v != -128:
			var h: float = v / 32.0
			if use_max:
				if h > best:
					best = h
			else:
				return h  # first valid channel
	return best


func sample_y(world_x: float, world_z: float, use_max: bool = false) -> float:
	# Terrain world coords → chunk grid cell
	var col: int = floori((world_x + 8.5) / 16.0) + grid_w / 2
	var row: int = floori((world_z + 8.5) / 16.0) + grid_h / 2
	var key: String = "%d,%d" % [row, col]
	if tiles.is_empty() or not tiles.has(key):
		return FLOOR_FALLBACK
	var tile: Array = tiles[key]
	# Local position within chunk (0–16); ty = X axis, tx = Z axis (matches heightmap[px][py])
	var ty: float = clampf(world_x + 8.5 - (col - grid_w / 2.0) * 16.0, 0.0, 15.9999)
	var tx: float = clampf(world_z + 8.5 - (row - grid_h / 2.0) * 16.0, 0.0, 15.9999)
	var tx0: int = int(tx); var ty0: int = int(ty)
	var fx: float = tx - tx0;  var fy: float = ty - ty0
	# Per-corner channel fallback: avoid -128 sentinels contaminating bilinear interpolation.
	var c00: float = _valid_h(tile, tx0,   ty0,   use_max)
	var c01: float = _valid_h(tile, tx0,   ty0+1, use_max)
	var c10: float = _valid_h(tile, tx0+1, ty0,   use_max)
	var c11: float = _valid_h(tile, tx0+1, ty0+1, use_max)
	# Replace any fully-invalid corners with the average of the valid ones.
	var valid_sum: float = 0.0
	var valid_cnt: int   = 0
	for c in [c00, c01, c10, c11]:
		if c > -999.0:
			valid_sum += c
			valid_cnt += 1
	if valid_cnt == 0:
		return FLOOR_FALLBACK  # no valid data at all
	var avg: float = valid_sum / valid_cnt
	if c00 < -999.0: c00 = avg
	if c01 < -999.0: c01 = avg
	if c10 < -999.0: c10 = avg
	if c11 < -999.0: c11 = avg
	return c00 * (1.0-fx) * (1.0-fy) + c01 * (1.0-fx) * fy + \
	       c10 * fx       * (1.0-fy) + c11 * fx       * fy


func actor_position(actor: Dictionary) -> Vector3:
	# 90° CW rotation: game Z → Godot X, game X → Godot Z.
	# Scale=8.0 (1 game unit = 8 terrain tiles). Offsets confirmed via regression on 249 actors.
	# Godot X = pos_z * 8.0 - 104.5,  Godot Z = pos_x * 8.0 - 136.51
	var wx: float = float(actor.pos_x) * 8.0 - 118.51
	var wz: float = float(actor.pos_z) * 8.0 - 128.5
	var wx_final: float = -wz - 24.0
	var wz_final: float = wx - 18.0
	var godot_x: float = -wx_final
	var godot_z: float = wz_final
	var fx: float = godot_x + actor_x_off
	var fz: float = godot_z + actor_z_off
	return Vector3(fx, sample_y(fx, fz, true), fz)


# Initial yaw per class: [params word, bit shift] of a 16-bit field whose low 12
# bits are a BAM angle (4096 = 360°). Read from each class's init function in
# SLUS_007.39 (addresses in tools/decode_actors.py, field "initial_yaw").
# Classes 5/20/26/28/31 follow Cnet paths and have no stored yaw (path_yaw_degrees);
# 9/37 aircraft start at a random heading; 98/99 are weapon definitions.
const STORED_YAW := {1: [4, 0], 6: [9, 0], 8: [9, 0], 10: [8, 16], 11: [4, 16], 12: [4, 0],
	16: [9, 0], 25: [5, 16], 27: [9, 0], 30: [9, 0], 32: [7, 0], 33: [9, 0], 34: [5, 0],
	36: [9, 0], 96: [0, 0], 97: [2, 0]}
# A negative 16-bit value means "no fixed yaw": item pickups (16) spin, props (96) and
# DCSQuad decals (97, loader 0x80049c38) get a random yaw.
const NEGATIVE_MEANS_UNSET := [16, 96, 97]
# Turret parts with their own absolute yaw, by index in "objects": the base of
# classes 8/36 (Turret.base_rotation) and the base + middle of class 33.
const PART_YAW := {8: {1: [12, 16]}, 36: {1: [12, 16]}, 33: {1: [12, 16], 2: [11, 16]}}
const PATHED_CLASSES := [5, 20, 26, 28, 31]
const NOT_PLACED := [98, 99]  # weapon definitions; their Cobj is the projectile model

# nets.json "nets" (tools/extract_nets.py); needed for path-following actors' heading.
var nets: Array = []


static func is_placed(actor: Dictionary) -> bool:
	return not int(actor.get("class", 0)) in NOT_PLACED


static func _bam_field(actor: Dictionary, spec: Array) -> int:
	## The raw signed 16-bit field, or 0x7fffffff when the word is missing.
	var params: Array = actor.get("params", [])
	if params.size() <= spec[0]:
		return 0x7fffffff
	var v: int = (String(params[spec[0]]).hex_to_int() >> spec[1]) & 0xFFFF
	return v - 0x10000 if v & 0x8000 else v


static func _bam_to_degrees(bam: int) -> float:
	# X-axis flip mirrors rotation direction, so angle = 90 - bam_deg.
	return 90.0 - float(bam & 0xFFF) / 4096.0 * 360.0


static func rotation_y_degrees(actor: Dictionary) -> float:
	## Returns NAN when the class has no stored initial yaw.
	var cls: int = int(actor.get("class", 0))
	if not STORED_YAW.has(cls):
		return NAN
	var v := _bam_field(actor, STORED_YAW[cls])
	if v == 0x7fffffff or (v < 0 and cls in NEGATIVE_MEANS_UNSET):
		return NAN
	return _bam_to_degrees(v)


static func part_yaw_offsets(actor: Dictionary) -> Dictionary:
	## Object index → Y rotation (degrees) relative to the actor node, for turret
	## parts that have their own yaw.
	var cls: int = int(actor.get("class", 0))
	var out: Dictionary = {}
	var own := rotation_y_degrees(actor)
	if not PART_YAW.has(cls) or is_nan(own):
		return out
	for idx: int in PART_YAW[cls]:
		var v := _bam_field(actor, PART_YAW[cls][idx])
		if v != 0x7fffffff:
			out[idx] = _bam_to_degrees(v) - own
	return out


func path_yaw_degrees(actor: Dictionary) -> float:
	## Heading of a path-following actor: from its spawn node towards the next
	## node, as the game computes it (ratan2(dx, dz) at init). NAN if unknown.
	if not int(actor.get("class", 0)) in PATHED_CLASSES:
		return NAN
	for net: Dictionary in nets:
		for a: Dictionary in net.get("actors", []):
			if int(a.id) != int(actor.id):
				continue
			var nodes: Array = net.nodes
			var start: Dictionary = nodes[int(a.start_node)]
			if start.links.is_empty():
				return NAN
			# At a junction the game picks randomly or by target; take the first link.
			var nxt: Dictionary = nodes[int(start.links[0])]
			var dx: float = float(nxt.pos_x) - float(start.pos_x)
			var dz: float = float(nxt.pos_z) - float(start.pos_z)
			return 90.0 - rad_to_deg(atan2(dx, dz))
	return NAN


func actor_yaw_degrees(actor: Dictionary) -> float:
	## Stored yaw, else path heading, else NAN (leave unrotated).
	var r := rotation_y_degrees(actor)
	return r if not is_nan(r) else path_yaw_degrees(actor)


static func part_y_offsets(actor: Dictionary, base_aabb: Callable) -> Dictionary:
	## Object name → Y offset for stacked two-part actors.
	## base_aabb(obj_name) -> AABB (or null when the mesh is missing).
	# For stacked two-part actors the object list is [top, base].
	# Pre-read the base AABB so the top can be placed on top of it.
	# Class 36 = turret (cobj031/032), class 8 = turret variant (cobj020/021).
	var objects: Array = actor.get("objects", [])
	var out: Dictionary = {}
	if int(actor.get("class", 0)) in [8, 36] and objects.size() == 2:
		var aabb: Variant = base_aabb.call(str(objects[1]))
		if aabb is AABB:
			out[str(objects[0])] = (aabb as AABB).end.y
	return out


static func is_x1(actor: Dictionary) -> bool:
	# Class 1 = X1-Alpha player mech, built from glTF parts by assemble_x1().
	return int(actor.get("class", 0)) == 1


# X1-Alpha parts in actors.json "objects" order (same in all 15 levels) and where
# each mounts on the glTF exports' attach_N empties (3DHS attachment points):
# cockpit on the legs' hips, a weapon on each cockpit gun mount, beacon lights on
# the cockpit roof, pilot at the cockpit origin (hidden inside in walker mode).
const X1_ROLES := ["legs", "cockpit", "weapon", "beacon_lights", "pilot"]
const X1_MOUNTS := {"cockpit": ["legs", ["attach_0"]], "weapon": ["cockpit", ["attach_0", "attach_1"]],
	"beacon_lights": ["cockpit", ["attach_2"]], "pilot": ["cockpit", [""]]}


static func assemble_x1(actor: Dictionary, load_part: Callable) -> Node3D:
	## load_part(obj_name: String) -> Node3D (a fresh instance of that part's .glb).
	## Returns a node with the parts mounted, named by role. Unlike the raw OBJs,
	## the .glb parts are upright with feet at y=0, so no flip or raise is needed.
	var root := Node3D.new()
	root.name = "X1"
	# The parts face +Z; turn them so the mech faces the actor node's -Z (Godot forward).
	root.rotation_degrees.y = 180.0
	var objects: Array = actor.get("objects", [])
	var parts: Dictionary = {}
	for i in mini(objects.size(), X1_ROLES.size()):
		var role: String = X1_ROLES[i]
		var mounts: Array = X1_MOUNTS.get(role, ["", [""]])
		for m in mounts[1].size():
			var mount: String = mounts[1][m]
			var part: Node3D = load_part.call(str(objects[i]))
			if part == null:
				continue
			part.name = role if m == 0 else "%s_%d" % [role, m + 1]
			var parent: Node = root
			if mounts[0] != "" and parts.has(mounts[0]):
				parent = parts[mounts[0]]
				if mount != "":
					parent = parts[mounts[0]].find_child(mount, true, false)
					if parent == null:
						parent = parts[mounts[0]]
			parent.add_child(part)
			if not parts.has(role):
				parts[role] = part
	return root
