## The game's chase camera (SLUS_007.39 0x800569ec / placement 0x8005612c), driving the
## player's Camera3D. player.gd adds it and calls update() every physics frame.
##
## V cycles the 7 views (the game's Select). Each view is a list of presets (primary,
## then fallbacks) from the mode table at 0x8009aec4, with flags: zoom 0x100 (dolly 4.0
## forward, look offset halved), zoom 300 (4.69 forward), world-fixed yaw (Godot
## heading 180°), line-of-sight fallback. Presets (0x8009ad08) give a yaw offset, pitch
## and a look offset in the player's frame (x right, y up, z forward).
##   0 standard chase       1 zoomed chase       2 / 3 the same with a fixed yaw
##   4 higher and farther   5 / 6 low and close (only inside class-95 Trigger zones with
##                          bitfield 0x40, e.g. in LAX1, M2C, M3A, M3B, M4A1, OV; outside
##                          the view drops back to 0)
##
## Placement: camera height H(pitch) + Ys/2 (Ys = the smoothed player height), look point
## at Ys + offset.y; the distance is the height difference × cot(pitch). Pitch and yaw
## ease 1/32 per 150 Hz step, capped at 52.7°/s and 105.5°/s; the offset eases at
## 2.93 u/s; Ys follows a cubic spring (0x80056530). FOV is fixed (26.4°).
##
## Line of sight (0x80055ee8, views 0-4): terrain only, from the body to the unsmoothed
## target position. A first hit on a see-through polygon (surface bit 0x80,
## terrain_collision.gd) is checked back from the player; if that is see-through too the
## view is clear. Blocked (or on a fallback), every 0.4 s it retries the primary, then
## steps to the next fallback: the camera swings up and out, never closer.
##
## Transform cut-away (0x80056870): during a transform a random preset 9-16 with clear
## line of sight is held rigidly until the transform ends.
##
extends Node

const BAM := TAU / 4096.0
# preset -> [yaw offset (game BAM), pitch (BAM), look offset (Godot units)]
const PRESETS := {
	0: [0, 330, Vector3(0, 2.0, 0)], 1: [0, 530, Vector3(0, 0, 1.0)],
	2: [0, 930, Vector3(0, 2.0, 1.0)], 5: [0, 430, Vector3(0, 2.5, 0)],
	# transform cut-aways, with their own fixed height (0xb000 / 0x9000)
	9: [1536, 512, Vector3.ZERO, 5.5], 10: [2560, 512, Vector3.ZERO, 5.5],
	11: [3584, 512, Vector3.ZERO, 5.5], 12: [512, 512, Vector3.ZERO, 5.5],
	13: [512, 768, Vector3.ZERO, 4.5], 14: [1536, 768, Vector3.ZERO, 4.5],
	15: [2560, 768, Vector3.ZERO, 4.5], 16: [-512, 768, Vector3.ZERO, 4.5],
	18: [0, 255, Vector3(0, 2.75, 0)]}
const ZOOM_IN := 1
const FIXED_YAW := 2
const LOS := 4
const ZOOM_CLOSE := 8
const VIEWS := [[[0, 1, 2], LOS], [[0, 1, 2], LOS | ZOOM_IN], [[0, 1, 2], LOS | FIXED_YAW],
	[[0, 1, 2], LOS | FIXED_YAW | ZOOM_IN], [[5, 2], LOS], [[18], ZOOM_CLOSE],
	[[18], ZOOM_CLOSE | FIXED_YAW]]
const CLOSE_VIEWS := [5, 6]
const OFFSET_RATE := 80 * 300 / 8192.0      # u/s
const PITCH_CAP := 2 * 300.0                # BAM/s
const YAW_CAP := 4 * 300.0
const ZOOM_RATE := 300.0   # zoom units/s: +-1 per 300 Hz tick, no easing (0x800569ec)
const RETRY := 0x78 / 300.0
const BODY_UP := 0.6                        # line-of-sight start above the feet
const LOS_TOP := 4.0                        # 0x8000
const HOVER_LOOK_DROP := 0x1500 / 8192.0    # look point drop at full transform
const ZONE_RADIUS := 0.3                    # the player's radius in the zone test
const LABEL_TIME := 3.0

var view := 0
var _player: CharacterBody3D
var _cam: Camera3D
var _terrain: Node                          # terrain_collision.gd
var _zones: Array = []                      # [centre, half x, half z] of view-5/6 zones
var _exclude: Array[RID] = []          # bodies the line of sight ignores
var _pitch := 330.0
var _yaw := 0.0
var _off := Vector3(0, 2.0, 0)
var _zoom := 0.0
var _ys := 0.0
var _yv := 0.0                              # spring velocity (raw)
var _at := 0                                # list index in use (0 = primary)
var _try := 0                               # fallback being tried
var _retry := 0.0
var _cut := -1                              # cut-away preset; -1 none chosen, -2 none clear
var _label: Label
var _label_left := 0.0


func setup(player: CharacterBody3D, cam: Camera3D, zones: Array) -> void:
	name = "GameCamera"
	_player = player
	_cam = cam
	_zones = zones
	var layer := CanvasLayer.new()
	add_child(layer)
	_label = Label.new()
	_label.set_anchors_and_offsets_preset(Control.PRESET_CENTER_TOP)
	_label.offset_top = 64
	_label.grow_horizontal = Control.GROW_DIRECTION_BOTH
	_label.add_theme_font_size_override("font_size", 22)
	_label.add_theme_color_override("font_outline_color", Color.BLACK)
	_label.add_theme_constant_override("outline_size", 6)
	_label.visible = false
	layer.add_child(_label)


func reset(heading: float, y: float) -> void:
	_yaw = heading
	_ys = y
	_yv = 0.0
	_pitch = PRESETS[VIEWS[view][0][0]][1]
	_off = PRESETS[VIEWS[view][0][0]][2]
	_at = 0
	_retry = 0.0


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_V:
		view = (view + 1) % VIEWS.size()
		if view in CLOSE_VIEWS and not _in_zone():
			view = 0
		_at = 0
		_try = 0
		_retry = 0.0
		_label.text = "View %d" % view
		_label.visible = true
		_label_left = LABEL_TIME


static func height(pitch: float) -> float:
	## 0x800564a8: camera height for a pitch (BAM).
	var k := 1.0 - (pitch - 330.0) / 600.0
	return maxf(4.5, 6.75 + 16.0 * (1.0 - k * k))


func update(delta: float, heading: float, bc: float, transforming: bool) -> void:
	if _cam == null:
		return
	_label_left -= delta
	_label.visible = _label_left > 0.0
	if view in CLOSE_VIEWS and not _in_zone():
		view = 0
		_at = 0
	var mode: Array = VIEWS[view]
	var list: Array = mode[0]
	var flags: int = mode[1]
	_follow_height(delta)
	var zoom_t := 0x100 if flags & ZOOM_IN else (300 if flags & ZOOM_CLOSE else 0)
	if zoom_t == 0x100 and _zoom > 0x100:
		_zoom = 0x100   # the game clamps down to 0x100 rather than easing
	else:
		_zoom = move_toward(_zoom, zoom_t, ZOOM_RATE * delta)
	if flags & LOS:
		_check_line_of_sight(delta, list, flags, heading, bc)
	var p: Array = PRESETS[list[mini(_at, list.size() - 1)]]
	var steps := delta * 150.0
	var k := 1.0 - pow(31.0 / 32.0, steps)
	_pitch += clampf((p[1] - _pitch) * k, -PITCH_CAP * delta, PITCH_CAP * delta)
	var yaw_t := _target_yaw(p, flags, heading)
	_yaw += clampf(wrapf(yaw_t - _yaw, -PI, PI) * k, -YAW_CAP * BAM * delta, YAW_CAP * BAM * delta)
	_off = _off.move_toward(p[2], OFFSET_RATE * delta)
	var xf := _place(_yaw, _pitch, _off, _zoom, heading, bc)
	if transforming:
		if _cut == -1:
			_cut = _pick_cutaway(heading, bc)
		if _cut >= 0:
			var c: Array = PRESETS[_cut]
			xf = _place(heading - c[0] * BAM, c[1], c[2], 0.0, heading, bc, c[3])
	else:
		_cut = -1
	_cam.global_transform = xf


func _target_yaw(p: Array, flags: int, heading: float) -> float:
	# A game yaw offset +o turns the other way in Godot (the map's x/z are the game's z/x).
	return (PI if flags & FIXED_YAW else heading) - p[0] * BAM


func _follow_height(delta: float) -> void:
	## 0x80056530 in raw units (0x2000 = 1): v += d³/2^26 per 150 Hz step (v resets
	## when it points away), moves (v >> 14) per tick, under 0x100 dropped, no overshoot.
	var d := (_player.global_position.y - _ys) * 8192.0
	var c := d * d * d / 67108864.0
	if signf(c) != signf(_yv):
		_yv = 0.0
	_yv += c * delta * 150.0
	var step := floorf(_yv / 16384.0) * delta * 300.0
	if absf(step) < 0x100:
		step = 0.0
	if absf(step) > absf(d):
		step = d
	_ys += step / 8192.0


func _place(yaw: float, pitch: float, off: Vector3, zoom: float, heading: float, bc: float, h := -1.0) -> Transform3D:
	## h: a preset's own height (cut-aways); otherwise H(pitch).
	var s := (256.0 - zoom / 2.0) / 256.0
	var o := off * s
	var fwd_p := Vector3(sin(heading), 0.0, cos(heading))
	var right_p := Vector3(-fwd_p.z, 0.0, fwd_p.x)
	var pos := _player.global_position
	var look := Vector3(pos.x, _ys + o.y - HOVER_LOOK_DROP * bc, pos.z) + fwd_p * o.z + right_p * o.x
	var cam_y := (h if h >= 0.0 else height(pitch)) + _ys / 2.0
	var pr := pitch * BAM
	var d := absf(cam_y - look.y) / tan(pr)
	var at := Vector3(look.x - d * sin(yaw), cam_y, look.z - d * cos(yaw))
	var fwd := Vector3(sin(yaw) * cos(pr), -sin(pr), cos(yaw) * cos(pr))
	at += fwd * (zoom * 128.0 / 8192.0)
	return Transform3D(Basis.looking_at(fwd, Vector3.UP), at)


func _candidate(preset: int, flags: int, heading: float, bc: float) -> Vector3:
	var p: Array = PRESETS[preset]
	return _place(_target_yaw(p, flags, heading), p[1], p[2], _zoom, heading, bc).origin


func _check_line_of_sight(delta: float, list: Array, flags: int, heading: float, bc: float) -> void:
	var clear := _clear(_candidate(list[mini(_at, list.size() - 1)], flags, heading, bc))
	if clear and _at == 0:
		_retry = 0.0
		return
	_retry += delta
	if _retry <= RETRY:
		return
	_retry = 0.0
	if _at != 0 and _clear(_candidate(list[0], flags, heading, bc)):
		_at = 0
		_try = 0
		return
	if not clear:
		_try = _try + 1 if _try + 1 < list.size() else mini(1, list.size() - 1)
		if _clear(_candidate(list[_try], flags, heading, bc)):
			_at = _try
		else:
			_retry = RETRY   # next fallback on the next frame


func _pick_cutaway(heading: float, bc: float) -> int:
	var first := randi_range(9, 16)
	for i in 8:
		var c := 9 + (first - 9 + i) % 8
		var p: Array = PRESETS[c]
		if _clear(_place(heading - p[0] * BAM, p[1], p[2], 0.0, heading, bc, p[3]).origin):
			return c
	return -2


func _clear(cam: Vector3) -> bool:
	## 0x80055ee8: terrain line of sight between the body and a camera position.
	var from := _player.global_position + Vector3(0, BODY_UP, 0)
	var to := cam
	if from.y < LOS_TOP:
		for i in 8:
			if (from.y + to.y) / 2.0 <= LOS_TOP:
				break
			to = (from + to) / 2.0
	var hit := _ray(to, from)
	if hit.is_empty():
		return true
	if not _see_through(hit):
		return false
	var back := _ray(from, hit.position)
	return back.is_empty() or _see_through(back)


func _see_through(hit: Dictionary) -> bool:
	return _terrain != null and _terrain.is_see_through(int(hit.get("face_index", -1)))


func _ray(a: Vector3, b: Vector3) -> Dictionary:
	## First TerrainCollision hit from a to b; other bodies (props, elevators, the
	## player) are skipped and remembered.
	if _terrain == null and _player.owner:
		_terrain = _player.owner.get_node_or_null("Terrain")
	var space := _player.get_world_3d().direct_space_state
	for i in 8:
		var q := PhysicsRayQueryParameters3D.create(a, b)
		q.exclude = _exclude
		q.hit_back_faces = true
		var hit := space.intersect_ray(q)
		if hit.is_empty() or (hit.collider as Node).name == "TerrainCollision":
			return hit
		_exclude.append(hit.rid)
	return {}


func _in_zone() -> bool:
	var p := _player.global_position
	for z: Array in _zones:
		if absf(p.x - z[0].x) - ZONE_RADIUS <= z[1] and absf(p.z - z[0].z) - ZONE_RADIUS <= z[2]:
			return true
	return false
