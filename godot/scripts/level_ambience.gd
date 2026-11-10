## Ambient scene dressing for a generated level scene (root of
## scenes/<LEVEL>_level.tscn). Visual/audio only; nothing reacts to the player
## except elevators that wait for a rider.
##   - effects the level places at load, started when the walker comes near, and
##     the damage sets' spawn effects of their actors (effect_player.gd)
##   - class-97 decals (level_overlays.build_decals)
##   - prop animation clips (all_frames, looping) and 3DTA texture animations
##   - additive and env-mapped (sphere-map reflection) materials of .glb props
##     (ObjLoader.apply_fc_materials)
##   - elevators (elevator.gd) move and carry the walker; item pickups spin;
##     teleporters (teleporters.gd) move it; blob shadows (shadows.gd); route
##     actors drive their Cnet paths without reacting to anything (route_actors.gd)
##   - scrolling terrain (water) UVs
##   - ambient sounds: BaseEntity.ambient_sound "sound N" → the Cshd entry with
##     script_id N (_sound_table) → its tone WAV, looping 3D audio at the actor;
##     effects play their EffectHeader.sound per copy
##   - minimap (minimap.gd) of the heightmap, map icons and the walker; M toggles
##   - debug overlay (debug_overlay.gd): trigger zones, paths, special floors, and the
##     spawner actors that wait for a script (hidden otherwise); O toggles
##   - damage preview (damage_preview.gd): K steps the nearest damageable prop through
##     its damage stages and destruction (effects, wreck model)
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
const Teleporters := preload("res://scripts/teleporters.gd")
const DamagePreview := preload("res://scripts/damage_preview.gd")
const Shadows := preload("res://scripts/shadows.gd")
const RouteActors := preload("res://scripts/route_actors.gd")
const EFFECT_RANGE := 16.0     # 0x20000 raw: placed effects start / spawn within this
const TELEPORT_SOUND := 105   # script sound id the teleporters' on_damage script plays

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
var _sounds: Dictionary = {}     # _sound_table(): sound id -> Cshd entry
var _spinning: Array = []        # [pickup node, yaw rad/s]
var _flashing: Array = []        # power-up MeshInstance3Ds (red tint every 128 ticks)
var _flash_on := false
var _tint: StandardMaterial3D
var _scrolling: Array = []       # [terrain MeshInstance3D, surface, u speed, v speed]
var _effects: Dictionary = {}     # EffectPlayer.build_registry
var _effects_root: Node3D
var _pending_effects: Array = []  # [anchor, effect] waiting for a player in range
var _damage: Node                 # damage_preview.gd, built on the first K
var _route_nodes: Array = []      # actors route_actors.gd moves
var _clock := 0.0
var _prop_spins: Array = []       # [prop node, spin_speed, sway amplitude BAM, base yaw deg]


func _ready() -> void:
	_base = "res://assets/%s/" % name
	if _decoded.is_empty():   # the player may have asked first (view_zones)
		var decoded: Variant = _json(_base + "actors_decoded.json")
		_decoded = decoded.get("actors", []) if decoded is Dictionary else []
		_layout = LevelLayout.new(FileAccess.get_file_as_string(_base + "heightmap.json"))
	_start_effects()
	_start_spawn_effects()
	_add_decals()
	_start_animations()
	_paint_x1s()
	_sounds = _sound_table()
	_start_ambient_sounds()
	_start_props()
	_start_elevators()
	_start_pickups()
	_start_teleporters()
	_start_routes()
	_start_shadows()
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
	## Effects the level places at load (flag 1) wait until a player comes within
	## EFFECT_RANGE (0x8003fd74 / 0x8003ff7c), then play once as in the game (endless
	## ones keep going); their copies only spawn while a player is in range.
	_effects = EffectPlayer.build_registry(_decoded, _json(_base + "csfx.json"))
	_effects_root = Node3D.new()
	_effects_root.name = "Effects"
	add_child(_effects_root)
	for rec: Dictionary in _decoded:
		if not EffectPlayer.placed_at_load(rec):
			continue
		var id: Variant = rec.fields.get("EffectHeader.effect_id", {}).get("value")
		if id == null or not _effects.effects.has(int(id)):
			continue
		var anchor := Node3D.new()
		anchor.position = _actor_pos(rec)
		_effects_root.add_child(anchor)
		_pending_effects.append([anchor, _effects.effects[int(id)]])


func _start_spawn_effects() -> void:
	## A damage set's spawn_effect starts with its actor (0x8003b5ac), attached to it.
	for rec: Dictionary in _decoded:
		var ref := str(rec.fields.get("BaseEntity.explosion", {}).get("value", ""))
		var node := _actor_node(int(rec.id))
		if not ref.begins_with("damage_effect_set ") or node == null or int(rec.class_id) == 1:
			continue
		var ds: Variant = _effects.damage_sets.get(int(ref.trim_prefix("damage_effect_set ")))
		var v := str(ds.fields.get("DamageEffectSet.spawn_effect", {}).get("value", "")) if ds else ""
		if v.begins_with("effect ") and _effects.effects.has(int(v.trim_prefix("effect "))):
			var p := EffectPlayer.new()
			p.sound = _play_effect_sound
			p.setup(_effects.effects[int(v.trim_prefix("effect "))], _effects.effects, _base + "particles", false)
			node.add_child(p)


func _player_near(at: Vector3) -> bool:
	return _player != null and _player.global_position.distance_to(at) < EFFECT_RANGE


func _start_pending_effects() -> void:
	for pe: Array in _pending_effects.duplicate():
		var anchor: Node3D = pe[0]
		if _player_near(anchor.global_position):
			_pending_effects.erase(pe)
			var p := EffectPlayer.new()
			p.in_range = _player_near
			p.sound = _play_effect_sound
			p.setup(pe[1], _effects.effects, _base + "particles", false)
			anchor.add_child(p)


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
		# Class-96 props are timed by _start_props (their animation_speed and flags).
		if _under_x1(n) or _actor_class(n) == 96:
			continue
		for ap: AnimationPlayer in n.find_children("*", "AnimationPlayer", false, false):
			var anim := "all_frames" if ap.has_animation("all_frames") else ""
			if anim != "":
				ap.get_animation(anim).loop_mode = Animation.LOOP_LINEAR
				ap.play(anim)


func _paint_x1s() -> void:
	## Each actor's BaseEntity uv_offset (LevelLayout.apply_uv_offset), after the glTF
	## materials are converted: the X1's paint (red in Precinct Assault, P2 black in
	## Crime War) and other actors' colour variants (turrets, walkers' shirts).
	for rec: Dictionary in _decoded:
		var node := _actor_node(int(rec.id))
		if node == null:
			continue
		if int(rec.class_id) == 1:
			if node.get_node_or_null("X1"):
				LevelLayout.apply_uv_offset(node.get_node("X1"), rec)
		else:
			LevelLayout.apply_uv_offset(node, rec, false)


func _actor_class(n: Node) -> int:
	## The class of the Actor_<id> node above n, or -1.
	var p := n.get_parent()
	while p and p != self:
		if p.has_meta("class"):
			return int(p.get_meta("class"))
		p = p.get_parent()
	return -1


func _start_props() -> void:
	## Class-96 Props, as the prop update 0x80084398 runs them:
	##  - frames advance only when animation_speed v != 0, one frame per 300 / v
	##    ticks (integer division; 1 -> 255 ticks), i.e. about v frames per second.
	##    prop_flags 0x04 plays them back and forth, 0x08 holds them (script message
	##    0x1a). 0x20 (random two-frame flicker) also needs v != 0: the one prop with
	##    it (OV) has v = 0 and starts hidden, so it isn't modelled.
	##  - spin_speed s != 0: yaw = (clock & 0xfff) * s BAM, a full turn every 4096 / s
	##    ticks; with spin_angle A it sways instead: rotation_y plus a triangle wave
	##    0, -A, 0, +A over the same period.
	##  - prop_flags 0x40: the low byte of rotation_z is an ambient sound id
	##    (0x8008363c, the same sound player as BaseEntity.ambient_sound).
	##  - 0x10 (hidden) is applied by generate_scene.gd.
	for rec: Dictionary in _decoded:
		if int(rec.class_id) != 96:
			continue
		var node := _actor_node(int(rec.id))
		if node == null:
			continue
		var f: Dictionary = rec.fields
		var flags := int(f.get("Prop.prop_flags", {}).get("raw", 0))
		if flags & 0x10:
			continue   # hidden props skip the whole update, sound included
		var speed := int(f.get("Prop.animation_speed", {}).get("raw", 0))
		for ap: AnimationPlayer in node.find_children("*", "AnimationPlayer", true, false):
			if not ap.has_animation("all_frames") or speed == 0 or flags & 0x08:
				continue
			var ticks_per_frame: int = 255 if speed == 1 else 300 / speed
			ap.get_animation("all_frames").loop_mode = \
				Animation.LOOP_PINGPONG if flags & 0x04 else Animation.LOOP_LINEAR
			ap.speed_scale = (300.0 / ticks_per_frame) / 10.0   # clips are 10 fps
			ap.play("all_frames")
		var spin := int(f.get("Prop.spin_speed", {}).get("raw", 0))
		if spin != 0:
			var amp := int(f.get("Prop.spin_angle", {}).get("raw", 0)) * 4096 / 360
			_prop_spins.append([node, spin, amp, node.rotation_degrees.y])
		if flags & 0x40:
			var sound_id := int(f.get("Prop.rotation_z", {}).get("raw", 0)) & 0xff
			var stream := _sound("sound %d" % sound_id)
			if stream:
				var s := AudioStreamPlayer3D.new()
				s.stream = stream
				s.unit_size = 3.0
				s.max_distance = 35.0
				s.volume_db = -6.0
				s.finished.connect(s.play)   # loop
				s.autoplay = true
				node.add_child(s)


func _update_prop_spins(ticks: int) -> void:
	for ps: Array in _prop_spins:
		var phase: int = ((ticks & 0xfff) * int(ps[1])) & 0xfff
		var node := ps[0] as Node3D
		if int(ps[2]) == 0:
			# Full spin from BAM 0; Godot yaw = 90 - degrees (level_layout._bam_to_degrees).
			node.rotation_degrees.y = 90.0 - phase * 360.0 / 4096.0
		else:
			var a: int = ps[2]
			var t: float = 2.0 * a * phase / 4096.0   # 0 .. 2A
			var tri: float = (-2.0 * t if t <= a / 2.0 else -2.0 * (a - t)) if t <= a \
				else (2.0 * (t - a) if t - a <= a / 2.0 else 2.0 * (a - (t - a)))
			node.rotation_degrees.y = float(ps[3]) - tri * 360.0 / 4096.0


func _under_x1(n: Node) -> bool:
	var p := n.get_parent()
	while p and p != self:
		if p.name == "X1":
			return true
		p = p.get_parent()
	return false


func _sound_table() -> Dictionary:
	## How the game resolves a sound id N (0x80045494): N >= 70 is the Cshd entry with
	## script_id N in the level's mission table (0x800a18d0); below 70 it is the global
	## table's entry with that script_id and the wanted positional flag (3D table
	## 0x800a1830 / plain 0x800a1880). Keys: N for >= 70, [N, positional] below.
	var shd: Variant = _json(_base + "shd.json")
	var out := {}
	if shd is Dictionary:
		for t: Dictionary in shd.get("tables", []):
			for e: Dictionary in t.get("entries", []):
				if not e.get("tone_wav"):
					continue
				var n := int(e.get("script_id", -1))
				var key: Variant = n if t.get("kind") == "mission" else [n, bool(e.get("positional"))]
				if not out.has(key):
					out[key] = e
	return out


func _sound(ref: Variant, positional := true) -> AudioStream:
	## The tone of a "sound N" field value (BaseEntity.ambient_sound, elevator and
	## effect sounds: all Cshd script ids), or null. Until 2026-10-29 these were looked
	## up by group_id, which picked weapon-bank samples for every ambient sound.
	var r := str(ref)
	if not r.begins_with("sound "):
		return null
	var n := int(r.trim_prefix("sound "))
	var e: Variant = _sounds.get(n if n >= 70 else [n, positional])
	if e == null or not ResourceLoader.exists(_base + e.tone_wav):
		return null
	return load(_base + e.tone_wav)


func _play_effect_sound(ref: Variant, at: Vector3) -> void:
	## EffectHeader.sound, played once per effect copy (0x8003e984): the plain entry,
	## panned from where it starts. Ids 61–68 and 100–118 only play with a player
	## within 16 units (0x80046c84).
	var stream := _sound(ref, false)
	if stream == null:
		return
	var n := int(str(ref).trim_prefix("sound "))
	if ((n >= 61 and n <= 68) or (n >= 100 and n <= 118)) and not _player_near(at):
		return
	var s := AudioStreamPlayer3D.new()
	s.stream = stream
	s.unit_size = 3.0
	s.max_distance = 35.0
	s.volume_db = -4.0
	add_child(s)
	s.global_position = at
	s.finished.connect(s.queue_free)
	s.play()


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


func _start_teleporters() -> void:
	await get_tree().physics_frame
	var t := Teleporters.new()
	add_child(t)
	t.setup(_decoded, _layout, func(at: Vector3) -> float:
		var f := _floors(at)
		return f[0] if f.size() > 0 else at.y, _sound("sound %d" % TELEPORT_SOUND, false))


func _start_routes() -> void:
	await get_tree().physics_frame
	var nets: Variant = _json(_base + "nets.json")
	if not nets is Dictionary:
		return
	var r := RouteActors.new()
	add_child(r)
	_route_nodes = r.setup(_decoded, nets.get("nets", []), _layout, get_node("Actors"), _floors)


func _start_shadows() -> void:
	## After the first physics frame (terrain collision) and the elevators' bodies.
	await get_tree().physics_frame
	await get_tree().physics_frame
	var space := get_world_3d().direct_space_state
	var exclude: Array[RID] = []
	for b: CollisionObject3D in find_children("*", "CollisionObject3D", true, false):
		if b.name != "TerrainCollision":
			exclude.append(b.get_rid())
	var floor_at := func(at: Vector3) -> float:
		var q := PhysicsRayQueryParameters3D.create(at + Vector3(0, 0x200 / 8192.0, 0), at - Vector3(0, 100, 0))
		q.exclude = exclude
		var hit := space.intersect_ray(q)
		return hit.position.y if hit else NAN
	var actors: Array = []
	var moving: Array = []
	for rec: Dictionary in _decoded:
		var node := _actor_node(int(rec.id))
		var flags: Variant = rec.fields.get("BaseEntity.bitfield", {}).get("value")
		if node == null or not (flags is Array and "has_shadow" in flags) or node is CharacterBody3D:
			continue
		actors.append(node)
		if int(rec.class_id) == 10 or node in _route_nodes:
			moving.append(node)
	if _player:
		actors.append(_player)
		moving.append(_player)
	var s := Shadows.new()
	add_child(s)
	s.setup(actors, moving, floor_at)


func _elevator_ground(at: Vector3) -> float:
	## The game's elevator ground probe (0x80066f78 from height 0): the highest
	## terrain floor at or below +0x200 (0.0625) at this x/z, or the lowest floor if
	## all are higher.
	var floors := _floors(at)
	if floors.is_empty():
		return at.y
	for f in floors:   # top down
		if f <= 0x200 / 8192.0:
			return f
	return floors[-1]


func _floors(at: Vector3) -> Array[float]:
	## Terrain floor heights at this x/z, top down. Rays only see TerrainCollision (its
	## _pass floors are already left out); props and the player are excluded.
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
	return floors


func _start_pickups() -> void:
	## Class-16 ItemPickups spin (0x8002fa20): rotation < 0 means spin at
	## ((rotational_speed * 16) / 218) >> 4 BAM per 300 Hz tick, from yaw 0. They
	## don't bob. Power-ups (item_flags & 0xf0) flash: while bit 7 of the 300 Hz clock
	## is set (128 ticks on, 128 off) they get entity flag 0x2000000 (0x80030224), and
	## the primitive builder (0x80015f38) keeps red and halves green and blue, the
	## game's hit tint. Drawn as a multiply overlay.
	for rec: Dictionary in _decoded:
		var node := _actor_node(int(rec.id))
		if int(rec.class_id) != 16 or node == null:
			continue
		if int(rec.fields.get("ItemPickup.item_flags", {}).get("raw", 0)) & 0xf0:
			_flashing.append_array(node.find_children("*", "MeshInstance3D", true, false))
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
		elif event.keycode == KEY_K and _player:
			if _damage == null:
				_damage = DamagePreview.new()
				add_child(_damage)
				_damage.setup(get_node("Actors"), _decoded, _effects, _base + "particles", _play_effect_sound)
			_damage.step(_player.global_position)
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
	# Spawners that wait for a script (generate_scene.gd) show with the overlay.
	for a: Node in get_node("Actors").get_children():
		if a.has_meta("waiting_spawn"):
			(a as Node3D).visible = _overlay.visible
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
				# Vertex colours are the PS1 shading / 256 (128 = texture unchanged).
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
		_hint.text = "W/S move · A/D turn · Q/E strafe · Shift slow · Space jump/brake · Shift+Space crouch\nT transform · V view · K damage prop · L lighting: %s · G fog: %s · M map: %s · O overlay: %s · Esc menu" % [
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
		var m := (sc[0] as MeshInstance3D).get_active_material(sc[1])
		# OBJ vt = u/255 and 1 - v/255, so v runs the other way in Godot.
		var off := Vector2(_scroll_texels(sc[2], ticks) / 255.0, -_scroll_texels(sc[3], ticks) / 255.0)
		if m is BaseMaterial3D:
			(m as BaseMaterial3D).uv1_offset = Vector3(off.x, off.y, 0.0)
		elif m is ShaderMaterial:
			(m as ShaderMaterial).set_shader_parameter("uv_offset", off)   # slfx_water
	_start_pending_effects()
	var flash := (ticks & 0x80) != 0
	if flash != _flash_on and not _flashing.is_empty():
		_flash_on = flash
		if _tint == null:
			_tint = StandardMaterial3D.new()
			_tint.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
			_tint.blend_mode = BaseMaterial3D.BLEND_MODE_MUL
			_tint.albedo_color = Color(1.0, 0.5, 0.5)
		for mi: MeshInstance3D in _flashing:
			mi.material_overlay = _tint if flash else null
	for sp: Array in _spinning:
		(sp[0] as Node3D).rotation.y += sp[1] * delta
	_update_prop_spins(ticks)
	if _minimap and _minimap.visible and _player:
		_minimap.set_camera(_player.global_position, _player.global_basis.z)   # X1 faces +Z
