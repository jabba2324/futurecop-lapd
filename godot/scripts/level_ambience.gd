## Ambient scene dressing for a generated level scene (root of
## scenes/<LEVEL>_level.tscn). Visual/audio only; nothing reacts to the player
## except elevators that wait for a rider.
##   - effects the level places at load, looping (effect_player.gd)
##   - class-97 decals (level_overlays.build_decals)
##   - prop animation clips (all_frames, looping) and 3DTA texture animations
##   - additive and env-mapped (sphere-map reflection) materials of .glb props
##     (ObjLoader.apply_fc_materials)
##   - elevators (elevator.gd) move and carry the walker; item pickups spin
##   - scrolling terrain (water) UVs
##   - ambient sounds: BaseEntity.ambient_sound → the Cshd entry with that
##     group_id → its tone WAV, looping 3D audio at the actor
##   - minimap (minimap.gd) of the heightmap, map icons and the walker; M toggles
##   - debug overlay (debug_overlay.gd): trigger zones, paths, special floors; O toggles
##   - lighting: L toggles the PS1 look (baked terrain vertex colours ×2, unshaded,
##     no sun; the default) and real-time lighting; G toggles distance fog
## Reads res://assets/<LEVEL>/ (synced by tools/sync_godot_assets.py).
extends Node3D

const EffectPlayer := preload("res://scripts/effect_player.gd")
const UvAnimator := preload("res://scripts/uv_animator.gd")
const LevelLayout := preload("res://scripts/level_layout.gd")
const LevelOverlays := preload("res://scripts/level_overlays.gd")
const ObjLoader := preload("res://scripts/obj_loader.gd")
const Minimap := preload("res://scripts/minimap.gd")
const DebugOverlay := preload("res://scripts/debug_overlay.gd")
const Elevator := preload("res://scripts/elevator.gd")

var _base := ""
var _layout: RefCounted
var _decoded: Array = []
var _ps1_look := true
var _fog := false
var _lit_materials: Dictionary = {}   # MeshInstance3D -> [surface override materials in lit mode]
var _hint: Label
var _minimap: Control
var _player: Node3D
var _overlay: Node3D
var _sounds: Dictionary = {}     # Cshd group_id -> entry
var _spinning: Array = []        # [pickup node, yaw rad/s]
var _scrolling: Array = []       # [terrain MeshInstance3D, surface, u speed, v speed]
var _clock := 0.0


func _ready() -> void:
	_base = "res://assets/%s/" % name
	if _decoded.is_empty():   # the player may have asked first (view_zones)
		var decoded: Variant = _json(_base + "actors_decoded.json")
		_decoded = decoded.get("actors", []) if decoded is Dictionary else []
		_layout = LevelLayout.new(FileAccess.get_file_as_string(_base + "heightmap.json"))
	_start_effects()
	_add_decals()
	_start_animations()
	_sounds = _sound_groups()
	_start_ambient_sounds()
	_start_elevators()
	_start_pickups()
	_start_terrain_scroll()
	_add_hint()
	_add_minimap()
	# After the terrain collision and decals exist, so their materials get the look too.
	_apply_lighting.call_deferred()


func _json(path: String) -> Variant:
	var f := FileAccess.open(path, FileAccess.READ)
	return JSON.parse_string(f.get_as_text()) if f else null


func _actor_pos(rec: Dictionary) -> Vector3:
	return _layout.actor_position({"pos_x": rec.header.pos_x.value, "pos_z": rec.header.pos_z.value})


# ─── Effects / decals / animation / sound ────────────────────────────────────

func _start_effects() -> void:
	var reg := EffectPlayer.build_registry(_decoded, _json(_base + "csfx.json"))
	var root := Node3D.new()
	root.name = "Effects"
	add_child(root)
	for rec: Dictionary in _decoded:
		if not EffectPlayer.placed_at_load(rec):
			continue
		var id: Variant = rec.fields.get("EffectHeader.effect_id", {}).get("value")
		if id == null or not reg.effects.has(int(id)):
			continue
		var anchor := Node3D.new()
		anchor.position = _actor_pos(rec)
		var p := EffectPlayer.new()
		p.setup(reg.effects[int(id)], reg.effects, _base + "particles", true)
		anchor.add_child(p)
		root.add_child(anchor)


func _add_decals() -> void:
	var decals := LevelOverlays.build_decals(_decoded, _json(_base + "dcs.json"), _base.trim_suffix("/"), _layout)
	add_child(decals)


func _start_animations() -> void:
	for n: Node in find_children("*", "Node3D", true, false):
		var path := n.scene_file_path
		if not path.ends_with(".glb"):
			continue
		ObjLoader.apply_fc_materials(n, path)   # additive (CONFT's light beams) and env-mapped
		# 3DTA texture animation on this part's own mesh (<model>_mesh).
		var uv := UvAnimator.read_glb_uv_animations(path)
		if uv:
			for mi: MeshInstance3D in n.find_children(path.get_file().get_basename() + "_mesh", "MeshInstance3D", true, false):
				var a := UvAnimator.new()
				a.setup(mi, uv, path)
				n.add_child(a)
				break
		# Clip animation for props; X1 parts are left to player.gd / parked at rest.
		if _under_x1(n):
			continue
		for ap: AnimationPlayer in n.find_children("*", "AnimationPlayer", false, false):
			var anim := "all_frames" if ap.has_animation("all_frames") else ""
			if anim != "":
				ap.get_animation(anim).loop_mode = Animation.LOOP_LINEAR
				ap.play(anim)


func _under_x1(n: Node) -> bool:
	var p := n.get_parent()
	while p and p != self:
		if p.name == "X1":
			return true
		p = p.get_parent()
	return false


func _sound_groups() -> Dictionary:
	## Cshd group_id -> its first entry with a tone WAV.
	var shd: Variant = _json(_base + "shd.json")
	var by_group := {}
	if shd is Dictionary:
		for t: Dictionary in shd.get("tables", []):
			for e: Dictionary in t.get("entries", []):
				if not by_group.has(int(e.group_id)) and e.get("tone_wav"):
					by_group[int(e.group_id)] = e
	return by_group


func _sound(ref: Variant) -> AudioStream:
	## The tone of a "sound N" field value, or null.
	var r := str(ref)
	if not r.begins_with("sound "):
		return null
	var e: Variant = _sounds.get(int(r.trim_prefix("sound ")))
	if e == null or not ResourceLoader.exists(_base + e.tone_wav):
		return null
	return load(_base + e.tone_wav)


func _start_ambient_sounds() -> void:
	var root := Node3D.new()
	root.name = "AmbientSounds"
	add_child(root)
	for rec: Dictionary in _decoded:
		if int(rec.class_id) == 10:
			continue   # elevators loop theirs only while moving (elevator.gd)
		var stream := _sound(rec.fields.get("BaseEntity.ambient_sound", {}).get("value", ""))
		if stream == null:
			continue
		var s := AudioStreamPlayer3D.new()
		s.stream = stream
		s.position = _actor_pos(rec) + Vector3(0, 0.5, 0)
		s.unit_size = 3.0
		s.max_distance = 35.0
		s.volume_db = -6.0
		s.finished.connect(s.play)   # loop
		s.autoplay = true
		root.add_child(s)


func view_zones() -> Array:
	## Class-95 Triggers with bitfield 0x40, where the close camera views 5 and 6 are
	## allowed (0x80031b40): [centre, half x, half z] in Godot units (fx12 size × 8 / 2;
	## width runs along Godot x, length along z, as in level_overlays.build_zones).
	var out := []
	if _decoded.is_empty():
		var decoded: Variant = _json("res://assets/%s/actors_decoded.json" % name)
		_decoded = decoded.get("actors", []) if decoded is Dictionary else []
		_layout = LevelLayout.new(FileAccess.get_file_as_string("res://assets/%s/heightmap.json" % name))
	for rec: Dictionary in _decoded:
		if int(rec.class_id) != 95 or not int(rec.fields.get("Trigger.bitfield", {}).get("raw", 0)) & 0x40:
			continue
		out.append([_actor_pos(rec), float(rec.fields["Trigger.width"].value) * 4.0,
			float(rec.fields["Trigger.length"].value) * 4.0])
	return out


# ─── Elevators / pickups ──────────────────────────────────────────────────────

func _actor_node(id: int) -> Node3D:
	return get_node_or_null("Actors/Actor_%d" % id) as Node3D


func _start_elevators() -> void:
	## Runs after the first physics frame, so the terrain collision can be ray-cast.
	await get_tree().physics_frame
	for rec: Dictionary in _decoded:
		var node := _actor_node(int(rec.id))
		if int(rec.class_id) != 10 or node == null:
			continue
		var e := Elevator.new()
		node.add_child(e)
		e.setup(rec, node, _elevator_ground(node.global_position),
			_sound(rec.fields.get("BaseEntity.ambient_sound", {}).get("value", "")),
			_sound(rec.fields.get("Elevator.end_sound", {}).get("value", "")))


func _elevator_ground(at: Vector3) -> float:
	## The game's elevator ground probe (0x80066f78 from height 0): the highest
	## terrain floor at or below +0x200 (0.0625) at this x/z, or the lowest floor if
	## all are higher. Rays only see TerrainCollision (its _pass floors are already
	## left out); props and the player are excluded.
	var space := get_world_3d().direct_space_state
	var exclude: Array[RID] = []
	for b: CollisionObject3D in find_children("*", "CollisionObject3D", true, false):
		if b.name != "TerrainCollision":
			exclude.append(b.get_rid())
	var floors: Array[float] = []
	var y := 100.0
	for i in 32:
		var q := PhysicsRayQueryParameters3D.create(Vector3(at.x, y, at.z), Vector3(at.x, -100.0, at.z))
		q.exclude = exclude
		var hit := space.intersect_ray(q)
		if hit.is_empty():
			break
		if hit.normal.y > 0.5:
			floors.append(hit.position.y)
		y = hit.position.y - 0.01
	if floors.is_empty():
		return at.y
	for f in floors:   # top down
		if f <= 0x200 / 8192.0:
			return f
	return floors[-1]


func _start_pickups() -> void:
	## Class-16 ItemPickups spin (0x8002fa20): rotation < 0 means spin at
	## ((rotational_speed * 16) / 218) >> 4 BAM per 300 Hz tick, from yaw 0. They
	## don't bob. Power-ups also flash (entity flag 0x2000000, 0.43 s on / off),
	## which isn't drawn here.
	for rec: Dictionary in _decoded:
		var node := _actor_node(int(rec.id))
		if int(rec.class_id) != 16 or node == null:
			continue
		if int(rec.fields.get("ItemPickup.rotation", {}).get("raw", 0)) >= 0:
			continue
		var rs := int(rec.fields.get("ItemPickup.rotational_speed", {}).get("raw", 0))
		var bam_per_tick := floori(float(int(rs * 16 / 218.0)) / 16.0)
		# Godot's yaw runs the other way (the map's x/z are the game's z/x).
		_spinning.append([node, -bam_per_tick * 300.0 * TAU / 4096.0])
		node.rotation.y = deg_to_rad(90.0)   # BAM 0 (level_layout._bam_to_degrees)


# ─── Lighting / fog ───────────────────────────────────────────────────────────

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_L:
			_ps1_look = not _ps1_look
			_apply_lighting()
		elif event.keycode == KEY_G:
			_fog = not _fog
			_apply_lighting()
		elif event.keycode == KEY_O:
			_toggle_overlay()
		elif event.keycode == KEY_M and _minimap:
			_minimap.visible = not _minimap.visible
			_update_hint()


func _toggle_overlay() -> void:
	if _overlay == null:   # built on first use
		_overlay = DebugOverlay.new()
		_overlay.setup(_base, _decoded, _layout)
		add_child(_overlay)
	else:
		_overlay.visible = not _overlay.visible
	_update_hint()


func _apply_lighting() -> void:
	var sun := get_node_or_null("Sun") as DirectionalLight3D
	if sun:
		sun.visible = not _ps1_look
	var we := get_node_or_null("WorldEnvironment") as WorldEnvironment
	if we and we.environment:
		var env := we.environment
		env.fog_enabled = _fog
		env.fog_light_color = env.background_color
		env.fog_density = 0.02
	for mi: MeshInstance3D in find_children("*", "MeshInstance3D", true, false):
		if mi.mesh == null or mi.material_override:
			continue   # effects/decals manage their own (unshaded) materials
		if not _lit_materials.has(mi):
			var saved := []
			for s in mi.mesh.get_surface_count():
				saved.append(mi.get_surface_override_material(s))
			_lit_materials[mi] = saved
		for s in mi.mesh.get_surface_count():
			if not _ps1_look:
				mi.set_surface_override_material(s, _lit_materials[mi][s])
				continue
			var src := mi.get_active_material(s)
			if not src is StandardMaterial3D:
				continue
			var m := (src as StandardMaterial3D).duplicate() as StandardMaterial3D
			m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
			if mi.name == "TerrainMesh":
				# PS1 vertex colour 128 = full brightness: the baked shading is ×2.
				m.vertex_color_use_as_albedo = true
				m.albedo_color = Color(2, 2, 2)
			mi.set_surface_override_material(s, m)
	_update_hint()


func _add_hint() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	_hint = Label.new()
	_hint.position = Vector2(12, 8)
	_hint.add_theme_color_override("font_outline_color", Color.BLACK)
	_hint.add_theme_constant_override("outline_size", 6)
	layer.add_child(_hint)


func _update_hint() -> void:
	if _hint:
		_hint.text = "W/S move · A/D turn · Q/E strafe · Shift slow · Space jump/brake · Shift+Space crouch\nT transform · V view · L lighting: %s · G fog: %s · M map: %s · O overlay: %s · Esc menu" % [
			"PS1" if _ps1_look else "lit", "on" if _fog else "off",
			"on" if _minimap and _minimap.visible else "off",
			"on" if _overlay and _overlay.visible else "off"]


# ─── Minimap ──────────────────────────────────────────────────────────────────

func _add_minimap() -> void:
	## The viewer's minimap (heightmap + decoded map icons) in the bottom-right corner,
	## tracking the walker. M toggles it.
	var players := find_children("*", "CharacterBody3D", true, false)
	_player = players[0] if players.size() > 0 else null
	var icons := []
	for rec: Dictionary in _decoded:
		var icon: Variant = LevelOverlays.field(rec, "map_icon")
		if icon == null:
			icon = LevelOverlays.field(rec, "map_icon_a")
		if icon is Dictionary:
			icons.append([_actor_pos(rec), icon])
	var layer := CanvasLayer.new()
	add_child(layer)
	_minimap = Minimap.new()
	_minimap.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_RIGHT)
	_minimap.offset_left = -232
	_minimap.offset_top = -232
	_minimap.offset_right = -12
	_minimap.offset_bottom = -12
	_minimap.mouse_filter = Control.MOUSE_FILTER_IGNORE
	layer.add_child(_minimap)
	_minimap.setup(_json(_base + "heightmap.json"), icons)


func _start_terrain_scroll() -> void:
	## Terrain polygons with tile-graphics bit 11 (water) scroll their UVs by the
	## chunk's two signed speeds; extract_terrain.py names them "_s<u>_<v>".
	var mi := get_node_or_null("Terrain/TerrainMesh") as MeshInstance3D
	if mi == null or mi.mesh == null:
		return
	for i in mi.mesh.get_surface_count():
		var mat := mi.mesh.surface_get_material(i)
		if mat == null:
			continue
		var m := RegEx.create_from_string("_s(-?\\d+)_(-?\\d+)").search(mat.resource_name)
		if m:
			_scrolling.append([mi, i, int(m.get_string(1)), int(m.get_string(2))])


static func _scroll_texels(speed: int, ticks: int) -> int:
	## 0x80065744: (s * clock >> 6) % 28 texels; s < 0 counts down from 27. (s > 0x20
	## blinks between 0 and 28 in the game; no level uses it.)
	if speed < 0:
		return 27 - ((-speed * ticks) >> 6) % 28
	return ((speed * ticks) >> 6) % 28


func _process(delta: float) -> void:
	_clock += delta
	var ticks := int(_clock * 300.0)
	for sc: Array in _scrolling:
		var m := (sc[0] as MeshInstance3D).get_active_material(sc[1]) as BaseMaterial3D
		if m:
			# OBJ vt = u/255 and 1 - v/255, so v runs the other way in Godot.
			m.uv1_offset = Vector3(_scroll_texels(sc[2], ticks) / 255.0, -_scroll_texels(sc[3], ticks) / 255.0, 0.0)
	for sp: Array in _spinning:
		(sp[0] as Node3D).rotation.y += sp[1] * delta
	if _minimap and _minimap.visible and _player:
		_minimap.set_camera(_player.global_position, _player.global_basis.z)   # X1 faces +Z
