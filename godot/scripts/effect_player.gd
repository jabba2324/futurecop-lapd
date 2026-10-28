## Approximate preview of a decoded effect definition (actor classes 87–94 /
## Csfx records, see tools/decode_actors.py): plays the shared EffectHeader
## timing (duration, repeat count/interval, chained next effect after
## next_delay, placement yaw/pitch/distance) with a simple visual per class:
##   87 trail ribbon · 88 lightning · 89 flash disc · 90 model shatter (spawns its
##   fragment effect) · 92 shockwave ring · 93 sprite (Cpyr image) · 94 particles
## Sizes/speeds are fx12 game units (×8 for Godot units) with the time base
## assumed to be seconds; treat it as a preview, not a faithful replay.
## Usage: var p := EffectPlayer.new(); p.setup(effect, registry, sprite_dir, loop)
##   effect   = {"cls": int, "def": {"EffectHeader.duration": v, "Effect93.size": v, ...}}
##   registry = effect_id -> effect (for chained next effects and fragments)
extends Node3D

const U := 8.0
const MAX_CHAIN := 6

var _fx: Dictionary
var _registry: Dictionary
var _sprites: String
var _loop := false
var _depth := 0
var _t := 0.0
var _spawned := 0
var _next_spawn := 0.0
var _chained := false
var _items: Array = []   # {node, age, life, kind, ...}
var _rng := RandomNumberGenerator.new()


static func build_registry(decoded: Array, csfx: Variant) -> Dictionary:
	## {effects: effect_id -> {cls, def}, damage_sets: set_id -> record}.
	## Csfx table 0 (global effects) is overlaid with the level's own class 87–94
	## effect-definition actors (decoded = actors_decoded.json "actors").
	var effects := {}
	var damage_sets := {}
	if csfx is Array:
		for table: Dictionary in csfx:
			if int(table.get("table_index", 0)) != 0:
				continue
			for r: Dictionary in table.get("records", []):
				var cls := int(r.get("class", 0))
				if cls < 87 or cls > 94 or cls == 91:
					continue
				var def := {}
				for seg: String in r.get("fields", {}):
					if r.fields[seg] is Dictionary:
						for name: String in r.fields[seg]:
							def[seg + "." + name] = r.fields[seg][name].get("value")
				if def.get("EffectHeader.effect_id") != null:
					effects[int(def["EffectHeader.effect_id"])] = {"cls": cls, "def": def}
	for rec: Dictionary in decoded:
		var cls := int(rec.class_id)
		var def := {}
		for k: String in rec.fields:
			def[k] = rec.fields[k].get("value")
		if cls == 91 and def.get("DamageEffectSet.set_id") != null:
			damage_sets[int(def["DamageEffectSet.set_id"])] = rec
		elif cls >= 87 and cls <= 94 and cls != 91 and def.get("EffectHeader.effect_id") != null:
			effects[int(def["EffectHeader.effect_id"])] = {"cls": cls, "def": def}
	return {"effects": effects, "damage_sets": damage_sets}


static func placed_at_load(rec: Dictionary) -> bool:
	var flags: Variant = rec.get("fields", {}).get("EffectHeader.effect_flags", {}).get("value")
	return flags is Array and "placed_at_load" in flags


func setup(fx: Dictionary, registry: Dictionary, sprite_dir: String, loop: bool, depth: int = 0) -> void:
	_fx = fx
	_registry = registry
	_sprites = sprite_dir
	_loop = loop
	_depth = depth
	_rng.randomize()
	# Placement offset: yaw/pitch/distance from the anchor.
	var yaw := deg_to_rad(_num("EffectHeader.place_yaw"))
	var pitch := deg_to_rad(_num("EffectHeader.place_pitch"))
	var dist := _num("EffectHeader.place_distance") * U
	position = Vector3(cos(pitch) * sin(yaw), sin(pitch), cos(pitch) * cos(yaw)) * dist


func _num(key: String, default := 0.0) -> float:
	var v: Variant = _fx.def.get(key)
	if v is String and v.begins_with("random "):
		var r: PackedStringArray = String(v).trim_prefix("random ").split("..")
		return _rng.randf_range(r[0].to_float(), r[1].to_float())
	return float(v) if v is int or v is float else default


func _field(name: String, default := 0.0) -> float:
	return _num("Effect%d.%s" % [_fx.cls, name], default)


func _colour(prefix: String) -> Color:
	return Color(_field(prefix + "_r") / 128.0, _field(prefix + "_g") / 128.0, _field(prefix + "_b") / 128.0)


func _ref_id(key: String) -> int:
	var v: Variant = _fx.def.get(key)
	return int(str(v).trim_prefix("effect ")) if v is String and str(v).begins_with("effect ") else -1


func _process(delta: float) -> void:
	_t += delta
	var repeats := int(_num("EffectHeader.repeat_count"))
	var interval := maxf(_num("EffectHeader.repeat_interval"), 0.05)
	var total := 1 if repeats == 0 else (1000000 if repeats < 0 else repeats + 1)
	while _spawned < total and _t >= _next_spawn:
		_spawn()
		_spawned += 1
		_next_spawn += interval
		if repeats < 0 and _spawned > 60:   # endless emitters: cap live spawns via item life
			break
	var nxt := _ref_id("EffectHeader.next_effect_id")
	if not _chained and nxt >= 0 and _registry.has(nxt) and _depth < MAX_CHAIN \
			and _t >= _num("EffectHeader.next_delay") + _num("EffectHeader.duration"):
		_chained = true
		var p := new()
		p.setup(_registry[nxt], _registry, _sprites, false, _depth + 1)
		add_child(p)
	_update_items(delta)
	var done := _spawned >= total and _items.is_empty() and (_chained or nxt < 0)
	if repeats < 0 and _spawned > 60:
		_spawned = 0   # keep endless effects running
		_next_spawn = _t
	if done:
		if _loop and _t > 0.5:
			_restart()
		elif not _loop and get_child_count() == 0:
			queue_free()


func _restart() -> void:
	for c in get_children():
		c.queue_free()
	_items.clear()
	_t = 0.0
	_spawned = 0
	_next_spawn = 0.8   # short pause between loops
	_chained = false


# ─── Spawning ─────────────────────────────────────────────────────────────────

func _material(c: Color, tex: Texture2D = null, billboard := true) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	m.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
	m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	m.cull_mode = BaseMaterial3D.CULL_DISABLED
	m.vertex_color_use_as_albedo = true
	m.albedo_color = c
	m.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
	if billboard:
		m.billboard_mode = BaseMaterial3D.BILLBOARD_ENABLED
	if tex:
		m.albedo_texture = tex
		m.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
	return m


func _add(node: GeometryInstance3D, item: Dictionary) -> void:
	node.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(node)
	item["node"] = node
	item["age"] = 0.0
	_items.append(item)


func _dir(yaw_deg: float, pitch_deg: float) -> Vector3:
	var y := deg_to_rad(yaw_deg)
	var p := deg_to_rad(pitch_deg)
	return Vector3(cos(p) * sin(y), sin(p), cos(p) * cos(y))


func _spawn() -> void:
	match int(_fx.cls):
		93:
			var tex: Texture2D = null
			var img := _sprites.path_join("cpyr001_p%03d.png" % int(_field("cpyr_id")))
			if img.begins_with("res://"):
				tex = load(img) if ResourceLoader.exists(img) else null
			elif FileAccess.file_exists(img):
				tex = ImageTexture.create_from_image(Image.load_from_file(img))
			var q := QuadMesh.new()
			var s := maxf(absf(_field("size")) / 4096.0 * U, 0.1)
			q.size = Vector2(s, s)
			var mi := MeshInstance3D.new()
			mi.mesh = q
			mi.material_override = _material(_colour("colour_0"), tex)
			_add(mi, {"kind": 93, "life": maxf(_field("fade_time"), 0.3) + _num("EffectHeader.duration"),
				"vel": _dir(_field("emit_yaw"), _field("emit_pitch")) * _field("emit_speed") * U,
				"grav": _field("gravity") / 4096.0 * U, "grow": _field("size_rate") / 4096.0 * U, "size": s})
		89:
			var q := QuadMesh.new()
			var s := maxf(absf(_field("size")) / 4096.0 * U, 0.3)
			q.size = Vector2(s, s)
			var mi := MeshInstance3D.new()
			mi.mesh = q
			mi.material_override = _material(_colour("centre_end"), _disc_texture())
			_add(mi, {"kind": 89, "life": maxf(_num("EffectHeader.duration"), _field("centre_ramp_time") + 0.3),
				"grow": _field("size_rate") / 4096.0 * U, "size": s})
		94:
			var n := clampi(int(_field("count", 4)), 1, 40)
			var c := _colour("colour_0")
			for i in n:
				var q := QuadMesh.new()
				q.size = Vector2(0.12, 0.12)
				var mi := MeshInstance3D.new()
				mi.mesh = q
				mi.material_override = _material(c)
				var speed := _rng.randf_range(_field("speed_min"), maxf(_field("speed_max"), _field("speed_min"))) / 4096.0 * U
				var pitch := _rng.randf_range(_field("pitch_min"), maxf(_field("pitch_max"), _field("pitch_min")))
				_add(mi, {"kind": 94, "life": maxf(_field("fade_time"), 0.3),
					"vel": _dir(_rng.randf() * 360.0, pitch) * speed, "grav": _field("gravity_percent") / 100.0 * 9.8})
		92:
			var t := TorusMesh.new()
			var w := maxf(_field("ring_width") / 4096.0 * U, 0.1)
			t.inner_radius = 1.0
			t.outer_radius = 1.0 + w
			var mi := MeshInstance3D.new()
			mi.mesh = t
			mi.material_override = _material(_colour("outer_colour"), null, false)
			mi.rotation_degrees.x = _field("ring_pitch") - 90.0
			mi.rotation_degrees.y = _field("ring_yaw")
			var start := maxf(_field("size") / 4096.0 * U, 0.1)
			mi.scale = Vector3.ONE * start
			_add(mi, {"kind": 92, "life": maxf(_field("fade_time"), 0.5) + 0.5, "r": start,
				"grow": maxf(_field("growth_speed") / 4096.0 * U, 0.5), "max": maxf(_field("max_size") / 4096.0 * U, start)})
		88:
			var mi := MeshInstance3D.new()
			mi.mesh = ImmediateMesh.new()
			mi.material_override = _material(_colour("colour"), null, false)
			_add(mi, {"kind": 88, "life": maxf(maxf(_num("EffectHeader.duration"), _field("fade_time")), 0.4),
				"radius": maxf(_field("end_radius") * U, 0.4), "bolts": clampi(int(_field("bolt_count", 3)), 1, 12),
				"segs": clampi(int(_field("segment_count", 6)), 2, 16)})
		87:
			var mi := MeshInstance3D.new()
			mi.mesh = ImmediateMesh.new()
			mi.material_override = _material(_colour("colour_0"), null, false)
			_add(mi, {"kind": 87, "life": maxf(_field("phase_time_0") + _num("EffectHeader.duration"), 0.4),
				"vel": _dir(_field("emit_yaw"), _field("emit_pitch")) * maxf(_field("emit_speed"), 1.0), "trail": []})
		90:
			var frag := _ref_id("Effect90.fragment_effect")
			if frag >= 0 and _registry.has(frag) and _depth < MAX_CHAIN:
				for i in clampi(int(_field("fragment_effect_count", 3)), 1, 12):
					var p := new()
					p.setup(_registry[frag], _registry, _sprites, false, _depth + 1)
					p.position += Vector3(_rng.randf_range(-1, 1), _rng.randf_range(0, 1), _rng.randf_range(-1, 1)) * 0.6
					add_child(p)


static var _disc: ImageTexture

static func _disc_texture() -> ImageTexture:
	## Soft radial disc for flashes (white; the material colour tints it).
	if _disc == null:
		var img := Image.create(32, 32, false, Image.FORMAT_RGBA8)
		for y in 32:
			for x in 32:
				var d := Vector2(x - 15.5, y - 15.5).length() / 15.5
				img.set_pixel(x, y, Color(1, 1, 1, clampf(1.0 - d, 0.0, 1.0)))
		_disc = ImageTexture.create_from_image(img)
	return _disc


# ─── Updating ─────────────────────────────────────────────────────────────────

func _update_items(delta: float) -> void:
	for it: Dictionary in _items.duplicate():
		it.age += delta
		var k: float = clampf(it.age / it.life, 0.0, 1.0)
		var node: MeshInstance3D = it.node
		var mat := node.material_override as StandardMaterial3D
		mat.albedo_color.a = 1.0 - k
		match int(it.kind):
			93, 94:
				it.vel.y -= it.get("grav", 0.0) * delta
				node.position += it.vel * delta
				if it.kind == 93:
					node.scale = Vector3.ONE * maxf(1.0 + it.grow * it.age / it.size, 0.05)
			89:
				node.scale = Vector3.ONE * maxf(1.0 + it.grow * it.age / it.size, 0.05)
			92:
				it.r = minf(it.r + it.grow * delta, it.max)
				node.scale = Vector3.ONE * it.r
			88:
				var im := node.mesh as ImmediateMesh
				im.clear_surfaces()
				im.surface_begin(Mesh.PRIMITIVE_LINES)
				for b in it.bolts:
					var end: Vector3 = Vector3(_rng.randf_range(-1, 1), _rng.randf_range(-0.2, 1), _rng.randf_range(-1, 1)).normalized() * it.radius
					var prev := Vector3.ZERO
					for s in range(1, it.segs + 1):
						var p: Vector3 = end * float(s) / it.segs
						if s < it.segs:
							p += Vector3(_rng.randf_range(-1, 1), _rng.randf_range(-1, 1), _rng.randf_range(-1, 1)) * it.radius * 0.12
						im.surface_add_vertex(prev)
						im.surface_add_vertex(p)
						prev = p
				im.surface_end()
			87:
				var head: Vector3 = (it.trail.back() if it.trail else Vector3.ZERO) + it.vel * delta
				it.trail.append(head)
				var im := node.mesh as ImmediateMesh
				im.clear_surfaces()
				if it.trail.size() >= 2:
					im.surface_begin(Mesh.PRIMITIVE_LINE_STRIP)
					for p in it.trail:
						im.surface_add_vertex(p)
					im.surface_end()
		if it.age >= it.life:
			node.queue_free()
			_items.erase(it)
