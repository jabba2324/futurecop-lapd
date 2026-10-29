## The walkable X1-Alpha: a CharacterBody3D that follows the terrain collision built by
## terrain_collision.gd, with the game's movement physics, leg animations and sounds,
## reverse-engineered from SLUS_007.39. Shared by every generated level scene.
##
## Keys (game default in brackets): W/S forward/back [Up/Down], A/D turn [Left/Right],
## Q/E strafe [L2/R2], Shift slow walk [L1], Space jump / hover brake [Triangle],
## T transform walker <-> hover [L1+R1], Esc back to the menu.
##
## Physics run in the game's own 37.5 Hz steps (8 clock units of 300 Hz), each doing
## vel += command − vel·drag; constants are game values converted to Godot units
## (1 unit = 0x2000; a game velocity V moves V/256 per step, see _G).
##   Walker (step 0x800549f0, forces 0x80054590): drag 37.5 %/step; commands run
##     0x18000 (4.7 u/s), slow 0xA000 (1.95), strafe 0x10000 (3.1), slow strafe 0xA000;
##     diagonals ×0.707 per axis. Turn 0x20/step (1.84 rad/s) × (1 − speed/2^20).
##     Slow walking never steps off a drop of 0.1 or more.
##   Jump (Space, on the ground, not slow, floor slope ≤ 38.7°): a probe 1 unit ahead
##     picks standing (ledge ≤ 0.34: tracks 17 → 58 launch → 59 → 60, vy 0x58000 plus a
##     0x40000 push), ledge climb (4 → 27 launch, vy 0x78000, forward push 0x1CCCC after
##     10 steps unless the ledge is above 1.1) or, while running, a running jump (54 or 55
##     by stride phase → hold 56/57, vy 0x64000, +0x18000 forward, lands back into the
##     run cycle at 40/38). Air: gravity 0x9800/step, terminal 0xC0000, 2.5 % drag, no
##     turning. Landing settles with track 5. Sounds: loop 0x26 during the wind-up, plain
##     0x36 at launch, loop 0x27 while settling, plain 0x37 at its end.
##   Hover (0x80053a74): drag 5 %/step (15 % braking with Space, 37.5 % against a wall;
##     after ~6 s without move input it ramps to 37.5 % over 0.85 s), commands 0x6666
##     (9.4 u/s), slow 0x3333, strafe 0x2000, slow strafe 0x1000, cap 10.7 u/s. The turn
##     rate builds up to 0x60 (2.76 rad/s) over ~0.5 s and resets on release.
##   Slopes (slide terms in 0x80054590 / 0x800537d0): steepest climbable slope 46° running,
##     41° strafing, 37° slow, 32° hover, 28° slow hover; step-up 0.46 walker, 0.96 hover.
##   Collision box (legs 3DBB, 0x800518a8): walker 0.52 × 0.64 (w × l), hover 0.64 × 1.32.
##
## Movement sounds follow 0x80047088: each gait has a positional motor loop, a step hit
## when its stride starts again, and a stop sound when it ends (see GAITS). Hover loops:
## 0x28 idle, 0x29 moving/turning, 0x2A braking. Ids are Cshd script ids, matched with
## the positional flag; the global table (bank 1) is the same in every level.
##
## Transform (0x80055988): cockpit track 2 (f0-29) and legs track 12 (f330-359) play
## together over 1.37 s, forwards to hover, backwards to walk; the body rises 0.5. At the
## start: plain sound 0x15 and player effect 4 (Csfx table 1). Hover holds cockpit
## track 1 and legs track 13. Hover physics run from the start of either transform
## until the walker is back down.
##
## The chase camera is the game's standard view (0x800569ec, preset 0 at 0x8009ad08):
## pitched down 29° at a look point 2 units above the feet, from 8.57 behind and 4.75
## above it. Yaw eases 1/32 per 60 Hz step, capped at ~105°/s; the look point follows
## height with a small dead zone.
##
## Not modelled: surface types (water, conveyors, damage floors), hard-landing damage,
## the crouch (Shift+Space), the game's wall push-back/heading deflection (Godot slides
## along walls instead), aiming, and the other camera views.
extends CharacterBody3D

const EffectPlayer := preload("res://scripts/effect_player.gd")

const STEP := 1.0 / 37.5                    # one game physics step
const _G := 37.5 / 256.0 / 8192.0           # game velocity units -> Godot units/s

# Walker
const WALK_DRAG := 0x6000 / 65536.0
const RUN_CMD := 0x18000 * _G
const SLOW_CMD := 0xA000 * _G
const STRAFE_CMD := 0x10000 * _G
const SLOW_STRAFE_CMD := 0xA000 * _G
const WALK_TURN := 0x20 * TAU / 4096.0      # rad per step
const SLOW_DROP := 0x333 / 8192.0           # slow walk won't step down this far
# Air
const GRAVITY := 0x9800 * _G                # Godot units/s lost per step
const TERMINAL := 0xC0000 * _G
const AIR_DRAG := 0x666 / 65536.0
const JUMP_MAX_SLOPE := asin(0xA000 / 65536.0)
const STANDING_LEDGE := 0xAE1 / 8192.0
const CLIMB_PUSH_MAX := 0x2333 / 8192.0
const HARD_LANDING := 0x50000 * _G
# Hover
const HOVER_DRAG := 0xCCC / 65536.0
const HOVER_BRAKE_DRAG := 0x2666 / 65536.0
const HOVER_WALL_DRAG := 0x6000 / 65536.0
const HOVER_CMD := 0x6666 * _G
const HOVER_SLOW_CMD := 0x3333 * _G
const HOVER_STRAFE_CMD := 0x2000 * _G
const HOVER_SLOW_STRAFE_CMD := 0x1000 * _G
const HOVER_MAX_SPEED := 0x92147 * _G
const HOVER_TURN_MAX := 0x60                # turn rate units; heading += rate/2 per step
const HOVER_TURN_RAMP := 0x60 / 0.5         # rate units per second
const IDLE_BRAKE_START := 0x700             # clock units (300 Hz) without move input
const IDLE_BRAKE_FULL := 0x800
const HOVER_RAISE := 0.5                    # 0x1000
# Slopes (steepest climbable) and step-up
const SLOPE := {"run": 46.0, "strafe": 41.0, "slow": 37.0, "hover": 32.0, "hover_slow": 28.0}
const STEP_UP := {false: 0.46, true: 0.96}
# Collision boxes (w, h, l) from the legs 3DBB
const WALKER_BOX := Vector3(0.52, 0.9, 0.64)
const HOVER_BOX := Vector3(0.64, 0.7, 1.32)
# Transform
const TRANSFORM_SOUND := 0x15               # plain
const TRANSFORM_EFFECT := 4                 # Csfx table 1
const TRANSFORM_TIME := 65536.0 / 0xa0 / 300.0   # 1.365 s
# Camera
const CAM_LOOK_UP := 2.0
const CAM_UP := 4.75
const CAM_BACK := 8.57
const CAM_YAW_EASE := 1.0 / 32.0            # per 60 Hz step
const CAM_YAW_MAX := deg_to_rad(105.0)      # rad/s
const CAM_Y_DEAD_ZONE := 0.03               # 0x100 game units

# Gaits: legs AnmD tracks and sounds (Cshd script ids). start → cycle (looping); "step"
# plays on entering a step_on track; "restart" re-triggers the loop there; stop_track
# plays when the gait ends.
const GAITS := {
	"run":   {"start": 8, "cycle": [38, 39, 40, 41], "loop": 0x22, "step": 0x38, "step_on": [39, 41], "stop": 0x32},
	"back":  {"start": 9, "cycle": [44, 45], "loop": 0x24, "step": 0x3a, "step_on": [44, 45], "stop": 0x34},
	"slow":  {"start": 6, "cycle": [30, 31], "loop": 0x25, "step": 0x3b, "step_on": [30, 31], "stop": 0x35},
	"slow_back": {"start": 7, "cycle": [34, 35], "loop": 0x25, "step": 0x3b, "step_on": [34, 35], "stop": 0x35},
	"strafe_l": {"start": 10, "cycle": [49, 50], "loop": 0x23, "restart": [50], "step": 0x33, "step_on": [49], "stop": 0x33, "stop_track": 48},
	"strafe_r": {"start": 11, "cycle": [52, 53], "loop": 0x23, "restart": [53], "step": 0x33, "step_on": [52], "stop": 0x33, "stop_track": 51},
	"slow_strafe_l": {"start": 1, "cycle": [20, 21], "loop": 0x23, "restart": [21], "step": 0x33, "step_on": [20], "stop": 0x33, "stop_track": 19},
	"slow_strafe_r": {"start": 2, "cycle": [23, 24], "loop": 0x23, "restart": [24], "step": 0x33, "step_on": [23], "stop": 0x33, "stop_track": 22},
	"turn_l": {"start": 14, "cycle": [14], "loop": 0x25, "step": 0x3b, "step_on": [14], "stop": 0x35},
	"turn_r": {"start": 15, "cycle": [15], "loop": 0x25, "step": 0x3b, "step_on": [15], "stop": 0x35},
}
const JUMP_WINDUP_LOOP := 0x26
const JUMP_LAUNCH_SOUND := 0x36
const LAND_LOOP := 0x27
const LAND_END_SOUND := 0x37
const HOVER_LOOPS := {"idle": 0x28, "move": 0x29, "brake": 0x2a}

var _legs_anim: AnimationPlayer
var _cockpit_anim: AnimationPlayer
var _tracks := {}           # legs track number -> animation name
var _x1: Node3D             # the visual assembly, raised in hover
var _shape: CollisionShape3D
var _cam: Camera3D
var _base := ""             # res://assets/<LEVEL>/
var _effects := {}          # Csfx table 1 registry
var _sounds := {}           # [script_id, positional] -> AudioStream
var _motor: AudioStreamPlayer3D
var _hover_motor: AudioStreamPlayer3D
var _sfx: AudioStreamPlayer

var _started := false
var _acc := 0.0             # physics step accumulator
var _hover := false         # hover physics (+0xad): from the start of a transform until landed
var _t := 0.0               # transform progress 0 (walker) .. 1 (hover)
var _t_dir := 0             # +1 to hover, -1 to walker, 0 idle
var _gait := ""             # current walker gait ("" = rest)
var _last_gait := ""        # the last gait started (a running jump needs "run")
var _cycle_i := 0
var _jump := {}             # {kind, phase: windup/air/land, steps, ledge, hold}
var _air_steps := 0
var _fall_speed := 0.0
var _hover_turn := 0.0      # hover turn rate (game units, signed)
var _idle_clock := 0.0      # hover: clock units without move input
var _cam_yaw := 0.0
var _cam_y := 0.0           # smoothed look-point height


func _ready() -> void:
	add_to_group("player")
	_ensure_actions()
	_load_sounds()
	_x1 = find_child("X1", true, false) as Node3D
	_shape = get_node_or_null("Collision") as CollisionShape3D
	_set_box(WALKER_BOX)
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
	floor_snap_length = STEP_UP[false]
	floor_max_angle = deg_to_rad(SLOPE.run)
	# Legs are the "legs" part of the X1 assembly built by level_layout.gd. Direct
	# children only: the cockpit (and its AnimationPlayer) is mounted inside the legs.
	var legs := find_child("legs", true, false)
	if legs:
		var players := legs.find_children("*", "AnimationPlayer", false, false)
		if players.size() > 0:
			_legs_anim = players[0]
	if _legs_anim == null:
		return
	for name in _legs_anim.get_animation_list():
		if name.begins_with("track"):
			_tracks[int(name.substr(5, 2))] = name
			_legs_anim.get_animation(name).loop_mode = Animation.LOOP_NONE
	# Tracks with the same frame range share one clip (export_gltf.py lists the others
	# in its extras.duplicate_tracks), e.g. landing track 5 = track 3 (f212-225).
	_map_duplicate_tracks(legs.scene_file_path)
	_legs_anim.animation_finished.connect(_on_legs_clip_finished)
	_play_track(0)


func _map_duplicate_tracks(glb_path: String) -> void:
	var f := FileAccess.open(glb_path, FileAccess.READ)
	if f == null or f.get_length() < 20:
		return
	f.seek(12)
	var json_len := f.get_32()
	f.seek(20)
	var j: Variant = JSON.parse_string(f.get_buffer(json_len).get_string_from_utf8())
	if not j is Dictionary:
		return
	for anim: Dictionary in j.get("animations", []):
		var ex: Dictionary = anim.get("extras", {})
		if ex.has("anmd_track") and _tracks.has(int(ex.anmd_track)):
			for k in ex.get("duplicate_tracks", []):
				_tracks[int(k)] = _tracks[int(ex.anmd_track)]


func _ensure_actions() -> void:
	## Keys for the extra moves, added at run time so project.godot needs no edits.
	for a: Array in [["strafe_left", KEY_Q], ["strafe_right", KEY_E], ["slow_walk", KEY_SHIFT],
			["jump", KEY_SPACE], ["transform", KEY_T]]:
		if not InputMap.has_action(a[0]):
			InputMap.add_action(a[0])
			var ev := InputEventKey.new()
			ev.keycode = a[1]
			InputMap.action_add_event(a[0], ev)


func _set_box(size: Vector3) -> void:
	if _shape == null:
		return
	var box := BoxShape3D.new()
	box.size = size
	_shape.shape = box
	_shape.position = Vector3(0, size.y / 2.0, 0)


func _snap_to_ground() -> void:
	# The layout's heightmap estimate can be off by a few units; put the feet on
	# the real collision surface under the spawn point (ignoring roofs/bridges
	# more than 5 units above the estimate).
	var hit := _ray_down(global_position, 5.0, 20.0)
	if hit:
		global_position.y = hit.position.y + 0.05


func _ray_down(at: Vector3, up: float, down: float) -> Dictionary:
	var q := PhysicsRayQueryParameters3D.create(at + Vector3(0, up, 0), at - Vector3(0, down, 0))
	q.exclude = [get_rid()]
	return get_world_3d().direct_space_state.intersect_ray(q)


func _unhandled_input(event: InputEvent) -> void:
	# Esc goes back to the level-select menu.
	if event.is_action_pressed("ui_cancel") and ResourceLoader.exists("res://scenes/menu.tscn"):
		get_tree().change_scene_to_file("res://scenes/menu.tscn")
	elif event.is_action_pressed("transform"):
		start_transform()
	elif event.is_action_pressed("jump") and not _hover:
		_try_jump()


# ─── Physics ──────────────────────────────────────────────────────────────────

func _physics_process(delta: float) -> void:
	if not _started:
		_started = true
		_snap_to_ground()
		_cam_yaw = _heading()
		_cam_y = global_position.y
	_acc += delta
	while _acc >= STEP:
		_acc -= STEP
		if _hover:
			_hover_step()
		else:
			_walker_step()
	_update_slope_limit()
	move_and_slide()
	_step_up(delta)
	_update_transform(delta)
	_update_landing()
	if _hover:
		_update_hover_sound()
	else:
		_update_gait()
	_update_camera(delta)


func _inputs() -> Vector2:
	## (forward, left) in -1..1, diagonals ×0.707 per axis as in the game.
	var f := Input.get_axis("move_back", "move_forward")
	var s := Input.get_axis("strafe_right", "strafe_left")
	if f != 0.0 and s != 0.0:
		f *= 0.7071
		s *= 0.7071
	return Vector2(f, s)


func _turn_input() -> float:
	return Input.get_axis("move_right", "move_left")


func _walker_step() -> void:
	var airborne: bool = not is_on_floor() or (not _jump.is_empty() and _jump.phase == "air")
	var h := Vector3(velocity.x, 0.0, velocity.z)
	if airborne:
		# No thrust or turning in the air; light drag; gravity.
		h *= 1.0 - AIR_DRAG
		velocity.y = maxf(velocity.y - GRAVITY, -TERMINAL)
		_air_steps += 1
		_fall_speed = maxf(_fall_speed, -velocity.y)
		if _jump.get("kind") == "climb":
			_jump.steps = int(_jump.get("steps", 0)) + 1
			if _jump.steps == 10 and _jump.ledge <= CLIMB_PUSH_MAX:
				h += global_basis.z * (0x1CCCC * _G)
	else:
		_air_steps = 0
		velocity.y = 0.0
		var busy := not _jump.is_empty() or _t_dir != 0   # wind-up / landing / transform
		var cmd := Vector3.ZERO
		if not busy:
			var inp := _inputs()
			var slow := Input.is_action_pressed("slow_walk")
			cmd = global_basis.z * inp.x * (SLOW_CMD if slow else RUN_CMD) \
				+ global_basis.x * inp.y * (SLOW_STRAFE_CMD if slow else STRAFE_CMD)
			var speed_factor := maxf(0.0, 1.0 - h.length() / (1048576.0 * _G))
			rotate_y(_turn_input() * WALK_TURN * speed_factor)
		h += cmd - h * WALK_DRAG
		if Input.is_action_pressed("slow_walk") and h.length() > 0.001 and _would_drop(h):
			h = Vector3.ZERO
	velocity.x = h.x
	velocity.z = h.z


func _would_drop(h: Vector3) -> bool:
	## Slow walk: refuse a move whose ground is 0.1 or more lower (or missing).
	var ahead := global_position + h.normalized() * maxf(h.length() * STEP, 0.3)
	var hit := _ray_down(ahead, 0.5, 2.0)
	return hit.is_empty() or global_position.y - hit.position.y >= SLOW_DROP


func _hover_step() -> void:
	var h := Vector3(velocity.x, 0.0, velocity.z)
	var inp := _inputs()
	var slow := Input.is_action_pressed("slow_walk")
	var braking := Input.is_action_pressed("jump")
	# Idle braking ramp: clock units without move input (turning counts as idle).
	if inp == Vector2.ZERO:
		_idle_clock = minf(_idle_clock + STEP * 300.0, IDLE_BRAKE_FULL)
	else:
		_idle_clock = 0.0
	var drag := HOVER_DRAG
	if _idle_clock > IDLE_BRAKE_START:
		drag = HOVER_DRAG + (_idle_clock - IDLE_BRAKE_START) * 0x5334 / 256.0 / 65536.0
	var cmd := Vector3.ZERO
	if braking:
		drag = HOVER_BRAKE_DRAG
	else:
		cmd = global_basis.z * inp.x * (HOVER_SLOW_CMD if slow else HOVER_CMD) \
			+ global_basis.x * inp.y * (HOVER_SLOW_STRAFE_CMD if slow else HOVER_STRAFE_CMD)
	if is_on_wall():
		drag = HOVER_WALL_DRAG
	h += cmd - h * drag
	h = h.limit_length(HOVER_MAX_SPEED)
	velocity.x = h.x
	velocity.z = h.z
	velocity.y = 0.0 if is_on_floor() else maxf(velocity.y - GRAVITY, -TERMINAL)
	# Turn rate builds up while held; drops to 0 on release or reversal.
	var turn := _turn_input()
	if turn == 0.0 or (_hover_turn != 0.0 and signf(turn) != signf(_hover_turn)):
		_hover_turn = 0.0
	if turn != 0.0:
		_hover_turn = clampf(_hover_turn + turn * HOVER_TURN_RAMP * STEP, -HOVER_TURN_MAX, HOVER_TURN_MAX)
	rotate_y(_hover_turn / 2.0 * TAU / 4096.0)


func _step_up(delta: float) -> void:
	## Climb a low step the body ran into: up to 0.46 (walker) or 0.96 (hover).
	if not is_on_wall() or not is_on_floor():
		return
	var h := Vector3(velocity.x, 0.0, velocity.z)
	if h.length() < 0.05:
		return
	var up := Vector3(0, STEP_UP[_hover], 0)
	var motion := h * delta
	if test_move(global_transform, up):
		return
	var raised := global_transform.translated(up)
	if test_move(raised, motion):
		return
	global_position += up + motion
	apply_floor_snap()


func _update_slope_limit() -> void:
	var slow := Input.is_action_pressed("slow_walk")
	var key := "hover_slow" if _hover and slow else "hover" if _hover \
		else "slow" if slow else "strafe" if _gait.begins_with("strafe") else "run"
	floor_max_angle = deg_to_rad(SLOPE[key])
	floor_snap_length = STEP_UP[_hover]


# ─── Jumping ──────────────────────────────────────────────────────────────────

func _try_jump() -> void:
	if not _jump.is_empty() or _t_dir != 0 or not is_on_floor() or _legs_anim == null:
		return
	if Input.is_action_pressed("slow_walk"):
		return   # L1 + Triangle is a crouch in the game, not a jump
	if get_floor_angle() > JUMP_MAX_SLOPE:
		return
	var ahead := _ray_down(global_position + global_basis.z * 1.0, 3.0, 3.0)
	var ledge: float = (ahead.position.y - global_position.y) if ahead else 0.0
	_stop_gait(false)
	if _gait_was_running():
		# Running jump: launch straight away, keeping the run's momentum.
		var frame := _legs_frame()
		var track := 55 if (frame >= 2 and frame <= 5) or (frame >= 18 and frame <= 31) else 54
		_jump = {"kind": "run", "phase": "air", "hold": 57 if track == 55 else 56, "ledge": ledge}
		_set_motor(JUMP_WINDUP_LOOP)
		_play_track(track)
		_launch(0x64000, 0x18000)
	elif ledge <= STANDING_LEDGE:
		_jump = {"kind": "stand", "phase": "windup", "ledge": ledge}
		_set_motor(JUMP_WINDUP_LOOP)
		_play_track(17)
	else:
		_jump = {"kind": "climb", "phase": "windup", "ledge": ledge, "steps": 0}
		_set_motor(JUMP_WINDUP_LOOP)
		_play_track(4)


func _gait_was_running() -> bool:
	return _last_gait == "run" and Vector2(velocity.x, velocity.z).length() > RUN_CMD


func _legs_frame() -> int:
	## Current legs AnmD frame (clip names are trackNN_fA-B, 10 fps).
	var name := String(_legs_anim.current_animation)
	var parts := name.get_slice("_f", 1).split("-")
	if parts.size() < 2:
		return 0
	var a := int(parts[0])
	var b := int(parts[1])
	var f := int(_legs_anim.current_animation_position * 10.0)
	return a + f if b >= a else a - f


func _launch(vy: int, push: int) -> void:
	velocity.y = vy * _G
	velocity += global_basis.z * (push * _G)
	_air_steps = 0
	_fall_speed = 0.0
	global_position.y += 0.02   # leave the floor so move_and_slide stops snapping
	_jump.phase = "air"
	_play(JUMP_LAUNCH_SOUND)
	_set_motor(-1)


func _update_landing() -> void:
	if _hover:
		return
	var landed := is_on_floor() and velocity.y <= 0.0
	if _jump.get("phase") == "air" and landed and _air_steps > 0:
		if _jump.kind == "run" and Input.get_axis("move_back", "move_forward") > 0.0:
			# Straight back into the run cycle: hold 56 → track 40, 57 → track 38.
			_cycle_i = 2 if _jump.hold == 56 else 0
			_jump = {}
			_gait = "run"
			_set_motor(GAITS.run.loop)
			_play_track(GAITS.run.cycle[_cycle_i])
		else:
			_jump.phase = "land"
			velocity.x = 0.0
			velocity.z = 0.0
			_set_motor(LAND_LOOP)
			_play_track(5)
	elif _jump.is_empty() and not is_on_floor() and _air_steps > 2 and _gait != "":
		_stop_gait(false)   # walked off a ledge: legs to rest while falling
	elif _jump.is_empty() and landed and _fall_speed > HARD_LANDING:
		_fall_speed = 0.0
		_jump = {"kind": "fall", "phase": "land"}
		_set_motor(LAND_LOOP)
		_play_track(5)
	if landed and _jump.is_empty():
		_fall_speed = 0.0


# ─── Gaits / leg animation / sounds ───────────────────────────────────────────

func _wanted_gait() -> String:
	if not is_on_floor():
		return ""
	var inp := _inputs()
	var slow := Input.is_action_pressed("slow_walk")
	if inp.x > 0.0:
		return "slow" if slow else "run"
	if inp.x < 0.0:
		return "slow_back" if slow else "back"
	if inp.y != 0.0:
		return ("slow_strafe_" if slow else "strafe_") + ("l" if inp.y > 0.0 else "r")
	var turn := _turn_input()
	if turn != 0.0:
		return "turn_l" if turn > 0.0 else "turn_r"
	return ""


func _update_gait() -> void:
	if _legs_anim == null or not _jump.is_empty() or _t_dir != 0:
		return
	var want := _wanted_gait()
	if want == _gait:
		return
	_stop_gait(true)
	_gait = want
	if want != "":
		_last_gait = want
		var g: Dictionary = GAITS[want]
		_cycle_i = -1
		_set_motor(g.loop)
		_play_track(g.start)


func _stop_gait(with_stop_track: bool) -> void:
	if _gait == "":
		return
	var g: Dictionary = GAITS[_gait]
	_play(g.stop)
	_set_motor(-1)
	_gait = ""
	if with_stop_track and g.has("stop_track"):
		_play_track(g.stop_track)
	else:
		_play_track(0, 0.2)


func _on_legs_clip_finished(_name: StringName) -> void:
	if _t_dir != 0 or _hover:
		return
	if not _jump.is_empty():
		_jump_clip_finished()
		return
	if _gait == "":
		return
	var g: Dictionary = GAITS[_gait]
	_cycle_i = (_cycle_i + 1) % g.cycle.size()
	var track: int = g.cycle[_cycle_i]
	_play_track(track)
	if track in g.get("restart", []):
		_set_motor(g.loop, true)
	if track in g.step_on:
		_play(g.step)


func _jump_clip_finished() -> void:
	match [_jump.kind, _jump.phase]:
		["stand", "windup"]:
			_play_track(58)
			_launch(0x58000, 0x40000)
			_jump.next = [59, 60]
		["climb", "windup"]:
			_play_track(27)
			_launch(0x78000, 0)
		["stand", "air"]:
			var rest: Array = _jump.get("next", [])
			if rest.size() > 0:
				_play_track(rest.pop_front())
		["run", "air"]:
			if not _jump.get("holding", false):   # take-off 54/55 done: hold 56/57
				_jump.holding = true
				_play_track(_jump.hold)
		[_, "land"]:
			_play(LAND_END_SOUND)
			_set_motor(-1)
			_jump = {}
			_play_track(0, 0.1)


func _play_track(n: int, blend := 0.0) -> void:
	if _legs_anim and _tracks.has(n):
		_legs_anim.play(_tracks[n], blend)


func _set_motor(id: int, restart := false) -> void:
	## Positional motor loop on the mech; -1 stops it.
	if _motor == null:
		return
	if id < 0:
		_motor.stop()
		return
	var s: AudioStream = _sounds.get([id, true])
	if s and (restart or _motor.stream != s or not _motor.playing):
		_motor.stream = s
		_motor.play()


func _update_hover_sound() -> void:
	var key := "brake" if Input.is_action_pressed("jump") else \
		"move" if _inputs() != Vector2.ZERO or _turn_input() != 0.0 else "idle"
	var s: AudioStream = _sounds.get([HOVER_LOOPS[key], true])
	if _hover_motor and s and (_hover_motor.stream != s or not _hover_motor.playing):
		_hover_motor.stream = s
		_hover_motor.play()


func _load_sounds() -> void:
	var root: Node = self   # the level scene's root is named after the level
	while root and not root.scene_file_path.ends_with("_level.tscn"):
		root = root.get_parent()
	if root == null:
		return
	_base = "res://assets/%s/" % root.name
	var f := FileAccess.open(_base + "shd.json", FileAccess.READ)
	var shd: Variant = JSON.parse_string(f.get_as_text()) if f else null
	if not shd is Dictionary:
		return
	# [script_id, positional]: positional ones are the motor loops.
	var wanted := [[TRANSFORM_SOUND, false], [JUMP_WINDUP_LOOP, true], [JUMP_LAUNCH_SOUND, false],
		[LAND_LOOP, true], [LAND_END_SOUND, false]]
	for id: int in HOVER_LOOPS.values():
		wanted.append([id, true])
	for g: Dictionary in GAITS.values():
		wanted.append_array([[g.loop, true], [g.step, false], [g.stop, false]])
	for t: Dictionary in shd.get("tables", []):
		for e: Dictionary in t.get("entries", []):
			var key := [int(e.script_id), bool(e.positional)]
			if _sounds.has(key) or not key in wanted or not e.get("tone_wav") \
					or not ResourceLoader.exists(_base + e.tone_wav):
				continue
			var stream := (load(_base + e.tone_wav) as AudioStreamWAV).duplicate() as AudioStreamWAV
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


# ─── Walker <-> hover transform ───────────────────────────────────────────────

func is_hover() -> bool:
	return _hover


func start_transform() -> void:
	if _t_dir != 0 or _cockpit_anim == null or _legs_anim == null:
		return
	if not _hover and (not _jump.is_empty() or not is_on_floor()):
		return
	var to_hover := _t <= 0.0
	_t_dir = 1 if to_hover else -1
	if to_hover:
		_hover = true
		_gait = ""
		_set_motor(-1)      # the walker's motor loop just stops (no stop sound)
		_set_box(HOVER_BOX)
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
		_play_track(13)
	elif _t <= 0.0 and _t_dir < 0:
		_t_dir = 0
		_hover = false
		_hover_turn = 0.0
		_idle_clock = 0.0
		_set_box(WALKER_BOX)
		_cockpit_anim.play(_anim_named(_cockpit_anim, "track00_"))   # walker rest
		_play_track(0)
		if _hover_motor:
			_hover_motor.stop()


# ─── Camera ───────────────────────────────────────────────────────────────────

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
