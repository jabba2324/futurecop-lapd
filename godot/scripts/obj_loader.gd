## Runtime Wavefront OBJ/MTL loader for the extractor output in parsed/, so the
## asset viewer can show any level without copying it into res://assets/.
##
## Supports what tools/extract_objects.py and extract_terrain.py write:
##   v x y z [r g b]   (terrain vertex colours encode the PS1 baked shading)
##   vt u v, vn x y z, f a/b/c … (any polygon, fan-triangulated; vt/vn optional)
##   usemtl NAME, mtllib FILE with newmtl / map_Kd / Kd
## One surface per material. Textures are cached by absolute path.
extends RefCounted

static var _textures: Dictionary = {}


static func load_mesh(path: String) -> ArrayMesh:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return null
	var dir := path.get_base_dir()
	var pos := PackedVector3Array()
	var col := PackedColorArray()
	var uv := PackedVector2Array()
	var nrm := PackedVector3Array()
	var materials: Dictionary = {}
	var groups: Dictionary = {}   # material name -> {v, n, t, c}
	var cur := "default"
	var has_colors := false

	while not f.eof_reached():
		var line := f.get_line()
		if line.length() < 2 or line[0] == "#":
			continue
		var p := line.split(" ", false)
		match p[0]:
			"v":
				pos.append(Vector3(p[1].to_float(), p[2].to_float(), p[3].to_float()))
				if p.size() >= 7:
					col.append(Color(p[4].to_float(), p[5].to_float(), p[6].to_float()))
					has_colors = true
				else:
					col.append(Color.WHITE)
			"vt":
				# OBJ v runs bottom-up; Godot UVs run top-down.
				uv.append(Vector2(p[1].to_float(), 1.0 - p[2].to_float()))
			"vn":
				nrm.append(Vector3(p[1].to_float(), p[2].to_float(), p[3].to_float()))
			"usemtl":
				cur = p[1] if p.size() > 1 else "default"
			"mtllib":
				materials.merge(_load_mtl(dir.path_join(line.substr(7).strip_edges())))
			"f":
				if not groups.has(cur):
					groups[cur] = {"v": PackedVector3Array(), "n": PackedVector3Array(),
						"t": PackedVector2Array(), "c": PackedColorArray()}
				var g: Dictionary = groups[cur]
				var corners: Array = []
				for i in range(1, p.size()):
					corners.append(p[i].split("/"))
				# Godot treats clockwise as front-facing, OBJ counter-clockwise:
				# emit each fan triangle as (0, i+1, i).
				for i in range(1, corners.size() - 1):
					for c in [corners[0], corners[i + 1], corners[i]]:
						var vi := int(c[0]) - 1
						g.v.append(pos[vi])
						g.c.append(col[vi])
						g.t.append(uv[int(c[1]) - 1] if c.size() > 1 and c[1] != "" else Vector2.ZERO)
						g.n.append(nrm[int(c[2]) - 1] if c.size() > 2 and c[2] != "" else Vector3.ZERO)

	var mesh := ArrayMesh.new()
	for name: String in groups:
		var g: Dictionary = groups[name]
		var arrays := []
		arrays.resize(Mesh.ARRAY_MAX)
		arrays[Mesh.ARRAY_VERTEX] = g.v
		arrays[Mesh.ARRAY_TEX_UV] = g.t
		arrays[Mesh.ARRAY_COLOR] = g.c
		arrays[Mesh.ARRAY_NORMAL] = _fill_normals(g.v, g.n)
		mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
		var mat: StandardMaterial3D = materials.get(name, StandardMaterial3D.new()).duplicate()
		mat.vertex_color_use_as_albedo = has_colors
		mesh.surface_set_material(mesh.get_surface_count() - 1, mat)
		mesh.surface_set_name(mesh.get_surface_count() - 1, name)
	return mesh


static func _fill_normals(v: PackedVector3Array, n: PackedVector3Array) -> PackedVector3Array:
	# Faces without vn get a flat face normal (clockwise winding, see above).
	for i in range(0, v.size(), 3):
		if n[i] == Vector3.ZERO:
			var fn := (v[i + 2] - v[i]).cross(v[i + 1] - v[i]).normalized()
			n[i] = fn
			n[i + 1] = fn
			n[i + 2] = fn
	return n


static func _load_mtl(path: String) -> Dictionary:
	var out: Dictionary = {}
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return out
	var cur: StandardMaterial3D = null
	while not f.eof_reached():
		var line := f.get_line().strip_edges()
		var p := line.split(" ", false)
		if p.is_empty():
			continue
		match p[0]:
			"newmtl":
				cur = StandardMaterial3D.new()
				cur.resource_name = p[1]
				out[p[1]] = cur
			"Kd":
				if cur:
					cur.albedo_color = Color(p[1].to_float(), p[2].to_float(), p[3].to_float())
			"map_Kd":
				if cur:
					var tex := load_texture(path.get_base_dir().path_join(line.substr(7).strip_edges()).simplify_path())
					if tex:
						cur.albedo_texture = tex
						# PS1 colour-keyed texels come through as alpha 0.
						cur.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
						cur.alpha_scissor_threshold = 0.5
	return out


static func load_texture(path: String) -> Texture2D:
	if _textures.has(path):
		return _textures[path]
	if path.begins_with("res://"):
		# Inside the project (generated scenes): use the imported texture.
		var res: Texture2D = load(path) if ResourceLoader.exists(path) else null
		_textures[path] = res
		return res
	var img := Image.load_from_file(path)
	var tex: ImageTexture = ImageTexture.create_from_image(img) if img else null
	if tex:
		tex.set_meta("source_path", path)  # lets the viewer show which page a surface uses
	_textures[path] = tex
	return tex


static func apply_fc_blend(root: Node) -> void:
	## glTF has no additive mode, so export_gltf.py marks PS1 ADDITION materials with an
	## "_add" name suffix (and alphaMode BLEND). Switch those to additive blending; MIX
	## ("_mix") already imports as alpha blending.
	for mi: MeshInstance3D in root.find_children("*", "MeshInstance3D", true, false):
		if mi.mesh == null:
			continue
		for s in mi.mesh.get_surface_count():
			var m := mi.mesh.surface_get_material(s) as StandardMaterial3D
			if m and m.resource_name.contains("_add"):
				m.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
				m.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
