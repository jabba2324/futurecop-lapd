## The walkable X1-Alpha: a CharacterBody3D that follows the terrain collision
## built by terrain_collision.gd. W/S walks along +Z (the X1's forward, see
## level_layout.assemble_x1), A/D turns.
extends CharacterBody3D

const SPEED := 5.0
const TURN_SPEED := 2.5
const GRAVITY := 20.0
# Walk cycle of the X1-Alpha legs: AnmD tracks 38→39→40→41 chain frames 11–39
# into a closed loop (next_track links, see tools/export_gltf.py). Track 0 is rest.
const WALK_CHAIN := ["track38_", "track39_", "track40_", "track41_"]
const REST_PREFIX := "track00_"

var _legs_anim: AnimationPlayer
var _walk: PackedStringArray = []
var _rest := ""
var _step := 0
var _moving := false
var _grounded_at_start := false

func _ready() -> void:
	add_to_group("player")
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
