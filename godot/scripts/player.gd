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
##
## The chase camera is the game's standard view (0x800569ec, preset 0 at 0x8009ad08):
## pitched down 29° (330/4096 turn) at a look point 2 units above the feet, from 8.57
## behind and 4.75 above that point (game units: look +0x4000, 1 Godot unit = 0x2000).
## Its yaw eases 1/32 per 60 Hz step towards the heading, capped at ~105°/s; the
## look point follows height changes with a small dead zone. Not modelled: the other
## 6 views, the fallback presets when the view is blocked, and the playerY/2 term
## in the game's camera height.
##
## Space transforms walker <-> hover (0x80055988, via player tick 0x800588d4):
## cockpit track 2 (f0-29) and legs track 12 (f330-359) play together over 1.37 s
## (progress +0xa0 per 300 Hz clock unit), forwards to hover, backwards to walk; the
## body rises 0.5 units with the progress. At the start: plain sound 0x15 and player
## effect 4 (Csfx table 1). Hover holds cockpit track 1 (f29) and legs track 13
## (f359); walker returns to track 0. Hover physics run from the start of either
## transform until the walker is back down: thrust with 5 % drag per 37.5 Hz step
## (steady 9.4 units/s, cap 10.7), and the positional loops 0x28 (idle) / 0x29 (moving
## or turning). Not modelled: the hover collision box, the idle braking ramp, the
## slow-walk/strafe modes.
extends CharacterBody3D

const EffectPlayer := preload("res://scripts/effect_player.gd")

const SPEED := 5.0
const TURN_SPEED := 2.5
const GRAVITY := 20.0
const CAM_LOOK_UP := 2.0
const CAM_UP := 4.75
const CAM_BACK := 8.57
const CAM_YAW_EASE := 1.0 / 32.0          # per 60 Hz step
const CAM_YAW_MAX := deg_to_rad(105.0)    # rad/s
const CAM_Y_DEAD_ZONE := 0.03             # 0x100 game units
# Walk cycle of the X1-Alpha legs: AnmD tracks 38→39→40→41 chain frames 11–39
# into a closed loop (next_track links, see tools/export_gltf.py). Track 0 is rest.
const WALK_CHAIN := ["track38_", "track39_", "track40_", "track41_"]
const REST_PREFIX := "track00_"
# movement -> [loop (positional), step (plain), stop (plain)] Cshd script ids
const SOUNDS := {"forward": [0x22, 0x38, 0x32], "back": [0x24, 0x3a, 0x34], "turn": [0x25, -1, 0x35]}
const HOVER_IDLE_SOUND := 0x28        # positional loops
const HOVER_MOVE_SOUND := 0x29
const TRANSFORM_SOUND := 0x15         # plain
const TRANSFORM_EFFECT := 4           # Csfx table 1
const TRANSFORM_TIME := 65536.0 / 0xa0 / 300.0   # 1.365 s
const HOVER_RAISE := 0.5              # 0x1000 game units
const HOVER_DRAG := 1.923             # 5 % per 37.5 Hz step, as a per-second rate
const HOVER_THRUST := 18.08           # units/s²: steady speed = thrust / drag = 9.4
const HOVER_MAX_SPEED := 10.7

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
var _cam: Camera3D
var _base := ""          # res://assets/<LEVEL>/
var _cockpit_anim: AnimationPlayer
var _x1: Node3D          # the visual assembly, raised in hover
var _hover := false      # hover physics (+0xad): from the start of a transform until landed
var _t := 0.0            # transform progress 0 (walker) .. 1 (hover)
var _t_dir := 0          # +1 to hover, -1 to walker, 0 idle
var _hover_motor: AudioStreamPlayer3D
var _effects := {}       # Csfx table 1 registry
var _cam_yaw := 0.0
var _cam_y := 0.0        # smoothed look-point height

func _ready() -> void:
	add_to_group("player")
	_load_sounds()
	_x1 = find_child("X1", true, false) as Node3D
	var cockpit := find_child("cockpit", true, false)
	if cockpit:
		var aps := cockpit.find_children("*", "AnimationPlayer", false, false)
		if aps.size() > 0:
			_cockpit_anim = aps[0]
	if _base != "":
		var f := FileAccess.open(_base + "csfx.json", FileAccess.READ)
		if f:
			_effects = EffectPlayer.build_registry([], JSON.parse_string(f.get_as_text()), 1).effects
	_cam = get_node_or_null("Camera") as Camera3D
	if _cam:
		_cam.top_level = true   # moves on its own, smoothed (see _update_camera)
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
	elif event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_SPACE:
		start_transform()

func _physics_process(delta: float) -> void:
	if not _grounded_at_start:
		_grounded_at_start = true
		_snap_to_ground()
		_cam_yaw = _heading()
		_cam_y = global_position.y
	if Input.is_action_pressed("move_left"):
		rotate_y(TURN_SPEED * delta)
	if Input.is_action_pressed("move_right"):
		rotate_y(-TURN_SPEED * delta)

	var dir := 0.0
	if Input.is_action_pressed("move_forward"):
		dir += 1.0
	if Input.is_action_pressed("move_back"):
		dir -= 1.0
	var turning := Input.is_action_pressed("move_left") != Input.is_action_pressed("move_right")
	if _hover:
		# Thrust along the heading, exponential drag, capped.
		var v := Vector3(velocity.x, 0.0, velocity.z) * exp(-HOVER_DRAG * delta)
		v += transform.basis.z * dir * HOVER_THRUST * delta
		v = v.limit_length(HOVER_MAX_SPEED)
		velocity.x = v.x
		velocity.z = v.z
	else:
		var horizontal := transform.basis.z * dir * SPEED
		velocity.x = horizontal.x
		velocity.z = horizontal.z
	velocity.y = 0.0 if is_on_floor() else velocity.y - GRAVITY * delta
	move_and_slide()
	_update_transform(delta)
	if _hover:
		_set_hover_sound(HOVER_MOVE_SOUND if dir != 0.0 or turning else HOVER_IDLE_SOUND)
		_update_camera(delta)
		return

	var moving := dir != 0.0
	_set_movement("forward" if dir > 0.0 else "back" if dir < 0.0 else "turn" if turning else "")
	if _legs_anim:
		if moving and not _moving and _walk.size() > 0:
			_step = 0
			_legs_anim.play(_walk[0])
		elif not moving and _moving and _rest != "":
			_legs_anim.play(_rest, 0.2)
	_moving = moving
	_update_camera(delta)


func _heading() -> float:
	var z := global_basis.z   # the X1 faces +Z
	return atan2(z.x, z.z)


func _update_camera(delta: float) -> void:
	if _cam == null:
		return
	var diff := wrapf(_heading() - _cam_yaw, -PI, PI)
	var ease_step := diff * (1.0 - pow(1.0 - CAM_YAW_EASE, delta * 60.0))
	_cam_yaw += clampf(ease_step, -CAM_YAW_MAX * delta, CAM_YAW_MAX * delta)
	var dy := global_position.y - _cam_y
	if absf(dy) > CAM_Y_DEAD_ZONE:
		_cam_y += (dy - signf(dy) * CAM_Y_DEAD_ZONE) * (1.0 - pow(0.85, delta * 60.0))
	var look := Vector3(global_position.x, _cam_y + CAM_LOOK_UP, global_position.z)
	var fwd := Vector3(sin(_cam_yaw), 0.0, cos(_cam_yaw))
	_cam.global_position = look - fwd * CAM_BACK + Vector3(0, CAM_UP, 0)
	_cam.look_at(look, Vector3.UP)

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
	_base = base
	var f := FileAccess.open(base + "shd.json", FileAccess.READ)
	var shd: Variant = JSON.parse_string(f.get_as_text()) if f else null
	if not shd is Dictionary:
		return
	# [script_id, positional]: positional ones are the motor loops.
	var wanted := [[HOVER_IDLE_SOUND, true], [HOVER_MOVE_SOUND, true], [TRANSFORM_SOUND, false]]
	for ids: Array in SOUNDS.values():
		wanted.append_array([[ids[0], true], [ids[1], false], [ids[2], false]])
	for t: Dictionary in shd.get("tables", []):
		for e: Dictionary in t.get("entries", []):
			var key := [int(e.script_id), bool(e.positional)]
			if _sounds.has(key) or not key in wanted or not e.get("tone_wav") \
					or not ResourceLoader.exists(base + e.tone_wav):
				continue
			var stream := (load(base + e.tone_wav) as AudioStreamWAV).duplicate() as AudioStreamWAV
			if key[1]:
				stream.loop_mode = AudioStreamWAV.LOOP_FORWARD
				stream.loop_begin = 0
				stream.loop_end = int(stream.get_length() * stream.mix_rate)
			_sounds[key] = stream
	_motor = AudioStreamPlayer3D.new()
	_motor.unit_size = 4.0
	_motor.volume_db = -4.0
	add_child(_motor)
	_hover_motor = AudioStreamPlayer3D.new()
	_hover_motor.unit_size = 4.0
	_hover_motor.volume_db = -4.0
	add_child(_hover_motor)
	_sfx = AudioStreamPlayer.new()
	_sfx.volume_db = -4.0
	_sfx.max_polyphony = 4
	add_child(_sfx)


func _play(script_id: int) -> void:
	var s: AudioStream = _sounds.get([script_id, false])
	if s and _sfx:
		_sfx.stream = s
		_sfx.play()


func _set_hover_sound(id: int) -> void:
	var s: AudioStream = _sounds.get([id, true])
	if _hover_motor and s and (_hover_motor.stream != s or not _hover_motor.playing):
		_hover_motor.stream = s
		_hover_motor.play()


# ─── Walker <-> hover transform ───────────────────────────────────────────────

func is_hover() -> bool:
	return _hover


func start_transform() -> void:
	if _t_dir != 0 or _cockpit_anim == null or _legs_anim == null:
		return
	var to_hover := _t <= 0.0
	_t_dir = 1 if to_hover else -1
	if to_hover:
		_hover = true
		_movement = ""      # the walker's motor loop just stops (no stop sound)
		if _motor:
			_motor.stop()
	_play(TRANSFORM_SOUND)
	if _effects.has(TRANSFORM_EFFECT):
		var fx := EffectPlayer.new()
		fx.setup(_effects[TRANSFORM_EFFECT], _effects, _base + "particles", false)
		add_child(fx)
		get_tree().create_timer(4.0).timeout.connect(fx.queue_free)
	_play_clip(_cockpit_anim, "track02_", to_hover)
	_play_clip(_legs_anim, "track12_", to_hover)


func _play_clip(ap: AnimationPlayer, prefix: String, forwards: bool) -> void:
	## Play a transform clip over TRANSFORM_TIME, forwards or backwards.
	var name := _anim_named(ap, prefix)
	if name == "":
		return
	var anim := ap.get_animation(name)
	anim.loop_mode = Animation.LOOP_NONE
	var speed := anim.length / TRANSFORM_TIME
	if forwards:
		ap.play(name, 0.0, speed)
	else:
		ap.play(name, 0.0, -speed, true)


func _anim_named(ap: AnimationPlayer, prefix: String) -> String:
	for name in ap.get_animation_list():
		if name.begins_with(prefix):
			return name
	return ""


func _update_transform(delta: float) -> void:
	if _t_dir == 0:
		return
	_t = clampf(_t + _t_dir * delta / TRANSFORM_TIME, 0.0, 1.0)
	if _x1:
		_x1.position.y = HOVER_RAISE * _t
	if _t >= 1.0 and _t_dir > 0:
		_t_dir = 0
		_cockpit_anim.play(_anim_named(_cockpit_anim, "track01_"))   # hover holds
		_legs_anim.play(_anim_named(_legs_anim, "track13_"))
	elif _t <= 0.0 and _t_dir < 0:
		_t_dir = 0
		_hover = false
		_cockpit_anim.play(_anim_named(_cockpit_anim, "track00_"))   # walker rest
		_legs_anim.play(_rest)
		_moving = false
		if _hover_motor:
			_hover_motor.stop()


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
