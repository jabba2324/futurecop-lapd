## Materials for terrain polygons lit by the SLFX light table (extract_terrain.py
## names them "..._lfx<word>"): the animated light of shaders/slfx_water.gdshaderinc.
extends RefCounted

const OPAQUE := preload("res://shaders/slfx_water.gdshader")
const MIX := preload("res://shaders/slfx_water_mix.gdshader")


static func word(material_name: String) -> int:
	## The SLFX word of an "_lfx" material, or -1.
	var m := RegEx.create_from_string("_lfx([0-9a-f]{8})").search(material_name)
	return m.get_string(1).hex_to_int() if m else -1


static func material(src: Material) -> ShaderMaterial:
	## A ShaderMaterial standing in for src (null if src isn't an "_lfx" material).
	if src == null or word(src.resource_name) < 0:
		return null
	var m := ShaderMaterial.new()
	m.resource_name = src.resource_name
	m.shader = MIX if src.resource_name.contains("_mix") else OPAQUE
	m.set_shader_parameter("word", word(src.resource_name))
	if src is BaseMaterial3D:
		m.set_shader_parameter("tex", (src as BaseMaterial3D).albedo_texture)
	return m
