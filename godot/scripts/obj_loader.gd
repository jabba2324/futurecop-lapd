## Runtime Wavefront OBJ/MTL loader for the extractor output in parsed/, so the
## asset viewer can show any level without copying it into res://assets/.
##
## Supports what tools/extract_objects.py and extract_terrain.py write:
##   v x y z [r g b]   (terrain: the PS1 baked shading / 256, drawn ×2; "_lfx"
##                      materials get slfx_water.gd's animated light instead)
##   vt u v, vn x y z, f a/b/c … (any polygon, fan-triangulated; vt/vn optional)
##   usemtl NAME, mtllib FILE with newmtl / map_Kd / Kd
## One surface per material. Textures are cached by absolute path.
extends RefCounted

const SlfxWater := preload("res://scripts/slfx_water.gd")

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
		if has_colors:
			mat.albedo_color = Color(2, 2, 2)   # PS1 shading / 256: 0.5 = texture unchanged
		var water := SlfxWater.material(mat)
		mesh.surface_set_material(mesh.get_surface_count() - 1, water if water else mat)
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
						# PS1 colour-keyed texels come through as alpha 0; semi-transparent
						# terrain ("_mix", extract_terrain.py) blends 50/50 via alpha 128.
						if cur.resource_name.contains("_mix"):
							cur.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
						else:
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


# Environment (reflection) mapping, after fc-mit's GLES2 renderer: an env-mapped face
# adds the level's sphere map, looked up by the view-space reflection vector, where its
# own texture is see-through (shine × (1 − alpha); STP texels are alpha 0.5). The
# sphere map is the 128×128 disc at (0, 124) in cbmp010 (fc-mit Environment.cpp); every
# level has one there. Not checked against the game's own env-map code.
const _ENV_SHADER := """
shader_type spatial;
render_mode unshaded, cull_back%s;
uniform sampler2D albedo_tex : source_color, filter_nearest;
uniform sampler2D shine : source_color, filter_linear;
varying vec2 env_uv;
void vertex() {
	vec3 e = normalize((MODELVIEW_MATRIX * vec4(VERTEX, 1.0)).xyz);
	vec3 n = normalize((MODELVIEW_MATRIX * vec4(NORMAL, 0.0)).xyz);
	vec3 r = reflect(e, n);
	float m = 2.0 * sqrt(r.x * r.x + r.y * r.y + (r.z + 1.0) * (r.z + 1.0));
	env_uv = vec2(r.x / m + 0.5, 0.5 - r.y / m);
}
void fragment() {
	vec4 c = texture(albedo_tex, UV) * COLOR;
	ALBEDO = c.rgb + texture(shine, env_uv).rgb * (1.0 - c.a);
	%s
}
"""
static var _env_shaders: Dictionary = {}   # mix? -> Shader
static var _shines: Dictionary = {}        # cbmp010 path -> Texture2D


static func shine_texture(cbmp010_path: String) -> Texture2D:
	## The sphere-map disc cut out of a level's cbmp010.png (cached).
	if not _shines.has(cbmp010_path):
		var tex := load_texture(cbmp010_path)
		var img := tex.get_image() if tex else null
		if img and img.is_compressed():
			img.decompress()
		_shines[cbmp010_path] = ImageTexture.create_from_image(img.get_region(Rect2i(0, 124, 128, 128))) if img else null
	return _shines[cbmp010_path]


static func _env_material(src: StandardMaterial3D, shine: Texture2D) -> ShaderMaterial:
	var mix := src.resource_name.contains("_mix")
	if not _env_shaders.has(mix):
		var sh := Shader.new()
		sh.code = _ENV_SHADER % ([", blend_mix, depth_draw_opaque", "ALPHA = c.a > 0.99 ? 1.0 : 0.5;"] if mix else ["", ""])
		_env_shaders[mix] = sh
	var m := ShaderMaterial.new()
	m.shader = _env_shaders[mix]
	m.resource_name = src.resource_name
	m.set_shader_parameter("albedo_tex", src.albedo_texture)
	m.set_shader_parameter("shine", shine)
	return m


static func apply_fc_materials(root: Node, glb_path: String) -> void:
	## PS1 material modes the glTF import can't express, for a scene instanced from
	## <level>/models/<cobj>.glb (export_gltf.py material suffixes):
	##   "_add": glTF has no additive mode, so these import as alpha blending; switch them
	##           to additive. ("_mix" already imports as alpha blending.)
	##   "_env": environment-mapped faces get the sphere-map shader above, with the
	##           sphere map from <level>/textures/cbmp010.png.
	var shine: Texture2D = null
	for mi: MeshInstance3D in root.find_children("*", "MeshInstance3D", true, false):
		if mi.mesh == null:
			continue
		for s in mi.mesh.get_surface_count():
			var m := mi.mesh.surface_get_material(s) as StandardMaterial3D
			if m == null:
				continue   # already converted (shared mesh) or not a glTF material
			if m.resource_name.contains("_add"):
				m.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
				m.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
			if m.resource_name.ends_with("_env"):
				if shine == null:
					shine = shine_texture(glb_path.get_base_dir().get_base_dir().path_join("textures/cbmp010.png"))
				if shine:
					mi.mesh.surface_set_material(s, _env_material(m, shine))
