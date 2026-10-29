## Actors that drive their Cnet routes (classes 5 PathedActor, 20 PathedTurret,
## 26 PathedWalker, 28, 31), as SLUS_007.39's path engine moves them (init 0x80050e0c,
## junction pick 0x8004f330, lookahead 0x8004f764, per-tick move 0x8005027c, corners
## 0x8004ffd0). Movement and animation only: they never react to the player (no
## targeting, shooting or obstruction slow-down). level_ambience.gd adds one node.
##   - start: the net node at the actor's position; the next node is picked there
##   - junctions: over the node's links, not the one it came from (unless
##     enable_backtrack, flag 0x1), the one two back only as a last resort, never a
##     disabled node; a random score (seek modes 1–5 / 9–13 seek or flee the player:
##     treated as random here), or for mode 6 / 14 toward / away from node
##     junction_seek_targeting, random anyway when (rand & 0xfff) < junction_seek_chance;
##     each score is shifted right by how many actors are already heading there
##   - dead end: stop on the last node, or vanish with flag 0x8000 (0x8004d958)
##   - speed move_speed / 512 u/s (class 26: a quarter when wandering, behaviour 0/1/3,
##     unless walker_flags bit 0); distance carries over each node (no arrival radius)
##   - corners: eased over E = min(both segments) / 2 either side of the node (a
##     quadratic Bézier here), heading interpolated linearly across it, speed
##     v (1 − (1 − c/4096) min(|Δyaw|, 1024 BAM) / 1024) with c = BasePathed +6
##     (move_speed_multiplier), back to v below v / 8; flag 0x2 v / 4, 0x800 sharp
##     corners, 0x1000 fixed heading
##   - height: node heights by their height mode (0x80067684: 0 highest floor,
##     1 lowest, 2–4 1st–3rd from the bottom, 5 explicit) plus height_offset / 512;
##     flags 0x100 + 0x200 snap to the floor under the actor every frame instead
##     (0x80066f78 from its own height, upright)
##   - animation: mode-2 AnmD tracks advance by distance (frame_time raw units per
##     frame), mode 1 by time (300 / frame_time fps); class 5 plays anim_start, 20 / 28
##     track 0, 26 walk_anim (wandering) or run_anim, frozen when stopped
## Stay still: flag 0x40 (wait for script message 19), class 26 behaviour 5 (halt),
## actors not on a node. Not modelled: acceleration (near-instant), banking (0x20),
## ground tilt (0x200 alone), take-off / landing of parked fliers.
extends Node

const BAM := 4096.0
const FRAME_FPS := 10.0     # export_gltf.py bakes AnmD frames at 10 fps

var _movers: Array = []     # per actor state, see _add
var _heading_to := {}       # [net id, node] -> actors heading there
var _rng := RandomNumberGenerator.new()
var _layout: RefCounted
var _space: PhysicsDirectSpaceState3D
var _exclude: Array[RID] = []


func setup(decoded: Array, nets: Array, layout: RefCounted, actors: Node3D, floors: Callable) -> Array:
	## Returns the actor nodes that move.
	name = "RouteActors"
	_layout = layout
	_rng.randomize()
	var recs := {}
	for r: Dictionary in decoded:
		recs[int(r.id)] = r
	var moved: Array = []
	_space = actors.get_world_3d().direct_space_state
	for b: CollisionObject3D in actors.get_parent().find_children("*", "CollisionObject3D", true, false):
		if b.name != "TerrainCollision":
			_exclude.append(b.get_rid())
	for net: Dictionary in nets:
		var nodes: Array = net.nodes
		var game_xz: Array = []
		var ys: Array = []
		for n: Dictionary in nodes:
			game_xz.append(Vector2(float(n.pos_x), float(n.pos_z)))
			ys.append(_node_y(n, layout.actor_position(n), floors))
		for a: Dictionary in net.get("actors", []):
			var rec: Dictionary = recs.get(int(a.id), {})
			var node := actors.get_node_or_null("Actor_%d" % int(a.id)) as Node3D
			if rec.is_empty() or node == null or not node.visible or a.get("start_node") == null:
				continue
			if _add(rec, node, net, game_xz, ys):
				moved.append(node)
	return moved


static func _node_y(n: Dictionary, at: Vector3, floors: Callable) -> float:
	var mode := int(n.get("height_mode", 0))
	if mode == 5 and n.get("height_world") != null:
		return float(n.height_world)
	var f: Array = floors.call(at)   # top down
	if f.is_empty():
		return at.y
	match mode:
		0:
			return f[0]
		1:
			return f[-1]
		_:
			var k := mode - 2   # 1st, 2nd, 3rd floor from the bottom
			return f[f.size() - 1 - k] if k < f.size() else 0.0


func _field(rec: Dictionary, name: String) -> int:
	for k: String in rec.fields:
		if k.ends_with("." + name):
			return int(rec.fields[k].get("raw", 0))
	return 0


func _add(rec: Dictionary, node: Node3D, net: Dictionary, xz: Array, ys: Array) -> bool:
	var cls := int(rec.class_id)
	var flags := int(rec.fields.get("BasePathedEntity.bitfield", {}).get("raw", 0))
	if flags & 0x40:
		return false   # waits for script message 19
	if cls == 26 and _field(rec, "behaviour") == 5:
		return false   # halt
	var speed := _field(rec, "move_speed") / 512.0
	if cls == 26 and _field(rec, "behaviour") in [0, 1, 3] and not _field(rec, "walker_flags") & 1:
		speed /= 4.0   # wandering
	if flags & 0x2:
		speed /= 4.0
	if speed <= 0.0:
		return false
	var start := -1
	for a: Dictionary in net.actors:
		if int(a.id) == int(rec.id):
			start = int(a.start_node)
	if start < 0 or start >= xz.size():
		return false
	var m := {"node": node, "net": net, "xz": xz, "ys": ys, "flags": flags, "speed": speed,
		"corner": _field(rec, "move_speed_multiplier") / 4096.0,
		"offset": _field(rec, "height_offset") / 512.0 if not flags & 0x10 else 0.0,
		"mode": _field(rec, "junction_seek_type"), "target": _field(rec, "junction_seek_targeting"),
		"chance": _field(rec, "junction_seek_chance"),
		"prev": -1, "prev2": -1, "a": start, "b": -1, "c": -1, "s": 0.0, "stopped": false,
		"anim": null, "frame_time": 0.0, "timed": false}
	m.b = _pick(m, start, -1, -1)
	if m.b < 0:
		return false
	m.c = _pick(m, m.b, start, -1)
	_heading(m, m.b, 1)
	_setup_anim(m, rec, cls)
	_movers.append(m)
	_place(m)
	return true


func _pick(m: Dictionary, at: int, came: int, two_back: int) -> int:
	## 0x8004f330: the next node from `at`.
	var nodes: Array = m.net.nodes
	var links: Array = nodes[at].links
	var best := -1
	var best_score := -1
	var fallback := -1
	var seek: bool = int(m.mode) in [6, 14] and (_rng.randi() & 0xfff) >= int(m.chance)
	for l: Variant in links:
		var n := int(l)
		if n < 0 or n >= nodes.size() or nodes[n].get("disabled", false):
			continue
		if n == came and not int(m.flags) & 0x1:
			continue
		if n == two_back:
			fallback = n
			continue
		var score := _rng.randi() & 0xffff
		if seek and int(m.target) >= 0 and int(m.target) < nodes.size():
			var goal: Vector2 = m.xz[int(m.target)] - m.xz[at]
			var d: float = goal.dot(m.xz[n] - m.xz[at])
			score = int(_bit_length(absi(int(d * 65536.0)))) * (1 if d >= 0.0 else -1)
			if int(m.mode) == 14:
				score = -score
			score += 64
		score >>= _heading_to.get([int(m.net.id), n], 0)
		if score > best_score or (score == best_score and _rng.randf() < 0.5):
			best_score = score
			best = n
	return best if best >= 0 else fallback


static func _bit_length(v: int) -> int:
	var n := 0
	while v > 0:
		v >>= 1
		n += 1
	return n


func _heading(m: Dictionary, n: int, d: int) -> void:
	if n < 0:
		return
	var key := [int(m.net.id), n]
	_heading_to[key] = maxi(int(_heading_to.get(key, 0)) + d, 0)


func _setup_anim(m: Dictionary, rec: Dictionary, cls: int) -> void:
	var ap: AnimationPlayer = null
	var glb := ""
	for n: Node in (m.node as Node3D).find_children("*", "AnimationPlayer", true, false):
		ap = n
		break
	if ap == null:
		return
	var owner_scene := ap.get_parent()
	while owner_scene and not owner_scene.scene_file_path.ends_with(".glb"):
		owner_scene = owner_scene.get_parent()
	if owner_scene == null:
		return
	glb = owner_scene.scene_file_path
	var track := 0
	match cls:
		5:
			track = _field(rec, "anim_start")
		26:
			track = _field(rec, "walk_anim") if float(m.speed) < _field(rec, "move_speed") / 512.0 else _field(rec, "run_anim")
	var info := _track_info(glb, track)
	if info.is_empty() or not ap.has_animation(info.name):
		return
	ap.stop()
	ap.get_animation(info.name).loop_mode = Animation.LOOP_LINEAR
	ap.play(info.name)
	m.anim = ap
	m.frame_time = float(info.frame_time)
	m.timed = int(info.mode) == 1 and cls != 26


static var _glb_tracks := {}   # glb path -> track -> {name, mode, frame_time}

static func _track_info(glb: String, track: int) -> Dictionary:
	if not _glb_tracks.has(glb):
		var tracks := {}
		var f := FileAccess.open(glb, FileAccess.READ)
		if f and f.get_length() > 20:
			f.seek(12)
			var n := f.get_32()
			f.seek(20)
			var j: Variant = JSON.parse_string(f.get_buffer(n).get_string_from_utf8())
			if j is Dictionary:
				for anim: Dictionary in j.get("animations", []):
					var ex: Dictionary = anim.get("extras", {})
					if not ex.has("anmd_track"):
						continue
					var raw: Array = ex.get("raw", [0, 0, 0, 0, 0])
					var info := {"name": anim.get("name", ""), "mode": int(raw[0]), "frame_time": float(raw[4])}
					tracks[int(ex.anmd_track)] = info
					for k in ex.get("duplicate_tracks", []):
						tracks[int(k)] = info
		_glb_tracks[glb] = tracks
	return _glb_tracks[glb].get(track, {})


func _physics_process(delta: float) -> void:
	for m: Dictionary in _movers:
		if m.stopped:
			continue
		var v: float = m.speed
		var corner := _corner_factor(m)
		v *= corner
		m.s += v / 8.0 * delta   # game position units (8 Godot units each)
		var seg: float = (m.xz[m.b] - m.xz[m.a]).length()
		var hops := 0
		while m.s >= seg and not m.stopped and hops < 16:   # zero-length links: bounded
			hops += 1
			m.s -= seg
			_heading(m, m.b, -1)
			m.prev2 = m.prev
			m.prev = m.a
			m.a = m.b
			m.b = m.c
			if m.b < 0:
				m.stopped = true
				m.s = 0.0
				if int(m.flags) & 0x8000:
					(m.node as Node3D).visible = false   # destroyed at the route's end
				break
			_heading(m, m.b, 1)
			m.c = _pick(m, m.b, m.a, m.prev)
			seg = (m.xz[m.b] - m.xz[m.a]).length()
		_place(m)
		if m.anim:
			var ap := m.anim as AnimationPlayer
			if m.timed:
				ap.speed_scale = (300.0 / maxf(m.frame_time, 1.0)) / FRAME_FPS
			else:   # mode 2: frames by distance, frozen when stopped
				ap.speed_scale = 0.0 if m.stopped else v * 8192.0 / maxf(m.frame_time, 1.0) / FRAME_FPS


func _ease(m: Dictionary) -> Array:
	## [corner node, E, t 0..1, incoming dir, outgoing dir] if inside a corner zone.
	var xz: Array = m.xz
	var seg: Vector2 = xz[m.b] - xz[m.a]
	var len := seg.length()
	if len <= 0.0 or int(m.flags) & 0x800:
		return []
	var d := seg / len
	if m.s < len / 2.0 and m.prev >= 0:
		var inc: Vector2 = xz[m.a] - xz[m.prev]
		var e := minf(inc.length(), len) / 2.0
		if m.s < e and inc.length() > 0.0:
			return [m.a, e, (m.s + e) / (2.0 * e), inc.normalized(), d]
	elif m.c >= 0:
		var out: Vector2 = xz[m.c] - xz[m.b]
		var e := minf(out.length(), len) / 2.0
		var u: float = m.s - len
		if u > -e and out.length() > 0.0:
			return [m.b, e, (u + e) / (2.0 * e), d, out.normalized()]
	return []


func _corner_factor(m: Dictionary) -> float:
	var z := _ease(m)
	if z.is_empty():
		return 1.0
	var dyaw := absf(angle_difference(atan2(z[3].x, z[3].y), atan2(z[4].x, z[4].y))) * BAM / TAU
	var k := 1.0 - (1.0 - float(m.corner)) * minf(dyaw, 1024.0) / 1024.0
	return 1.0 if k < 0.125 else k


func _place(m: Dictionary) -> void:
	var xz: Array = m.xz
	var seg: Vector2 = xz[m.b] - xz[m.a] if m.b >= 0 else Vector2.ZERO
	var len := seg.length()
	var d := seg / len if len > 0.0 else Vector2.ZERO
	var p: Vector2 = xz[m.a] + d * m.s
	var dir := d
	var z := _ease(m)
	if not z.is_empty():
		var n: Vector2 = xz[z[0]]
		var e: float = z[1]
		var t: float = z[2]
		var p0: Vector2 = n - z[3] * e
		var p2: Vector2 = n + z[4] * e
		p = p0.lerp(n, t).lerp(n.lerp(p2, t), t)
		var y0 := atan2(z[3].x, z[3].y)
		dir = Vector2(sin(y0 + angle_difference(y0, atan2(z[4].x, z[4].y)) * t),
			cos(y0 + angle_difference(y0, atan2(z[4].x, z[4].y)) * t))
	var at: Vector3 = _layout.actor_position({"pos_x": p.x, "pos_z": p.y})
	var y0: float = m.ys[m.a]
	var y1: float = m.ys[m.b] if m.b >= 0 else y0
	at.y = lerpf(y0, y1, clampf(m.s / len, 0.0, 1.0) if len > 0.0 else 0.0) + m.offset
	if int(m.flags) & 0x300 == 0x300:
		var q := PhysicsRayQueryParameters3D.create(Vector3(at.x, (m.node as Node3D).global_position.y + 0x200 / 8192.0, at.z),
			Vector3(at.x, -100.0, at.z))
		q.exclude = _exclude
		var hit := _space.intersect_ray(q)
		if hit:
			at.y = hit.position.y
	var node := m.node as Node3D
	node.global_position = at
	if not int(m.flags) & 0x1000 and dir != Vector2.ZERO:
		# level_layout.path_yaw_degrees: 90° − ratan2(dx, dz)
		node.rotation.y = deg_to_rad(90.0 - rad_to_deg(atan2(dir.x, dir.y)))
