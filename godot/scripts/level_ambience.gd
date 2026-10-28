## Ambient scene dressing for a generated level scene (root of
## scenes/<LEVEL>_level.tscn): loops the effects the level places at load
## (effect_player.gd) and plays 3DTA texture animations on the instanced .glb
## parts (uv_animator.gd). Nothing reacts to the player; it's visuals only.
## Reads res://assets/<LEVEL>/actors_decoded.json, csfx.json, heightmap.json and
## particles/ (synced by tools/sync_godot_assets.py).
extends Node3D

const EffectPlayer := preload("res://scripts/effect_player.gd")
const UvAnimator := preload("res://scripts/uv_animator.gd")
const LevelLayout := preload("res://scripts/level_layout.gd")


func _ready() -> void:
	var base := "res://assets/%s/" % name
	_start_effects(base)
	_start_texture_animations()


func _json(path: String) -> Variant:
	var f := FileAccess.open(path, FileAccess.READ)
	return JSON.parse_string(f.get_as_text()) if f else null


func _start_effects(base: String) -> void:
	var decoded: Variant = _json(base + "actors_decoded.json")
	if not decoded is Dictionary:
		return
	var actors: Array = decoded.get("actors", [])
	var reg := EffectPlayer.build_registry(actors, _json(base + "csfx.json"))
	var layout = LevelLayout.new(FileAccess.get_file_as_string(base + "heightmap.json"))
	var root := Node3D.new()
	root.name = "Effects"
	add_child(root)
	for rec: Dictionary in actors:
		if not EffectPlayer.placed_at_load(rec):
			continue
		var id: Variant = rec.fields.get("EffectHeader.effect_id", {}).get("value")
		if id == null or not reg.effects.has(int(id)):
			continue
		var anchor := Node3D.new()
		anchor.position = layout.actor_position({"pos_x": rec.header.pos_x.value, "pos_z": rec.header.pos_z.value})
		var p := EffectPlayer.new()
		p.setup(reg.effects[int(id)], reg.effects, base + "particles", true)
		anchor.add_child(p)
		root.add_child(anchor)


func _start_texture_animations() -> void:
	for n: Node in find_children("*", "Node3D", true, false):
		var path := n.scene_file_path
		if not path.ends_with(".glb"):
			continue
		var anims := UvAnimator.read_glb_uv_animations(path)
		if anims.is_empty():
			continue
		# The part's own mesh (named <model>_mesh); nested parts have their own instances.
		var mesh_name := path.get_file().get_basename() + "_mesh"
		for mi: MeshInstance3D in n.find_children(mesh_name, "MeshInstance3D", true, false):
			var a := UvAnimator.new()
			a.setup(mi, anims, path)
			n.add_child(a)
			break
