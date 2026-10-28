## Ambient scene dressing for a generated level scene (root of
## scenes/<LEVEL>_level.tscn). Visual/audio only; nothing reacts to the player.
##   - effects the level places at load, looping (effect_player.gd)
##   - class-97 decals (level_overlays.build_decals)
##   - prop animation clips (all_frames, looping) and 3DTA texture animations
##   - ambient sounds: BaseEntity.ambient_sound → the Cshd entry with that
##     group_id → its tone WAV, looping 3D audio at the actor
##   - lighting: L toggles the PS1 look (baked terrain vertex colours ×2, unshaded,
##     no sun; the default) and real-time lighting; G toggles distance fog
## Reads res://assets/<LEVEL>/ (synced by tools/sync_godot_assets.py).
extends Node3D

const EffectPlayer := preload("res://scripts/effect_player.gd")
const UvAnimator := preload("res://scripts/uv_animator.gd")
const LevelLayout := preload("res://scripts/level_layout.gd")
const LevelOverlays := preload("res://scripts/level_overlays.gd")

var _base := ""
var _layout: RefCounted
var _decoded: Array = []
var _ps1_look := true
var _fog := false
var _lit_materials: Dictionary = {}   # MeshInstance3D -> [surface override materials in lit mode]
var _hint: Label


func _ready() -> void:
	_base = "res://assets/%s/" % name
	var decoded: Variant = _json(_base + "actors_decoded.json")
	_decoded = decoded.get("actors", []) if decoded is Dictionary else []
	_layout = LevelLayout.new(FileAccess.get_file_as_string(_base + "heightmap.json"))
	_start_effects()
	_add_decals()
	_start_animations()
	_start_ambient_sounds()
	_add_hint()
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


func _start_ambient_sounds() -> void:
	var shd: Variant = _json(_base + "shd.json")
	var by_group := {}
	if shd is Dictionary:
		for t: Dictionary in shd.get("tables", []):
			for e: Dictionary in t.get("entries", []):
				if not by_group.has(int(e.group_id)) and e.get("tone_wav"):
					by_group[int(e.group_id)] = e
	var root := Node3D.new()
	root.name = "AmbientSounds"
	add_child(root)
	for rec: Dictionary in _decoded:
		var ref := str(rec.fields.get("BaseEntity.ambient_sound", {}).get("value", ""))
		if not ref.begins_with("sound "):
			continue
		var e: Variant = by_group.get(int(ref.trim_prefix("sound ")))
		if e == null or not ResourceLoader.exists(_base + e.tone_wav):
			continue
		var s := AudioStreamPlayer3D.new()
		s.stream = load(_base + e.tone_wav)
		s.position = _actor_pos(rec) + Vector3(0, 0.5, 0)
		s.unit_size = 3.0
		s.max_distance = 35.0
		s.volume_db = -6.0
		s.finished.connect(s.play)   # loop
		s.autoplay = true
		root.add_child(s)


# ─── Lighting / fog ───────────────────────────────────────────────────────────

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_L:
			_ps1_look = not _ps1_look
			_apply_lighting()
		elif event.keycode == KEY_G:
			_fog = not _fog
			_apply_lighting()


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
		_hint.text = "W/S walk · A/D turn · L lighting: %s · G fog: %s · Esc menu" % [
			"PS1" if _ps1_look else "lit", "on" if _fog else "off"]
