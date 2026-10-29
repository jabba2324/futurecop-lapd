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
##   94 points (0x80042af4, 0x800437e8, draw 0x80044260): fixed-size screen squares,
##     speed raw / 512 u/s at a pitch measured from +y, spawn_radius and jitter bits;
##     dv/dt = (drift - v) drag_scale / drag_time - gravity_percent % of 8 u/s²;
##     flags 0x10 additive, 0x20 / 0x08 50/50, else opaque.
##   87 trail (0x80075a84, draw 0x80074104): a ribbon from the owner through its points,
##     colour_0 → 1 → 2 → black over phase_time_0..2, width size → + size_growth,
##     colour on the centreline and black edges, additive; points move by emit_speed /
##     jitter / gravity, with drag once per frame (30 fps assumed).
##   88 lightning (0x80073bbc, 0x80071b40): bolt_count bolts to random points at
##     end_radius, segment_count jittered segments, fading to black over fade_time,
##     each re-rolled half the time every flicker_interval; width / 256, flag 8 tapers.
##   90 shatter (0x8004c324): max_fragments random faces of the owner's model
##     (owner_meshes) fly off and spin; they end on landing or after duration, half
##     spawning impact_effect, the first fragment_effect_count carrying fragment_effect.
##   89 flag 4 star (0x80014490), 92 flag 0x10 double ring.
##   placement: dir(place_yaw, place_pitch) × place_distance (raw / 512) from the anchor
## Not modelled: 88's terrain snapping and player targeting (flags 0x20 / 4), 90's
## whole-model flags 8 / 0x10, 92's flag-1 textured strip (unused). Guessed: 92's
## half ring (0x200) layout. Owners don't move here, so trails only stretch to their
## moving points.
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
const POINT_GRAVITY := 8.0         # u/s²: 94's gravity_percent is of 1 game unit/s²
const FRAME_RATE := 30.0           # 87's per-frame drag, at an assumed 30 fps

var owner_radius := 0.5      # Godot units; sizes of 0 / < 0 scale with it
## Owner-less effects only spawn a copy while a player is within 16 units
## (0x8003e984); when set, a copy is skipped while this returns false.
var in_range := Callable()
## Called as sound.call("sound N", global position) for each copy's EffectHeader.sound.
var sound := Callable()
## The owner's model, which class 90 shatters into its faces: MeshInstance3Ds.
var owner_meshes: Array = []

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
		p.sound = sound
		p.owner_meshes = owner_meshes
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
	var snd: Variant = _fx.def.get("EffectHeader.sound")
	if sound.is_valid() and snd != null and is_inside_tree():
		sound.call(snd, global_position)
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
			var r := _fraw("spawn_radius")
			var vbits := _fraw("velocity_jitter_bits")
			var pbits := _fraw("position_jitter_bits")
			for i in clampi(_fraw("count", 4), 1, 64):
				var speed := _rng.randf_range(_field("speed_min"), maxf(_field("speed_max"), _field("speed_min"))) / 512.0
				# Pitch is measured from +y (0 = straight up, 1024 = horizontal).
				var from_up := _rng.randf_range(_field("pitch_min"), maxf(_field("pitch_max"), _field("pitch_min")))
				var d := _dir(_rng.randf() * 360.0, 90.0 - from_up)
				var at := d * (r / 512.0 if r > 0 else -r / 512.0 * _rng.randf())
				var dt := float(_fraw("drag_time"))
				if dt < 0.0:
					dt = _rng.randf_range(0.0, -dt)
				pts.append([at + _jitter(pbits), d * speed + _jitter(vbits), dt])
			var fade := _field("fade_time")
			_add(mi, {"kind": 94, "life": _life() if _life() > 0.0 else maxf(fade, 0.3), "pts": pts,
				"grav": _field("gravity_percent") / 100.0 * POINT_GRAVITY, "fade": fade,
				"drift": _dir(_field("drift_yaw"), _field("drift_pitch")) * _fraw("drift_speed") / 512.0,
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
			var bolts := []
			for b in clampi(_fraw("bolt_count", 1), 1, 16):
				bolts.append(_new_bolt())
			_add(mi, {"kind": 88, "life": _life() if _life() > 0.0 else maxf(_field("fade_time"), 0.4),
				"bolts": bolts, "flicker": 0.0})
		87:
			var mi := MeshInstance3D.new()
			mi.mesh = ImmediateMesh.new()
			mi.material_override = _material(1)
			var j := float(_fraw("velocity_jitter")) / 2.0
			var v := _dir(_field("emit_yaw"), _field("emit_pitch")) * _field("emit_speed") * 4096.0 \
				+ Vector3(_rng.randf_range(-j, j), _rng.randf_range(-j, j), _rng.randf_range(-j, j))
			var moving := v != Vector3.ZERO or _fraw("gravity") != 0
			_add(mi, {"kind": 87, "life": _life(), "moving": moving,
				"pts": [{"pos": Vector3.ZERO, "vel": v * SPEED_93, "age": 0.0}]})
		90:
			_spawn_fragments()


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


func _jitter(bits: int) -> Vector3:
	## rand32 >> (32 - n) per component: uniform in +-2^(n-1) raw units (/512 Godot).
	if bits <= 0:
		return Vector3.ZERO
	var h := float(1 << (bits - 1)) / 512.0
	return Vector3(_rng.randf_range(-h, h), _rng.randf_range(-h, h), _rng.randf_range(-h, h))


func _child(id: int) -> Node3D:
	## A fragment / impact effect as a child player, or null.
	if id < 0 or not _registry.has(id) or _depth >= MAX_CHAIN:
		return null
	var p := new()
	p.owner_radius = owner_radius
	p.in_range = in_range
	p.sound = sound
	p.owner_meshes = owner_meshes
	p.setup(_registry[id], _registry, _sprites, false, _depth + 1)
	return p


func _new_bolt() -> Dictionary:
	## 0x80070e48 / 0x80070730: the far end on the end_radius sphere (flags 1 / 0x20:
	## a random horizontal point within it), interior points jittered sideways by
	## random walks that shrink toward the far end.
	var r := _fraw("end_radius") / 512.0
	var flags := _fraw("tail_flags")
	var far: Vector3
	if flags & 0x21:
		var a := _rng.randf() * TAU
		far = Vector3(cos(a), 0.0, sin(a)) * _rng.randf() * r
	else:
		far = _dir(_rng.randf() * 360.0, _rng.randf_range(-90.0, 90.0)) * r
	var n := clampi(_fraw("segment_count", 6), 1, 32)
	var axis := Vector3.UP if absf(far.normalized().y) < 0.9 else Vector3.RIGHT
	var p := far.cross(axis).normalized()
	var q := far.cross(p).normalized()
	var step := 16.0 * (_fraw("jitter") >> 7) / 8192.0
	var pts := [Vector3.ZERO]
	var wp := 0.0
	var wq := 0.0
	var walk := [-1.0, -0.5, 0.0, 0.5, 1.0]
	var along := [0.5, 1.0, 1.5]
	for i in range(1, n):
		wp += walk[_rng.randi() % 5]
		wq += walk[_rng.randi() % 5]
		var k: float = ((i - 1) + along[_rng.randi() % 3]) / n
		pts.append(far * k + (p * wp + q * wq) * (1.0 - float(i - 1) / n) * step)
	pts.append(far)
	return {"pts": pts, "age": 0.0}


func _spawn_fragments() -> void:
	## 0x8004c324: up to max_fragments random faces of the owner's model fly off.
	var faces := []   # [a, b, c, [uv_a, uv_b, uv_c], material]
	for mi: MeshInstance3D in owner_meshes:
		if not is_instance_valid(mi) or mi.mesh == null or not is_inside_tree():
			continue
		var xf := global_transform.affine_inverse() * mi.global_transform
		for si in mi.mesh.get_surface_count():
			var arr := mi.mesh.surface_get_arrays(si)
			var v: PackedVector3Array = arr[Mesh.ARRAY_VERTEX]
			var uv: Variant = arr[Mesh.ARRAY_TEX_UV]
			var idx: Variant = arr[Mesh.ARRAY_INDEX]
			var mat := mi.get_active_material(si)
			var count: int = (idx as PackedInt32Array).size() if idx else v.size()
			for t in range(0, count - 2, 3):
				var ids: Array = [t, t + 1, t + 2] if not idx else [idx[t], idx[t + 1], idx[t + 2]]
				var uvs := [Vector2.ZERO, Vector2.ZERO, Vector2.ZERO]
				if uv:
					uvs = [uv[ids[0]], uv[ids[1]], uv[ids[2]]]
				faces.append([xf * v[ids[0]], xf * v[ids[1]], xf * v[ids[2]], uvs, mat])
	if faces.is_empty():
		return
	var n := mini(clampi(_fraw("max_fragments", 10), 1, 200), faces.size())
	var carry := _fraw("fragment_effect_count")
	var flags := _fraw("tail_flags")
	var rmin := float(_fraw("radial_speed_min"))
	var rmax := float(_fraw("radial_speed_max"))
	for k in n:
		var f: Array = faces[_rng.randi() % faces.size()]
		var c: Vector3 = (f[0] + f[1] + f[2]) / 3.0
		var st := SurfaceTool.new()
		st.begin(Mesh.PRIMITIVE_TRIANGLES)
		for i in 3:
			st.set_uv(f[3][i])
			st.add_vertex(f[i] - c)
		var mi := MeshInstance3D.new()
		mi.mesh = st.commit()
		if f[4]:
			mi.material_override = f[4]
		mi.position = c
		# The game draws the radial speed from [max, 2 max - min] (sic), vy upward.
		var radial := c.normalized() * _rng.randf_range(rmax, 2.0 * rmax - rmin) / 512.0
		radial.y = absf(radial.y)
		var vel := radial + _dir(_field("direction_yaw"), _field("direction_pitch")) \
			* _rng.randf_range(_field("direction_speed_min"), maxf(_field("direction_speed_max"), _field("direction_speed_min"))) / 512.0
		if flags & 4:
			vel.y = 0.0
		var spin := Vector2(_rng.randf_range(_field("spin_min"), _field("spin_max")),
			_rng.randf_range(_field("spin_min"), _field("spin_max"))) * TICKS / 256.0 * TAU / 4096.0
		var life := _life() + _rng.randf_range(0.0, 255.0) / TICKS if _life() > 0.0 else 0.0
		_add(mi, {"kind": 90, "life": life, "vel": vel, "spin": spin,
			"grav": -_rng.randf_range(2.5, 2.98) * _field("gravity_percent") / 100.0,
			"floor": -0.05, "impact": _rng.randf() < 0.5, "r": (f[0] - c).length()})
		if carry < 0 or k < carry:
			var fx := _child(_ref_id("Effect90.fragment_effect"))
			if fx:
				mi.add_child(fx)


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
				var drag_scale := float(_fraw("drag_scale"))
				for p: Array in it.pts:
					# dv/dt = (drift - v) * drag_scale / drag_time - gravity
					if p[2] > 0.0:
						p[1] += (it.drift - p[1]) * minf(drag_scale / p[2] * delta, 1.0)
					p[1].y -= it.grav * delta
					p[0] += p[1] * delta
					im.surface_set_color(c)
					im.surface_add_vertex(p[0])
				im.surface_end()
			88:
				_update_lightning(node, it, delta, cam)
			87:
				gone = gone or _update_trail(node, it, delta, cam)
			90:
				it.vel.y += it.grav * delta
				node.position += it.vel * delta
				node.rotate_object_local(Vector3.RIGHT, it.spin.x * delta)
				node.rotate_object_local(Vector3.UP, it.spin.y * delta)
				if node.position.y < it.floor and it.vel.y < 0.0:
					gone = true   # landed (0x8004c0c0)
				if gone and it.impact:
					var fx := _child(_ref_id("Effect90.impact_effect"))
					if fx:
						fx.owner_radius = it.r
						fx.position = node.position
						add_child(fx)
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
	if _fraw("tail_flags") & 4:
		# Star (0x80014490): a spike per segment, tip at R in the rim colour, base
		# half-width R >> star_shift in the centre colour.
		var base: float = it.r / float(1 << clampi(_fraw("star_shift"), 0, 15))
		for k in range(1, n + 1):
			var th: float = it.roll + TAU * k / n
			var d := Vector3(cos(th), sin(th), 0.0)
			var side := Vector3(-sin(th), cos(th), 0.0) * base
			im.surface_set_color(centre)
			im.surface_add_vertex(b * side)
			im.surface_set_color(rim)
			im.surface_add_vertex(b * (d * it.r))
			im.surface_set_color(centre)
			im.surface_add_vertex(b * -side)
		im.surface_end()
		return
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
	var n: int = maxi(it.segs, 8)
	var bands := [[r0, r1, inner, outer]]
	if flags & 0x10:   # double ring: a second band outward, colours mirrored
		bands.append([r1, r1 + (r1 - r0), outer, inner])
	for i in n:
		var a0 := TAU * i / n
		var a1 := TAU * (i + 1) / n
		# 0x200: only the first half of each 45° octant (0x8009b214; guessed)
		if flags & 0x200 and fmod(a0, PI / 4.0) >= PI / 8.0 - 0.0001:
			continue
		var p := [Vector3(cos(a0), 0, sin(a0)), Vector3(cos(a1), 0, sin(a1))]
		for band: Array in bands:
			for v: Array in [[p[0], band[0], band[2]], [p[0], band[1], band[3]], [p[1], band[1], band[3]],
					[p[0], band[0], band[2]], [p[1], band[1], band[3]], [p[1], band[0], band[2]]]:
				im.surface_set_color(v[2])
				im.surface_add_vertex(v[0] * v[1])
	im.surface_end()
	var black := inner.r + inner.g + inner.b + outer.r + outer.g + outer.b <= 0.0
	return black or it.size >= _fraw("max_size")


func _strip(im: ImmediateMesh, a: Vector3, b: Vector3, wa: float, wb: float, ca: Color, cb: Color, cam: Basis) -> void:
	## One segment as two additive gouraud quads: colour on the centreline, black at
	## the edges, laid out across the screen direction.
	var side := (b - a).cross(global_basis.inverse() * cam.z)
	if side.length() < 0.000001:
		return
	side = side.normalized()
	for e: float in [1.0, -1.0]:
		var oa := side * wa * e
		var ob := side * wb * e
		for v: Array in [[a, ca], [a + oa, Color.BLACK], [b + ob, Color.BLACK],
				[a, ca], [b + ob, Color.BLACK], [b, cb]]:
			im.surface_set_color(v[1])
			im.surface_add_vertex(v[0])


func _update_lightning(node: MeshInstance3D, it: Dictionary, delta: float, cam: Basis) -> void:
	## 0x80071b40 / flicker 0x800718b8 / draw 0x80071fa4.
	var fade := maxf(_field("fade_time"), 0.001)
	var interval := _field("flicker_interval")
	var flags := _fraw("tail_flags")
	it.flicker += delta
	var regen: bool = interval > 0.0 and it.flicker >= interval
	if regen:
		it.flicker -= interval
	# Drift: v = offset * drift / 65536 raw per tick, pos += v * dt / 8.
	var drift_rate := _fraw("drift") / 65536.0 * TICKS / 8.0
	var im := node.mesh as ImmediateMesh
	im.clear_surfaces()
	im.surface_begin(Mesh.PRIMITIVE_TRIANGLES)
	var w := _fraw("width") / 256.0
	for i in it.bolts.size():
		var b: Dictionary = it.bolts[i]
		b.age += delta
		if regen and _rng.randf() < 0.5 and (not flags & 2 or b.age >= fade):
			b = _new_bolt()
			it.bolts[i] = b
		var line: Vector3 = b.pts[-1]
		for k in range(1, b.pts.size() - 1):
			var on_line: Vector3 = line * (float(k) / (b.pts.size() - 1))
			b.pts[k] += (b.pts[k] - on_line) * drift_rate * delta
		var c := _rgb("colour", 255.0).lerp(Color.BLACK, clampf(b.age / fade, 0.0, 1.0))
		var n: int = b.pts.size() - 1
		for k in n:
			var wa := w if not flags & 8 else w * (1.0 - float(k) / n)
			var wb := w if not flags & 8 else w * (1.0 - float(k + 1) / n)
			_strip(im, b.pts[k], b.pts[k + 1], wa, wb, c, c, cam)
	im.surface_end()


func _trail_colour(age: float) -> Color:
	## colour_0 -> colour_1 -> colour_2 -> black over phase_time_0/1/2 (alpha 0 after).
	var t := [_field("phase_time_0"), _field("phase_time_1"), _field("phase_time_2")]
	var c := [_rgb("colour_0", 255.0), _rgb("colour_1", 255.0), _rgb("colour_2", 255.0), Color.BLACK]
	for k in 3:
		if age < t[k]:
			return (c[k] as Color).lerp(c[k + 1], age / t[k])
		age -= t[k]
	return Color(0, 0, 0, 0)


func _update_trail(node: MeshInstance3D, it: Dictionary, delta: float, cam: Basis) -> bool:
	## 0x80075a84 / draw 0x80074104: the head stays at the owner (here the effect's
	## origin); each point ages through the colour phases and widens by size_growth
	## over them. Returns true once every point is past its last phase.
	var total := _field("phase_time_0") + _field("phase_time_1") + _field("phase_time_2")
	var drag := _fraw("drag") / 4096.0
	for p: Dictionary in it.pts:
		p.age += delta
		if it.moving:
			p.vel.y += _fraw("gravity") * GRAVITY_93 * delta
			p.vel *= pow(maxf(1.0 - drag, 0.0), delta * FRAME_RATE)   # once per frame
			p.pos += p.vel * delta
	var im := node.mesh as ImmediateMesh
	im.clear_surfaces()
	var head_w := _fraw("size") / 512.0
	var prev := Vector3.ZERO
	var prev_w := head_w
	var prev_c := _rgb("colour_0", 255.0)
	var alive := false
	im.surface_begin(Mesh.PRIMITIVE_TRIANGLES)
	for p: Dictionary in it.pts:
		var c := _trail_colour(p.age)
		alive = alive or c.a > 0.0
		var w := head_w + _fraw("size_growth") / 512.0 * clampf(p.age / maxf(total, 0.001), 0.0, 1.0)
		_strip(im, prev, p.pos, prev_w, w, prev_c, Color(c.r, c.g, c.b), cam)
		prev = p.pos
		prev_w = w
		prev_c = Color(c.r, c.g, c.b)
	im.surface_end()
	return not alive and total > 0.0
