## Class-29 Teleporters (SLUS_007.39 handler 0x80030d3c; only UN has them). Each is
## an invisible sphere; a player touching it is moved to the stored destination
## (0x80030c60) with its heading and velocity kept, the screen flashes white and the
## actor's on_damage script plays sound 105. level_ambience.gd adds one node for all.
##   - trigger: sphere of radius `radius` (fx12) centred `height_offset` above the
##     ground (ground_cast); contact = horizontal distance < r + the body's radius
##     and vertical < r + its half height (0x800620d8)
##   - destination: (dest_x, dest_z) << 1 in world fx16, at the ground there
##     (dest_ground_cast) + dest_height_offset * 16
##   - filter 1/1 = players (and their projectiles); no enable flag, no cooldown
##   - flash (0x80026f68): additive white at min(255, v), v from 300 down 450/s
##   - ground cast 0 = the highest floor there (0x80067684 sorts the floor heights
##     and returns the last for -1); five of UN's six lift the walker about 2 units
## Not modelled: Csac-spawned teleporters (UN's actor 58 waits for a script).
extends Node

const PLAYER_RADIUS := 0.28   # generate_scene.gd's capsule
const PLAYER_HALF := 0.45
const FLASH_START := 300.0
const FLASH_RATE := 450.0     # per second

var _pads: Array = []   # {centre: Vector3, r: float, dest: Vector3}
var _player: CharacterBody3D
var _sound: AudioStreamPlayer
var _flash: ColorRect
var _v := 0.0


func setup(decoded: Array, layout: RefCounted, top_floor: Callable, sound: AudioStream) -> void:
	name = "Teleporters"
	for rec: Dictionary in decoded:
		if int(rec.class_id) != 29 or rec.get("kind") == "Csac":
			continue
		var f: Dictionary = rec.fields
		var raw := func(n: String) -> int:
			var v := int(f.get("Teleporter." + n, {}).get("raw", 0))
			return v - 0x10000 if n.ends_with("offset") and v >= 0x8000 else v
		var at: Vector3 = layout.actor_position({"pos_x": rec.header.pos_x.value, "pos_z": rec.header.pos_z.value})
		var to: Vector3 = layout.actor_position({"pos_x": raw.call("dest_x") * 2 / 65536.0,
			"pos_z": raw.call("dest_z") * 2 / 65536.0})
		at.y = top_floor.call(at) + raw.call("height_offset") / 512.0
		to.y = top_floor.call(to) + raw.call("dest_height_offset") / 512.0
		_pads.append({"centre": at, "r": raw.call("radius") / 512.0, "dest": to, "id": int(rec.id)})
	if _pads.is_empty():
		return
	_player = get_tree().get_first_node_in_group("player") as CharacterBody3D
	if sound:
		_sound = AudioStreamPlayer.new()
		_sound.stream = sound
		add_child(_sound)
	var layer := CanvasLayer.new()
	add_child(layer)
	_flash = ColorRect.new()
	_flash.set_anchors_preset(Control.PRESET_FULL_RECT)
	_flash.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var mat := CanvasItemMaterial.new()
	mat.blend_mode = CanvasItemMaterial.BLEND_MODE_ADD
	_flash.material = mat
	_flash.color = Color(1, 1, 1, 0)
	layer.add_child(_flash)


func pads() -> Array:
	return _pads


func _physics_process(delta: float) -> void:
	if _player == null:
		return
	var body := _player.global_position + Vector3(0, PLAYER_HALF, 0)
	for p: Dictionary in _pads:
		var d: Vector3 = body - p.centre
		if Vector2(d.x, d.z).length() < p.r + PLAYER_RADIUS and absf(d.y) < p.r + PLAYER_HALF:
			_player.global_position = p.dest
			_v = FLASH_START
			if _sound:
				_sound.play()
			break


func _process(delta: float) -> void:
	if _flash == null:
		return
	_v = maxf(_v - FLASH_RATE * delta, 0.0)
	_flash.color.a = minf(_v, 255.0) / 255.0
