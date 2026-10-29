## The walkable X1-Alpha: a CharacterBody3D that follows the terrain collision
## built by terrain_collision.gd. W/S walks along +Z (the X1's forward, see
## level_layout.assemble_x1), A/D turns, Esc returns to the level-select menu.
##
## Movement sounds follow the game's walker (SLUS_007.39 0x800549f0 → 0x80047088):
## legs AnmD frame events pick a motor loop per movement (a positional Cshd sound,
## attached to the mech), play a step hit when the event repeats the running loop,
## and play the loop's stop sound when the movement changes or ends.
##   forward: loop 0x22, steps 0x38 at the starts of tracks 39 and 41, stop 0x32
##   back:    loop 0x24, steps 0x3a, stop 0x34   (the game uses tracks 44/45; we reuse 38-41)
##   turning: loop 0x25, stop 0x35               (no step: our turn doesn't animate the legs)
## Ids are Cshd script ids; the global table (bank 1) is the same in every level.
extends CharacterBody3D

const SPEED := 5.0
const TURN_SPEED := 2.5
const GRAVITY := 20.0
# Walk cycle of the X1-Alpha legs: AnmD tracks 38→39→40→41 chain frames 11–39
# into a closed loop (next_track links, see tools/export_gltf.py). Track 0 is rest.
const WALK_CHAIN := ["track38_", "track39_", "track40_", "track41_"]
const REST_PREFIX := "track00_"
# movement -> [loop (positional), step (plain), stop (plain)] Cshd script ids
const SOUNDS := {"forward": [0x22, 0x38, 0x32], "back": [0x24, 0x3a, 0x34], "turn": [0x25, -1, 0x35]}

var _legs_anim: AnimationPlayer
var _walk: PackedStringArray = []
var _rest := ""
var _step := 0
var _moving := false
var _grounded_at_start := false
var _sounds := {}        # [script_id, positional] -> AudioStream
var _motor: AudioStreamPlayer3D
var _sfx: AudioStreamPlayer
var _movement := ""      # running motor loop: "", "forward", "back" or "turn"

func _ready() -> void:
	add_to_group("player")
	_load_sounds()
	floor_snap_length = 0.3
	floor_max_angle = deg_to_rad(50.0)
	# Legs are the "legs" part of the X1 assembly built by level_layout.gd.
	var legs := find_child("legs", true, false)
	if legs:
		# Direct children only: the cockpit (and its AnimationPlayer) is mounted inside the legs.
		var players := legs.find_children("*", "AnimationPlayer", false, false)
		if players.size() > 0:
			_legs_anim = players[0]
	if _legs_anim == null:
		return
	for prefix in WALK_CHAIN:
		var name := _find_anim(prefix)
		if name != "":
			_walk.append(name)
	_rest = _find_anim(REST_PREFIX)
	for name in _walk:
		_legs_anim.get_animation(name).loop_mode = Animation.LOOP_NONE
	_legs_anim.animation_finished.connect(_on_clip_finished)
	if _rest != "":
		_legs_anim.play(_rest)

func _find_anim(prefix: String) -> String:
	for name in _legs_anim.get_animation_list():
		if name.begins_with(prefix):
			return name
	return ""

func _snap_to_ground() -> void:
	# The layout's heightmap estimate can be off by a few units; put the feet on
	# the real collision surface under the spawn point (ignoring roofs/bridges
	# more than 5 units above the estimate).
	var space := get_world_3d().direct_space_state
	var q := PhysicsRayQueryParameters3D.create(global_position + Vector3(0, 5, 0),
		global_position + Vector3(0, -20, 0))
	q.exclude = [get_rid()]
	var hit := space.intersect_ray(q)
	if hit:
		global_position.y = hit.position.y + 0.05

func _unhandled_input(event: InputEvent) -> void:
	# Esc goes back to the level-select menu.
	if event.is_action_pressed("ui_cancel") and ResourceLoader.exists("res://scenes/menu.tscn"):
		get_tree().change_scene_to_file("res://scenes/menu.tscn")

func _physics_process(delta: float) -> void:
	if not _grounded_at_start:
		_grounded_at_start = true
		_snap_to_ground()
	if Input.is_action_pressed("move_left"):
		rotate_y(TURN_SPEED * delta)
	if Input.is_action_pressed("move_right"):
		rotate_y(-TURN_SPEED * delta)

	var dir := 0.0
	if Input.is_action_pressed("move_forward"):
		dir += 1.0
	if Input.is_action_pressed("move_back"):
		dir -= 1.0
	var horizontal := transform.basis.z * dir * SPEED
	velocity.x = horizontal.x
	velocity.z = horizontal.z
	velocity.y = 0.0 if is_on_floor() else velocity.y - GRAVITY * delta
	move_and_slide()

	var moving := dir != 0.0
	var turning := Input.is_action_pressed("move_left") != Input.is_action_pressed("move_right")
	_set_movement("forward" if dir > 0.0 else "back" if dir < 0.0 else "turn" if turning else "")
	if _legs_anim:
		if moving and not _moving and _walk.size() > 0:
			_step = 0
			_legs_anim.play(_walk[0])
		elif not moving and _moving and _rest != "":
			_legs_anim.play(_rest, 0.2)
	_moving = moving

func _on_clip_finished(_name: StringName) -> void:
	# Advance along the walk chain while moving; the chain loops back to its start.
	if _moving and _walk.size() > 0:
		_step = (_step + 1) % _walk.size()
		_legs_anim.play(_walk[_step])
		if _step % 2 == 1 and _movement in ["forward", "back"]:   # tracks 39 and 41
			_play(SOUNDS[_movement][1])


# ─── Sounds ───────────────────────────────────────────────────────────────────

func _load_sounds() -> void:
	var root: Node = self   # the level scene's root is named after the level
	while root and not root.scene_file_path.ends_with("_level.tscn"):
		root = root.get_parent()
	if root == null:
		return
	var base := "res://assets/%s/" % root.name
	var f := FileAccess.open(base + "shd.json", FileAccess.READ)
	var shd: Variant = JSON.parse_string(f.get_as_text()) if f else null
	if not shd is Dictionary:
		return
	for t: Dictionary in shd.get("tables", []):
		for e: Dictionary in t.get("entries", []):
			var key := [int(e.script_id), bool(e.positional)]
			if _sounds.has(key) or not e.get("tone_wav") or not ResourceLoader.exists(base + e.tone_wav):
				continue
			for ids: Array in SOUNDS.values():
				if key in [[ids[0], true], [ids[1], false], [ids[2], false]]:
					var stream := (load(base + e.tone_wav) as AudioStreamWAV).duplicate() as AudioStreamWAV
					if key[1]:   # motor loops
						stream.loop_mode = AudioStreamWAV.LOOP_FORWARD
						stream.loop_begin = 0
						stream.loop_end = int(stream.get_length() * stream.mix_rate)
					_sounds[key] = stream
	_motor = AudioStreamPlayer3D.new()
	_motor.unit_size = 4.0
	_motor.volume_db = -4.0
	add_child(_motor)
	_sfx = AudioStreamPlayer.new()
	_sfx.volume_db = -4.0
	_sfx.max_polyphony = 4
	add_child(_sfx)


func _play(script_id: int) -> void:
	var s: AudioStream = _sounds.get([script_id, false])
	if s and _sfx:
		_sfx.stream = s
		_sfx.play()


func _set_movement(m: String) -> void:
	if m == _movement or _motor == null:
		return
	if _movement != "":
		_play(SOUNDS[_movement][2])
		_motor.stop()
	_movement = m
	if m != "":
		_motor.stream = _sounds.get([SOUNDS[m][0], true])
		if _motor.stream:
			_motor.play()
