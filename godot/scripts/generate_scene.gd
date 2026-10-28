## Run once with: Godot --headless --script scripts/generate_scene.gd [-- LEVEL]
## Builds a level as a walkable .tscn scene: terrain (collision built at load time
## by terrain_collision.gd), every placed actor as static scenery, and the first
## X1-Alpha as the player (player.gd, a CharacterBody3D with a chase camera).
## LEVEL defaults to CONFT; pass any extracted level name (e.g. OVMP, LAX1).
## Needs godot/assets/<LEVEL>/ (filled by tools/sync_godot_assets.py), incl. models/*.glb for the
## class-1 X1-Alpha parts. Run `godot --headless --path godot --import` first.
extends SceneTree

var _level: String = "CONFT"
var _base: String
var _terrain_obj: String
var _actors_json: String
var _hm_json: String
var _out_scene: String

const LevelLayout := preload("res://scripts/level_layout.gd")

var _layout: RefCounted

# Per-level setup that used to be hand edits on the generated scenes.
const SUN_COLOR := {"CONFT": Color(0.68477565, 0.2997981, 0.37404373)}
# Player collision: a capsule around the X1 legs (feet at y=0, ~0.9 units tall).
const PLAYER_RADIUS := 0.28
const PLAYER_HEIGHT := 0.9
# Chase camera relative to the player actor: behind (-Z, the X1 faces +Z) and
# above, pitched down 40°.
const PLAYER_CAMERA := Transform3D(Basis(Vector3.UP, PI)) * Transform3D(Basis(Vector3(1, 0, 0),
	Vector3(0, 0.7660445, -0.64278764), Vector3(0, 0.64278764, 0.7660445)), Vector3(0, 2.5363665, 0.7880659))
const PLAYER_CAMERA_FOV := 117.99254


func _init() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() > 0 and args[0] != "":
		_level = args[0].to_upper()

	_base        = "res://assets/%s/" % _level
	_terrain_obj = _base + "terrain/map.obj"
	_actors_json = _base + "actors.json"
	_hm_json     = _base + "heightmap.json"
	_out_scene   = "res://scenes/%s_level.tscn" % _level

	print("Generating %s_level.tscn …" % _level)

	_load_heightmap()

	var root := Node3D.new()
	root.name = _level

	# Offset to compensate for grids wider/taller than the CONFT baseline (gw=14, gh=16).
	if _layout.actor_x_off != 0.0 or _layout.actor_z_off != 0.0:
		print("  Actor offset: X=%+.0f  Z=%+.0f" % [_layout.actor_x_off, _layout.actor_z_off])

	_add_music(root)
	_add_environment(root)
	_add_terrain(root)
	if not _add_actors(root):
		_add_camera(root)  # no X1 in this level: fixed overview camera instead

	var scene := PackedScene.new()
	var err := scene.pack(root)
	if err != OK:
		push_error("pack() failed: %d" % err)
		quit(1)
		return

	err = ResourceSaver.save(scene, _out_scene)
	if err != OK:
		push_error("save() failed: %d" % err)
		quit(1)
		return

	print("Saved → %s" % _out_scene)
	root.free()
	quit()


func _own(node: Node, root: Node) -> void:
	node.owner = root
	for child in node.get_children():
		_own(child, root)


func _load_heightmap() -> void:
	var file := FileAccess.open(_hm_json, FileAccess.READ)
	if file == null:
		push_warning("Could not load heightmap.json — Y will default to floor level")
		_layout = LevelLayout.new()
		return
	_layout = LevelLayout.new(file.get_as_text())
	file.close()
	# Cnet paths give path-following actors their heading (tools/extract_nets.py).
	var nets_file := FileAccess.open(_base + "nets.json", FileAccess.READ)
	if nets_file:
		var nets: Variant = JSON.parse_string(nets_file.get_as_text())
		_layout.nets = nets.get("nets", []) if nets is Dictionary else []
	if _layout.grid_w > 0:
		print("  Heightmap loaded (%d tiles, grid %dx%d)" % [_layout.tiles.size(), _layout.grid_w, _layout.grid_h])



func _add_environment(root: Node3D) -> void:
	var sun := DirectionalLight3D.new()
	sun.name = "Sun"
	sun.rotation_degrees = Vector3(-50, 30, 0)
	sun.light_energy = 1.5
	sun.shadow_enabled = true
	if SUN_COLOR.has(_level):
		sun.light_color = SUN_COLOR[_level]
	root.add_child(sun)
	sun.owner = root

	var sky_node := WorldEnvironment.new()
	sky_node.name = "WorldEnvironment"
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.35, 0.45, 0.65)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.45, 0.5, 0.6)
	env.ambient_light_energy = 0.6
	sky_node.environment = env
	root.add_child(sky_node)
	sky_node.owner = root


func _add_music(root: Node3D) -> void:
	var dir := _base + "music/"
	var tracks := Array(DirAccess.get_files_at(dir)).filter(func(f: String) -> bool: return f.get_extension() == "wav")
	if tracks.is_empty():
		return
	var music := AudioStreamPlayer.new()
	music.name = "Music"
	music.stream = load(dir + tracks[0])
	music.autoplay = true
	music.set_script(load("res://scripts/music_loop.gd"))
	root.add_child(music)
	music.owner = root


func _add_camera(root: Node3D) -> void:
	var cam := Camera3D.new()
	cam.name = "Camera3D"
	cam.fov = 60
	root.add_child(cam)
	cam.owner = root
	# Look at the scaled training zone centre (world ≈ 0, Z≈-18).
	cam.look_at_from_position(Vector3(0, 35, 18), Vector3(0, -1.4, -18), Vector3(0, 1, 0))


func _add_terrain(root: Node3D) -> void:
	var terrain_node := Node3D.new()
	terrain_node.name = "Terrain"
	root.add_child(terrain_node)
	terrain_node.owner = root

	terrain_node.set_script(load("res://scripts/terrain_collision.gd"))

	var mesh_res := load(_terrain_obj) as Mesh
	if mesh_res == null:
		push_warning("Could not load terrain — run --import first")
		return

	var mi := MeshInstance3D.new()
	mi.name = "TerrainMesh"
	mi.mesh = mesh_res

	# PS1 terrain has wall faces wound for the game's fixed camera angle.
	# Disable backface culling so all wall surfaces are visible from any angle.
	for i in range(mesh_res.get_surface_count()):
		var mat := mesh_res.surface_get_material(i)
		if mat is StandardMaterial3D:
			var m := (mat as StandardMaterial3D).duplicate() as StandardMaterial3D
			m.cull_mode = BaseMaterial3D.CULL_DISABLED
			mi.set_surface_override_material(i, m)

	terrain_node.add_child(mi)
	mi.owner = root


func _add_actors(root: Node3D) -> bool:
	## Returns true when a player (the first X1-Alpha) was placed.
	var actors_root := Node3D.new()
	actors_root.name = "Actors"
	root.add_child(actors_root)
	actors_root.owner = root

	var file := FileAccess.open(_actors_json, FileAccess.READ)
	if file == null:
		push_warning("Could not open actors.json")
		return false

	var data: Variant = JSON.parse_string(file.get_as_text())
	file.close()
	if not data or not data.has("actors"):
		push_error("actors.json parse failed")
		return false

	var placed := 0
	var has_player := false
	for actor in data.actors:
		var objects: Array = actor.get("objects", [])
		if objects.is_empty() or not LevelLayout.is_placed(actor):
			continue

		var cls: int = actor.get("class", 0)
		var is_player: bool = LevelLayout.is_x1(actor) and not has_player
		var actor_node: Node3D = CharacterBody3D.new() if is_player else Node3D.new()
		has_player = has_player or is_player
		actor_node.name = "Actor_%d" % actor.id
		# Store class and id as metadata so they're visible in editor
		actor_node.set_meta("actor_id", actor.id)
		actor_node.set_meta("class",    cls)
		actor_node.position = _layout.actor_position(actor)
		if is_player:
			actor_node.position.y += 0.1  # drop onto the terrain collision at start
		var rot_y: float = _layout.actor_yaw_degrees(actor)
		if not is_nan(rot_y):
			actor_node.rotation_degrees.y = rot_y

		actors_root.add_child(actor_node)
		actor_node.owner = root
		var aligned: Array = LevelLayout.ground_aligned_parts(actor)
		var normal: Vector3 = _layout.ground_normal(actor_node.position.x, actor_node.position.z)
		if -1 in aligned:
			actor_node.quaternion = LevelLayout.tilt_local(Quaternion.IDENTITY, actor_node.quaternion, normal)

		if LevelLayout.is_x1(actor):
			_add_x1(actor_node, actor, root)
			placed += 1
			continue

		var obj_y_offsets: Dictionary = LevelLayout.part_y_offsets(actor, func(obj: String) -> Variant:
			var base_path: String = _base + "objects/" + obj
			if ResourceLoader.exists(base_path):
				var base_mesh: Mesh = load(base_path) as Mesh
				if base_mesh:
					return base_mesh.get_aabb()
			return null)
		var part_yaws: Dictionary = LevelLayout.part_yaw_offsets(actor)
		for i in objects.size():
			var obj_name: String = objects[i]
			var path: String = _base + "objects/" + str(obj_name)
			if not ResourceLoader.exists(path):
				continue
			var mesh_res: Mesh = load(path) as Mesh
			if mesh_res == null:
				continue
			var mi := MeshInstance3D.new()
			mi.name = str(obj_name).get_basename()
			mi.mesh = mesh_res
			var y_off: float = obj_y_offsets.get(str(obj_name), 0.0)
			if y_off != 0.0:
				mi.position.y = y_off
			if part_yaws.has(i):
				mi.rotation_degrees.y = part_yaws[i]
			if i in aligned:
				mi.quaternion = LevelLayout.tilt_local(actor_node.quaternion, mi.quaternion, normal)
			actor_node.add_child(mi)
			mi.owner = root

		placed += 1

	print("  Placed %d actors" % placed)
	return has_player


func _add_x1(actor_node: Node3D, actor: Dictionary, root: Node3D) -> void:
	var x1 := LevelLayout.assemble_x1(actor, func(obj: String) -> Node3D:
		var path: String = _base + "models/" + obj.get_basename() + ".glb"
		if not ResourceLoader.exists(path):
			push_warning("Missing %s — copy parsed/%s/models/ into assets" % [path, _level])
			return null
		return (load(path) as PackedScene).instantiate())
	actor_node.add_child(x1)
	_own_new(x1, root)
	if actor_node is CharacterBody3D:
		actor_node.set_script(load("res://scripts/player.gd"))
		var capsule := CapsuleShape3D.new()
		capsule.radius = PLAYER_RADIUS
		capsule.height = PLAYER_HEIGHT
		var shape := CollisionShape3D.new()
		shape.name = "Collision"
		shape.shape = capsule
		shape.position.y = PLAYER_HEIGHT / 2.0
		actor_node.add_child(shape)
		shape.owner = root
		var cam := Camera3D.new()
		cam.name = "Camera"
		cam.transform = PLAYER_CAMERA
		cam.keep_aspect = Camera3D.KEEP_WIDTH
		cam.fov = PLAYER_CAMERA_FOV
		actor_node.add_child(cam)
		cam.owner = root


func _own_new(node: Node, root: Node) -> void:
	# Own only nodes we created; nodes inside instanced .glb scenes keep their owner.
	# Parts mount on attach_N nodes inside other parts' instances, and pack() only
	# saves children added inside an instance when it is an editable instance.
	if node.owner == null:
		node.owner = root
		if node.scene_file_path != "":
			root.set_editable_instance(node, true)
	for child in node.get_children():
		_own_new(child, root)
