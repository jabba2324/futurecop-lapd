## Plays a decoded effect definition (actor classes 87–94 / Csfx records, see
## tools/decode_actors.py) as SLUS_007.39 does. Decoded:
##   timing (0x8003c404 register, 0x8003cae4 tick, 0x8003e984 spawn), in 300 Hz ticks:
##     repeat_count 0 with an interval = endless, N = N spawns in all, -1 (or no
##     interval) = once; every repeat is a fresh copy of the effect (copies don't
##     repeat or chain). The next effect starts next_delay after the effect does
##     (at once for 0), or after the last repeat with flag 0x800. A copy lives for
##     `duration`; 0 = no timed death (a 93 sprite then lives one animation cycle).
##   93 sprite (0x80040874 / 0x80040e60, draw 0x8004a048): velocity
##     dir(emit_yaw, emit_pitch) × emit_speed raw / 436.9 u/s, gravity raw × 0.002682
##     u/s² with + = up; `size` is the half width, raw / 512 (min 40; 0 = the owner's
##     radius, < 0 = |v| / 4096 of it), size_rate per 256 ticks; Cpyr strip frame
##     floor(t × anim_fps) % frames. Tail flags: 0x04 billboard (else a world quad by
##     rotation_y / rotation_x), 0x08 billboard roll by rotation_y, 0x02 alone 50/50
##     untinted, 0x10 / 0x20 tinted (colour / 128) additive / subtractive, with the
##     colour_0 → colour_1 ramp over fade_time (0x100: drain to black, blue first).
##   89 flash disc (0x8006ff44 / 0x8006fb7c): a camera-facing additive gouraud fan,
##     radius size / 512 (size_rate per 256 ticks), centre and rim colours / 255
##     ramping start → end over their times (flag 1 ping-pongs), moving as 93; not
##     drawn beyond 16 units from the camera.
##   92 ring (0x8006e814 / 0x8006d770): a flat annulus from size to size + ring_width
##     (raw / 512) in the plane of ring_yaw / ring_pitch (1024 = flat), growing
##     growth_speed per 256 ticks (less growth_decel per 128 ticks, down to 4096); all
##     channels drop alike so the brightest reaches 0 at fade_time; ends when black
##     or at max_size. Flag 0x200 = half ring.
##   94 points (0x800437e8, draw 0x80044260): fixed-size screen squares, speed raw / 512
##     u/s; flags 0x10 additive, 0x20 / 0x08 50/50, else opaque.
##   placement: dir(place_yaw, place_pitch) × place_distance (raw / 512) from the anchor
## Guessed / simplified: 92's blend (additive here) and its flag-1 textured strip and
## double ring, 89's star shape, 94's gravity (gravity_percent of the walker's), and
## 87 (trail), 88 (lightning) and 90 (shatter), which are rough previews.
## Usage: var p := EffectPlayer.new(); p.setup(effect, registry, sprite_dir, loop)
##   effect   = {"cls": int, "def": {"EffectHeader.duration": value, ...}, "raw": {...}}
##   registry = effect_id -> effect (for chained next effects and fragments)
##   loop     = replay when finished (viewer previews); the game plays effects once
extends Node3D

const U := 8.0               # fx12 value → Godot units
const MAX_CHAIN := 6
const MAX_COPIES := 48
const TICKS := 300.0
const FLASH_RANGE := 16.0
const SPEED_93 := 1.0 / 436.9      # emit_speed raw → u/s
const GRAVITY_93 := 0.002682       # gravity raw → u/s², + = up
const GROW := TICKS / 256.0 / 512.0  # "per 256 ticks" raw rate → u/s
const WALKER_GRAVITY := 26.1       # u/s² (0x9800 per 1/37.5 s step)

var owner_radius := 0.5      # Godot units; sizes of 0 / < 0 scale with it
## Owner-less effects only spawn a copy while a player is within 16 units
## (0x8003e984); when set, a copy is skipped while this returns false.
var in_range := Callable()

var _fx: Dictionary
var _registry: Dictionary
var _sprites: String
var _loop := false
var _depth := 0
var _t := 0.0
var _spawned := 0
var _total := 1
var _interval := 0.0
var _chain_at := -1.0        # seconds; -1 = no chain
var _chained := false
var _items: Array = []       # {node, age, life, kind, ...}
var _rng := RandomNumberGenerator.new()


static func build_registry(decoded: Array, csfx: Variant, table := 0) -> Dictionary:
	## {effects: effect_id -> {cls, def, raw}, damage_sets: set_id -> record}.
	## Csfx table 0 (global effects) is overlaid with the level's own class 87–94
	## effect-definition actors (decoded = actors_decoded.json "actors"). Table 1 is the
	## player's effects (pass decoded = []), e.g. effect 4 at the X1's transform.
	var effects := {}
	var damage_sets := {}
	if csfx is Array:
		for tbl: Dictionary in csfx:
			if int(tbl.get("table_index", 0)) != table:
				continue
			for r: Dictionary in tbl.get("records", []):
				var cls := int(r.get("class", 0))
				if cls < 87 or cls > 94:
					continue
				var def := {}
				var raw := {}
				var fields := {}   # actors_decoded.json shape, for damage sets
				for seg: String in r.get("fields", {}):
					if r.fields[seg] is Dictionary:
						for name: String in r.fields[seg]:
							def[seg + "." + name] = r.fields[seg][name].get("value")
							raw[seg + "." + name] = r.fields[seg][name].get("raw")
							fields[seg + "." + name] = r.fields[seg][name]
				if cls == 91:
					# The global damage sets most props' BaseEntity.explosion points at.
					if def.get("DamageEffectSet.set_id") != null:
						damage_sets[int(def["DamageEffectSet.set_id"])] = {"id": -1, "class_id": 91, "fields": fields}
				elif def.get("EffectHeader.effect_id") != null:
					effects[int(def["EffectHeader.effect_id"])] = {"cls": cls, "def": def, "raw": raw}
	for rec: Dictionary in decoded:
		var cls := int(rec.class_id)
		var def := {}
		var raw := {}
		for k: String in rec.fields:
			def[k] = rec.fields[k].get("value")
			raw[k] = rec.fields[k].get("raw")
		if cls == 91 and def.get("DamageEffectSet.set_id") != null:
			damage_sets[int(def["DamageEffectSet.set_id"])] = rec
		elif cls >= 87 and cls <= 94 and cls != 91 and def.get("EffectHeader.effect_id") != null:
			effects[int(def["EffectHeader.effect_id"])] = {"cls": cls, "def": def, "raw": raw}
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
	position = _dir(_num("EffectHeader.place_yaw"), _num("EffectHeader.place_pitch")) \
		* _num("EffectHeader.place_distance") * U
	_start()


func _start() -> void:
	_t = 0.0
	_spawned = 0
	_chained = false
	var count := int(_raw("EffectHeader.repeat_count"))
	_interval = _num("EffectHeader.repeat_interval")
	if count == 1 or _raw("EffectHeader.repeat_interval") < 1:
		count = -1
		_interval = 0.0
	_total = 1 if count < 0 else (1 << 30 if count == 0 else count)
	var nxt := _ref_id("EffectHeader.next_effect_id")
	_chain_at = -1.0
	if nxt >= 0 and _registry.has(nxt) and _depth < MAX_CHAIN:
		if int(_raw("EffectHeader.effect_flags")) & 0x800:
			_chain_at = (_total - 1) * _interval if _total < (1 << 30) else -1.0
		else:
			_chain_at = _num("EffectHeader.next_delay")


func _num(key: String, default := 0.0) -> float:
	var v: Variant = _fx.def.get(key)
	if v is String and v.begins_with("random "):
		var r: PackedStringArray = String(v).trim_prefix("random ").split("..")
		return _rng.randf_range(r[0].to_float(), r[1].to_float())
	return float(v) if v is int or v is float else default


func _raw(key: String, default := 0) -> int:
	var v: Variant = _fx.get("raw", {}).get(key)
	return int(v) if v is int or v is float else default


func _field(name: String, default := 0.0) -> float:
	return _num("Effect%d.%s" % [_fx.cls, name], default)


func _fraw(name: String, default := 0) -> int:
	return _raw("Effect%d.%s" % [_fx.cls, name], default)


func _rgb(prefix: String, scale: float) -> Color:
	return Color(_field(prefix + "_r") / scale, _field(prefix + "_g") / scale, _field(prefix + "_b") / scale)


func _ref_id(key: String) -> int:
	var v: Variant = _fx.def.get(key)
	return int(str(v).trim_prefix("effect ")) if v is String and str(v).begins_with("effect ") else -1


func _size(raw: int) -> float:
	## A size field in Godot units: raw / 512, or relative to the owner's radius.
	if raw == 0:
		return owner_radius
	if raw < 0:
		return -raw / 4096.0 * owner_radius
	return raw / 512.0


func _process(delta: float) -> void:
	_t += delta
	while _spawned < _total and _t >= _spawned * _interval:
		if not in_range.is_valid() or in_range.call(global_position):
			_spawn()
		_spawned += 1
		if _interval <= 0.0:
			break
	if not _chained and _chain_at >= 0.0 and _t >= _chain_at:
		_chained = true
		var p := new()
		p.owner_radius = owner_radius
		p.in_range = in_range
		p.setup(_registry[_ref_id("EffectHeader.next_effect_id")], _registry, _sprites, false, _depth + 1)
		add_child(p)
	_update_items(delta)
	var done := _spawned >= _total and _items.is_empty() and (_chained or _chain_at < 0.0) \
		and get_children().all(func(c: Node) -> bool: return not c is Node3D or c.is_queued_for_deletion())
	if done:
		if _loop:
			if _t > 0.5:
				for c in get_children():
					c.queue_free()
				_start()
				_t = -0.8   # short pause between loops
		else:
			queue_free()


# ─── Spawning ─────────────────────────────────────────────────────────────────

func _material(blend: int, tex: Texture2D = null) -> StandardMaterial3D:
	## blend: 0 opaque, 1 additive, 2 subtractive, 3 50/50.
	var m := StandardMaterial3D.new()
	m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	m.cull_mode = BaseMaterial3D.CULL_DISABLED
	m.vertex_color_use_as_albedo = true
	match blend:
		1:
			m.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
		2:
			m.blend_mode = BaseMaterial3D.BLEND_MODE_SUB
	if blend != 0:
		m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		m.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
	else:
		m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
	if blend == 3:
		m.albedo_color.a = 0.5
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
	if _items.size() > MAX_COPIES:
		(_items[0].node as Node).queue_free()
		_items.remove_at(0)


func _dir(yaw_deg: float, pitch_deg: float) -> Vector3:
	var y := deg_to_rad(yaw_deg)
	var p := deg_to_rad(pitch_deg)
	return Vector3(cos(p) * sin(y), sin(p), cos(p) * cos(y))


func _life() -> float:
	## A copy's life in seconds; 0 = no timed death.
	return _num("EffectHeader.duration")


func _load_sprite(id: int) -> Texture2D:
	var img := _sprites.path_join("cpyr001_p%03d.png" % id)
	if img.begins_with("res://"):
		return load(img) if ResourceLoader.exists(img) else null
	if FileAccess.file_exists(img):
		return ImageTexture.create_from_image(Image.load_from_file(img))
	return null


func _spawn() -> void:
	match int(_fx.cls):
		93:
			_spawn_sprite()
		89:
			var mi := MeshInstance3D.new()
			mi.mesh = ImmediateMesh.new()
			mi.material_override = _material(1)
			_add(mi, {"kind": 89, "life": _life(), "r": _size(_fraw("size")),
				"vel": _dir(_field("emit_yaw"), _field("emit_pitch")) * _field("emit_speed") * 4096.0 * SPEED_93,
				"grav": _field("gravity") * GRAVITY_93, "grow": _field("size_rate") * GROW,
				"segs": maxi(_fraw("segments"), 4), "roll": deg_to_rad(_field("rotation"))})
		94:
			var flags := _fraw("tail_flags")
			var mat := _material(1 if flags & 0x10 else (3 if flags & 0x28 else 0))
			mat.use_point_size = true
			mat.point_size = maxf(absf(_field("size")), 1.0) * 1.5
			var mi := MeshInstance3D.new()
			mi.mesh = ImmediateMesh.new()
			mi.material_override = mat
			var pts := []
			for i in clampi(_fraw("count", 4), 1, 64):
				var speed := _rng.randf_range(_field("speed_min"), maxf(_field("speed_max"), _field("speed_min"))) / 512.0
				var pitch := _rng.randf_range(_field("pitch_min"), maxf(_field("pitch_max"), _field("pitch_min")))
				pts.append([Vector3.ZERO, _dir(_rng.randf() * 360.0, pitch) * speed])
			var fade := _field("fade_time")
			_add(mi, {"kind": 94, "life": _life() if _life() > 0.0 else maxf(fade, 0.3), "pts": pts,
				"grav": _field("gravity_percent") / 100.0 * WALKER_GRAVITY, "fade": fade,
				"to_black": int(_fraw("colour_mode")) & 1})
		92:
			var mi := MeshInstance3D.new()
			mi.mesh = ImmediateMesh.new()
			mi.material_override = _material(1)
			mi.basis = Basis(Vector3.UP, deg_to_rad(_field("ring_yaw"))) \
				* Basis(Vector3.RIGHT, deg_to_rad(90.0 - _field("ring_pitch")))
			_add(mi, {"kind": 92, "life": _life(), "size": float(_fraw("size")),
				"speed": float(_fraw("growth_speed")), "segs": maxi(_fraw("segments"), 8)})
		88:
			var mi := MeshInstance3D.new()
			mi.mesh = ImmediateMesh.new()
			mi.material_override = _material(1)
			_add(mi, {"kind": 88, "life": maxf(maxf(_life(), _field("fade_time")), 0.4),
				"radius": maxf(_field("end_radius") * U, 0.4), "bolts": clampi(int(_field("bolt_count", 3)), 1, 12),
				"segs": clampi(int(_field("segment_count", 6)), 2, 16)})
		87:
			var mi := MeshInstance3D.new()
			mi.mesh = ImmediateMesh.new()
			mi.material_override = _material(1)
			_add(mi, {"kind": 87, "life": maxf(_field("phase_time_0") + _life(), 0.4),
				"vel": _dir(_field("emit_yaw"), _field("emit_pitch")) * maxf(_field("emit_speed"), 1.0), "trail": []})
		90:
			var frag := _ref_id("Effect90.fragment_effect")
			if frag >= 0 and _registry.has(frag) and _depth < MAX_CHAIN:
				for i in clampi(int(_field("fragment_effect_count", 3)), 1, 12):
					var p := new()
					p.owner_radius = owner_radius
					p.setup(_registry[frag], _registry, _sprites, false, _depth + 1)
					p.position += Vector3(_rng.randf_range(-1, 1), _rng.randf_range(0, 1), _rng.randf_range(-1, 1)) * 0.6
					add_child(p)


func _spawn_sprite() -> void:
	var flags := _fraw("tail_flags")
	var tex := _load_sprite(int(_field("cpyr_id")))
	var frames := 1
	if tex and tex.get_width() > 0:
		frames = maxi(tex.get_height() / tex.get_width(), 1)
	var tinted := flags & 0x30 != 0
	var blend := 1 if flags & 0x10 else (2 if flags & 0x20 else (3 if flags & 0x02 else 0))
	var mat := _material(blend, tex)
	mat.uv1_scale = Vector3(1.0, 1.0 / frames, 1.0)
	var q := QuadMesh.new()
	q.size = Vector2(2.0, 2.0)   # scaled to the half width
	var mi := MeshInstance3D.new()
	mi.mesh = q
	mi.material_override = mat
	var size_raw := _fraw("size")
	var half := _size(size_raw) if size_raw <= 0 else maxf(size_raw, 40) / 512.0
	var fps := _field("anim_fps")
	var life := _life()
	if life <= 0.0:
		life = frames / fps if fps > 0.0 else maxf(_field("fade_time"), 1.0)
	var fade := _field("fade_time")
	_add(mi, {"kind": 93, "life": life, "half": half, "grow": _field("size_rate") * GROW,
		"vel": _dir(_field("emit_yaw"), _field("emit_pitch")) * _field("emit_speed") * 4096.0 * SPEED_93,
		"grav": _field("gravity") * GRAVITY_93, "frames": frames, "fps": fps,
		"billboard": flags & 0x0C != 0, "roll": flags & 0x08 != 0,
		"yaw": deg_to_rad(_field("rotation_y")), "pitch": deg_to_rad(_field("rotation_x")),
		"yaw_rate": _field("rotation_y_speed") * TICKS / 256.0 * TAU / 4096.0,
		"pitch_rate": _field("rotation_x_speed") * TICKS / 256.0 * TAU / 4096.0,
		"tinted": tinted, "c0": _rgb("colour_0", 128.0) if tinted else Color.WHITE,
		"c1": _rgb("colour_1", 128.0), "fade": fade if tinted and fade * 4800.0 >= 16.0 else 0.0,
		"drain": flags & 0x100 != 0})


# ─── Updating ─────────────────────────────────────────────────────────────────

func _camera_basis() -> Basis:
	var cam := get_viewport().get_camera_3d() if is_inside_tree() else null
	return cam.global_basis if cam else Basis.IDENTITY


func _ramp(a: Color, b: Color, k: float) -> Color:
	return a.lerp(b, clampf(k, 0.0, 1.0))


static func _drain(c: Color, k: float) -> Color:
	## Drain r+g+b to black over k 0→1, blue first, then green, then red.
	var left := (c.r + c.g + c.b) * clampf(k, 0.0, 1.0)
	var out := c
	for ch in [2, 1, 0]:
		var take := minf(out[ch], left)
		out[ch] -= take
		left -= take
	return out


func _update_items(delta: float) -> void:
	var cam := _camera_basis()
	for it: Dictionary in _items.duplicate():
		it.age += delta
		var node: MeshInstance3D = it.node
		var gone: bool = it.life > 0.0 and it.age >= it.life
		match int(it.kind):
			93:
				it.vel.y += it.grav * delta
				node.position += it.vel * delta
				it.half = maxf(it.half + it.grow * delta, 0.0)
				it.yaw += it.yaw_rate * delta
				it.pitch += it.pitch_rate * delta
				var b: Basis
				if it.billboard:
					b = cam * (Basis(Vector3.BACK, it.yaw) if it.roll else Basis.IDENTITY)
					b = global_basis.inverse() * b
				else:
					b = Basis(Vector3.UP, it.yaw) * Basis(Vector3.RIGHT, it.pitch)
				node.basis = b.orthonormalized().scaled(Vector3.ONE * maxf(it.half, 0.001))
				var mat := node.material_override as StandardMaterial3D
				if it.frames > 1:
					var f := int(it.age * it.fps) % int(it.frames) if it.fps > 0.0 else 0
					mat.uv1_offset = Vector3(0.0, float(f) / it.frames, 0.0)
				if it.tinted:
					var c: Color = it.c0
					if it.fade > 0.0:
						c = _drain(it.c0, it.age / it.fade) if it.drain else _ramp(it.c0, it.c1, it.age / it.fade)
					mat.albedo_color = Color(c.r, c.g, c.b, mat.albedo_color.a)
			89:
				it.vel.y += it.grav * delta
				node.position += it.vel * delta
				it.r = maxf(it.r + it.grow * delta, 0.0)
				node.visible = cam != Basis.IDENTITY and get_viewport().get_camera_3d().global_position \
					.distance_to(node.global_position) < FLASH_RANGE + it.r
				_draw_disc(node, it, cam)
			92:
				gone = gone or _update_ring(node, it, delta)
			94:
				var im := node.mesh as ImmediateMesh
				im.clear_surfaces()
				var k: float = it.age / maxf(it.fade, 0.01)
				var c := _rgb("colour_0", 255.0).lerp(Color.BLACK if it.to_black else _rgb("colour_1", 255.0), clampf(k, 0.0, 1.0))
				im.surface_begin(Mesh.PRIMITIVE_POINTS)
				for p: Array in it.pts:
					p[1].y -= it.grav * delta
					p[0] += p[1] * delta
					im.surface_set_color(c)
					im.surface_add_vertex(p[0])
				im.surface_end()
			88:
				_draw_lightning(node, it)
			87:
				var head: Vector3 = (it.trail.back() if it.trail else Vector3.ZERO) + it.vel * delta
				it.trail.append(head)
				var im := node.mesh as ImmediateMesh
				im.clear_surfaces()
				if it.trail.size() >= 2:
					im.surface_begin(Mesh.PRIMITIVE_LINE_STRIP)
					im.surface_set_color(_rgb("colour_0", 128.0))
					for p in it.trail:
						im.surface_add_vertex(p)
					im.surface_end()
		if gone:
			node.queue_free()
			_items.erase(it)


func _colour_at(start: String, end: String, ramp: float, age: float) -> Color:
	var k := age / ramp if ramp > 0.0 else 1.0
	if _fraw("tail_flags") & 1:   # ping-pong
		k = fmod(k, 2.0)
		k = 2.0 - k if k > 1.0 else k
	return _ramp(_rgb(start, 255.0), _rgb(end, 255.0), k)


func _draw_disc(node: MeshInstance3D, it: Dictionary, cam: Basis) -> void:
	var centre := _colour_at("centre_start", "centre_end", _field("centre_ramp_time"), it.age)
	var rim := _colour_at("rim_start", "rim_end", _field("rim_ramp_time"), it.age)
	var b := global_basis.inverse() * cam
	var im := node.mesh as ImmediateMesh
	im.clear_surfaces()
	if it.r <= 0.0:
		return
	im.surface_begin(Mesh.PRIMITIVE_TRIANGLES)
	var n: int = it.segs
	for i in n:
		var a0: float = it.roll + TAU * i / n
		var a1: float = it.roll + TAU * (i + 1) / n
		im.surface_set_color(centre)
		im.surface_add_vertex(Vector3.ZERO)
		im.surface_set_color(rim)
		im.surface_add_vertex(b * (Vector3(cos(a0), sin(a0), 0.0) * it.r))
		im.surface_add_vertex(b * (Vector3(cos(a1), sin(a1), 0.0) * it.r))
	im.surface_end()


func _update_ring(node: MeshInstance3D, it: Dictionary, delta: float) -> bool:
	## Returns true when the ring has ended (black or at max_size).
	var ticks := delta * TICKS
	it.size += it.speed * ticks / 256.0
	var decel := _field("growth_decel")
	if decel > 0.0:
		it.speed = maxf(it.speed - decel * ticks / 128.0, minf(it.speed, 4096.0))
	node.rotate_object_local(Vector3.UP, _field("yaw_speed") * ticks / 256.0 * TAU / 4096.0)
	var inner := _rgb("inner_colour", 255.0)
	var outer := _rgb("outer_colour", 255.0)
	var flags := _fraw("tail_flags")
	if not flags & 1:
		var top := maxf(maxf(maxf(inner.r, inner.g), maxf(inner.b, outer.r)), maxf(outer.g, outer.b))
		var drop: float = top * it.age / maxf(_field("fade_time"), 0.01)
		inner = Color(maxf(inner.r - drop, 0), maxf(inner.g - drop, 0), maxf(inner.b - drop, 0))
		outer = Color(maxf(outer.r - drop, 0), maxf(outer.g - drop, 0), maxf(outer.b - drop, 0))
	var r0: float = it.size / 512.0
	var r1: float = (it.size + _fraw("ring_width")) / 512.0
	var im := node.mesh as ImmediateMesh
	im.clear_surfaces()
	im.surface_begin(Mesh.PRIMITIVE_TRIANGLES)
	var n: int = it.segs
	var arc := PI if flags & 0x200 else TAU
	for i in n:
		var a0 := arc * i / n
		var a1 := arc * (i + 1) / n
		var p := [Vector3(cos(a0), 0, sin(a0)), Vector3(cos(a1), 0, sin(a1))]
		for v: Array in [[p[0], r0, inner], [p[0], r1, outer], [p[1], r1, outer],
				[p[0], r0, inner], [p[1], r1, outer], [p[1], r0, inner]]:
			im.surface_set_color(v[2])
			im.surface_add_vertex(v[0] * v[1])
	im.surface_end()
	var black := inner.r + inner.g + inner.b + outer.r + outer.g + outer.b <= 0.0
	return black or it.size >= _fraw("max_size")


func _draw_lightning(node: MeshInstance3D, it: Dictionary) -> void:
	var im := node.mesh as ImmediateMesh
	im.clear_surfaces()
	im.surface_begin(Mesh.PRIMITIVE_LINES)
	im.surface_set_color(_rgb("colour", 128.0))
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
