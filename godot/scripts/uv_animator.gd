## Plays a model's 3DTA texture animations (mesh.extras.fc_uv_animations from
## tools/export_gltf.py): each animation's faces sit in their own primitives with
## frame-0 UVs, and every vertex follows its nearest frame-0 corner through the
## frames. Frames loop; the per-animation flags are not decoded yet.
##
## Per model (cache key, e.g. the .glb path) the base mesh is read once and one
## ArrayMesh per frame combination is built once and shared by every instance;
## playback just swaps MeshInstance3D.mesh. (Rebuilding per instance per frame
## change dropped LAX1, with 85 animated props, from 144 to 6 frames/s.)
## Usage: var a := UvAnimator.new(); a.setup(mesh_instance, anims, key); part.add_child(a)
extends Node

static var _cache: Dictionary = {}   # key -> {surfaces, blend_names, blend_mode, corner, meshes}

var _mi: MeshInstance3D
var _anims: Array = []
var _model: Dictionary = {}
var _frame: PackedInt32Array = []
var _time := 0.0


static func read_glb_uv_animations(path: String) -> Array:
	## fc_uv_animations from a .glb's JSON chunk ([] when absent).
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null or f.get_length() < 20:
		return []
	f.seek(12)
	var json_len := f.get_32()
	f.seek(20)
	var j: Variant = JSON.parse_string(f.get_buffer(json_len).get_string_from_utf8())
	if not j is Dictionary or j.get("meshes", []).is_empty():
		return []
	return j.meshes[0].get("extras", {}).get("fc_uv_animations", [])


static func clear_cache() -> void:
	_cache.clear()


func setup(mi: MeshInstance3D, anims: Array, key: String) -> void:
	_mi = mi
	_anims = anims
	_frame.resize(anims.size())
	if not _cache.has(key):
		_cache[key] = _read_model(mi.mesh as ArrayMesh, anims)
	_model = _cache[key]


static func _read_model(mesh: ArrayMesh, anims: Array) -> Dictionary:
	var model := {"surfaces": [], "blend_names": [], "corner": {}, "meshes": {},
		"blend_mode": mesh.blend_shape_mode if mesh else 0}
	if mesh == null:
		return model
	for b in mesh.get_blend_shape_count():
		model.blend_names.append(mesh.get_blend_shape_name(b))
	for s in mesh.get_surface_count():
		model.surfaces.append({"arrays": mesh.surface_get_arrays(s), "blend": mesh.surface_get_blend_shape_arrays(s),
			"prim": mesh.surface_get_primitive_type(s), "material": mesh.surface_get_material(s),
			"name": mesh.surface_get_name(s)})
	for ai in anims.size():
		var f0: Array = anims[ai].frames[0]
		for s in anims[ai].get("primitives", []):
			if int(s) >= model.surfaces.size():
				continue
			var uvs: PackedVector2Array = model.surfaces[int(s)].arrays[Mesh.ARRAY_TEX_UV]
			var corner := PackedInt32Array()
			for uv in uvs:
				var best := 0
				var best_d := INF
				for c in 4:
					var d := uv.distance_squared_to(Vector2(f0[c][0], f0[c][1]))
					if d < best_d:
						best_d = d
						best = c
				corner.append(best)
			model.corner[int(s)] = [ai, corner]
	return model


func _process(delta: float) -> void:
	if _model.is_empty() or _model.corner.is_empty():
		return
	_time += delta
	var changed := false
	for ai in _anims.size():
		var dur := maxf(float(_anims[ai].frame_duration_s), 0.016)
		var f: int = int(_time / dur) % int(_anims[ai].frames.size())
		if f != _frame[ai]:
			_frame[ai] = f
			changed = true
	if changed:
		var k := str(_frame)
		if not _model.meshes.has(k):
			_model.meshes[k] = _build(_frame)
		_mi.mesh = _model.meshes[k]


func _build(frame: PackedInt32Array) -> ArrayMesh:
	var mesh := ArrayMesh.new()
	mesh.blend_shape_mode = _model.blend_mode
	for n in _model.blend_names:
		mesh.add_blend_shape(n)
	for s in _model.surfaces.size():
		var surf: Dictionary = _model.surfaces[s]
		var arrays: Array = surf.arrays.duplicate()
		if _model.corner.has(s):
			var ai: int = _model.corner[s][0]
			var corner: PackedInt32Array = _model.corner[s][1]
			var fr: Array = _anims[ai].frames[frame[ai]]
			var uvs := PackedVector2Array()
			uvs.resize(corner.size())
			for v in corner.size():
				uvs[v] = Vector2(fr[corner[v]][0], fr[corner[v]][1])
			arrays[Mesh.ARRAY_TEX_UV] = uvs
		mesh.add_surface_from_arrays(surf.prim, arrays, surf.blend)
		mesh.surface_set_material(s, surf.material)
		mesh.surface_set_name(s, surf.name)
	return mesh
