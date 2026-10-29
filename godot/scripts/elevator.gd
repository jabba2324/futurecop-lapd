## A class-10 Elevator, moving as in SLUS_007.39 (init 0x80041488, update 0x80041880,
## messages 0x80041728). level_ambience.gd adds one as a child of each Elevator actor
## node; it moves that node, whose prop collision becomes an AnimatableBody3D so the
## walker rides it.
##   - stop heights: ground under the actor + height_offset_i (fx12 << 4, absolute)
##   - starts at stop starting_position-1, heading up (down if that is the top stop);
##     ping-pongs 0, 1, (2), 1, 0 and waits at every stop, ends included
##   - speed (raw << 4) / 300 fx16 per 300 Hz tick, up and down separately
##   - stop_time raw * 256 / 218 ticks (about raw/256 s); 0 = until a player is on it
##   - trigger_type bits: 1 player-activated (leaves as soon as a player stands on
##     it), 2 halted until script message 83, 4 moves only on script message 82.
##     Nothing here sends script messages, so types 2, 3 and 4 stay where they start.
##   - ambient_sound loops while moving; end_sound plays once on arrival
##   - tile_effect is the surface byte of its top (player.gd reads the "tile_effect"
##     meta of the body it stands on)
## Not modelled: the anti-crush pause (message 0xb).
extends Node

const TICKS := 300.0
const PLAYER_GAP := 0.1     # the player counts as on it within this of its top

var _actor: Node3D
var _body: AnimatableBody3D
var _stops: Array[float] = []
var _waits: Array[float] = []   # seconds; 0 = until a player is on it
var _up := 0.0                  # Godot units / s
var _down := 0.0
var _type := 0
var _at := 0                    # current (or last) stop
var _dir := 1
var _target := -1               # stop it's moving to, -1 while waiting
var _timer := 0.0
var _top := 0.0                 # body top above the actor origin
var _box := AABB()              # collision bounds, actor-local
var _loop: AudioStreamPlayer3D
var _end: AudioStreamPlayer3D
var _player: Node3D


static func _raw(rec: Dictionary, name: String) -> int:
	return int(rec.fields.get("Elevator." + name, {}).get("raw", 0))


func setup(rec: Dictionary, actor: Node3D, ground: float, loop_sound: AudioStream, end_sound: AudioStream) -> void:
	name = "ElevatorMotion"
	_actor = actor
	var n := clampi(_raw(rec, "num_stops"), 1, 3)
	for i in n:
		var off := _raw(rec, "height_offset_%d" % i)
		if off >= 0x8000:
			off -= 0x10000
		_stops.append(ground + off / 512.0)
		_waits.append(int(_raw(rec, "stop_time_%d" % i) * 256 / 218) / TICKS)
	_up = int(_raw(rec, "up_speed") * 16 / 300) * TICKS / 8192.0
	_down = int(_raw(rec, "down_speed") * 16 / 300) * TICKS / 8192.0
	_type = _raw(rec, "trigger_type")
	var sp := _raw(rec, "starting_position")
	_at = clampi(sp - 1, 0, n - 1)
	_dir = -1 if sp >= n else 1
	_actor.position.y = _stops[_at]
	_timer = _waits[_at]
	_make_body(_raw(rec, "tile_effect"))
	if loop_sound:
		_loop = _sound(loop_sound)
		_loop.finished.connect(func() -> void: if _target >= 0: _loop.play())
	if end_sound:
		_end = _sound(end_sound)


func _sound(stream: AudioStream) -> AudioStreamPlayer3D:
	var s := AudioStreamPlayer3D.new()
	s.stream = stream
	s.unit_size = 3.0
	s.max_distance = 17.0   # the game's actor sounds fade out at about this range
	s.volume_db = -4.0
	_actor.add_child(s)
	return s


func _make_body(tile_effect: int) -> void:
	## Swap the prop's StaticBody3D (terrain_collision.gd) for a moving body.
	_body = AnimatableBody3D.new()
	_body.name = "Collision"
	_body.sync_to_physics = true
	_body.set_meta("tile_effect", tile_effect)
	for old: Node in _actor.get_children():
		if old is StaticBody3D:
			for cs: Node in old.get_children():
				if cs is CollisionShape3D:
					old.remove_child(cs)
					_body.add_child(cs)
					var b: AABB = (cs as CollisionShape3D).transform * (cs as CollisionShape3D).shape.get_debug_mesh().get_aabb()
					_box = b if _box.size == Vector3.ZERO else _box.merge(b)
			_actor.remove_child(old)
			old.queue_free()
	# Top-level and moved directly: an AnimatableBody3D only syncs to physics (and
	# hands its velocity to the rider) when its own transform is set.
	_body.top_level = true
	_actor.add_child(_body)
	_body.global_transform = _actor.global_transform
	_top = _box.end.y


func _physics_process(delta: float) -> void:
	if _type & 6:
		return   # waits for a script message
	if _target < 0:
		var on := _player_on()
		var go := on if _type & 1 or _waits[_at] == 0.0 else false
		if not go and not (_type & 1) and _waits[_at] > 0.0:
			_timer -= delta
			go = _timer <= 0.0
		if go:
			_leave()
		return
	var y := _actor.position.y
	var goal := _stops[_target]
	var step := (_up if goal > y else _down) * delta
	if absf(goal - y) <= step:
		_set_y(goal)
		_arrive()
	else:
		_set_y(y + signf(goal - y) * step)


func _set_y(y: float) -> void:
	_actor.position.y = y
	_body.global_transform = _actor.global_transform


func _leave() -> void:
	if _stops.size() < 2:
		return
	if _at + _dir < 0 or _at + _dir >= _stops.size():
		_dir = -_dir
	_target = _at + _dir
	if _loop and not _loop.playing:
		_loop.play()


func _arrive() -> void:
	_at = _target
	_target = -1
	_timer = _waits[_at]
	if _loop:
		_loop.stop()
	if _end:
		_end.play()


func _player_on() -> bool:
	if _player == null:
		_player = get_tree().get_first_node_in_group("player") as Node3D
		if _player == null:
			return false
	var p := _actor.global_transform.affine_inverse() * _player.global_position
	return p.x >= _box.position.x and p.x <= _box.end.x and p.z >= _box.position.z \
		and p.z <= _box.end.z and absf(p.y - _top) <= PLAYER_GAP
