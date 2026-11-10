## Load a scene, let it run for some frames (default 90), save the viewport to a
## PNG and quit. Needs a real or virtual display (e.g. Xvfb), not --headless.
##   godot --path godot --resolution 1280x720 --script res://scripts/screenshot.gd \
##     -- res://scenes/M2C_level.tscn /abs/out.png [frames]
extends SceneTree
var _frames := 0
var _wait := 90
var _out := ""
func _initialize() -> void:
	var a := OS.get_cmdline_user_args()
	_out = a[1]
	if a.size() > 2: _wait = int(a[2])
	change_scene_to_file(a[0])
func _process(_d: float) -> bool:
	_frames += 1
	if _frames == _wait:
		root.get_viewport().get_texture().get_image().save_png(_out)
		print("saved ", _out)
		return true
	return false
