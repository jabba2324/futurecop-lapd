## K in the walk scenes (level_ambience.gd adds it): steps the nearest damageable
## prop through the game's damage states, without combat. From SLUS_007.39:
##   - a damaged actor shows one effect for its current health stage, attached to it
##     and restarted on every hit (0x8004ce04 → 0x8003bae4, 0x8003b65c): below 75 %,
##     50 % and 25 % the damage set's (BaseEntity.explosion) part effects
##   - destroyed (0x8002a810 → 0x8003b898): the part's destroy effect, or half the
##     time the set's random destroy effect when set_flags bit 0 is clear; then the
##     actor's wreck model replaces it if it has one (0x80031690), else it vanishes
##     (0x8004d958)
## Group 0 is what plain hits show; groups 1–4 belong to weapon damage types (bits
## 2, 4, 8, 16). When group 0 has nothing for a stage the first group that does is
## shown instead, and the label says so.
## The wreck gets convex-hull collision in place of the prop's, unless the actor has
## disable_destroyed_collision.
extends Node

const EffectPlayer := preload("res://scripts/effect_player.gd")
const TerrainCollision := preload("res://scripts/terrain_collision.gd")
const RANGE := 8.0
const STAGES := ["intact", "below 75 %", "below 50 %", "below 25 %", "destroyed"]
const FIELDS := ["", "damage_75_effect", "damage_50_effect", "damage_25_effect", "destroy_effect"]

var _actors: Node3D
var _registry: Dictionary     # EffectPlayer.build_registry
var _sprites: String
var _sets := {}               # actor id -> damage set record
var _stage := {}              # actor id -> stage index
var _playing := {}            # actor id -> EffectPlayer
var _label: Label
var _label_t := 0.0
var _rng := RandomNumberGenerator.new()
var _sound: Callable          # (ref, position): effect sounds


func setup(actors: Node3D, decoded: Array, registry: Dictionary, sprites: String, sound := Callable()) -> void:
	name = "DamagePreview"
	_sound = sound
	_actors = actors
	_registry = registry
	_sprites = sprites
	_rng.randomize()
	for rec: Dictionary in decoded:
		var ref := str(rec.fields.get("BaseEntity.explosion", {}).get("value", ""))
		if ref.begins_with("damage_effect_set "):
			var ds: Variant = registry.damage_sets.get(int(ref.trim_prefix("damage_effect_set ")))
			if ds and actors.get_node_or_null("Actor_%d" % rec.id) and int(rec.class_id) != 1:
				_sets[int(rec.id)] = ds
				var flags: Variant = rec.fields.get("BaseEntity.bitfield", {}).get("value")
				if flags is Array and "disable_destroyed_collision" in flags:
					actors.get_node("Actor_%d" % rec.id).set_meta("no_wreck_collision", true)
	var layer := CanvasLayer.new()
	add_child(layer)
	_label = Label.new()
	_label.set_anchors_and_offsets_preset(Control.PRESET_CENTER_BOTTOM)
	_label.offset_top = -90
	_label.offset_bottom = -60
	_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_label.grow_horizontal = Control.GROW_DIRECTION_BOTH
	_label.add_theme_color_override("font_outline_color", Color.BLACK)
	_label.add_theme_constant_override("outline_size", 6)
	_label.visible = false
	layer.add_child(_label)


func step(from: Vector3) -> void:
	var best := -1
	var best_d := RANGE
	for id: int in _sets:
		var n: Node3D = _actors.get_node("Actor_%d" % id)
		var d := n.global_position.distance_to(from)
		if d < best_d:
			best_d = d
			best = id
	if best < 0:
		_show("No damageable prop within %d units" % int(RANGE))
		return
	var stage: int = (_stage.get(best, 0) + 1) % STAGES.size()
	_stage[best] = stage
	var node: Node3D = _actors.get_node("Actor_%d" % best)
	if _playing.has(best) and is_instance_valid(_playing[best]):
		_playing[best].queue_free()
	_playing.erase(best)
	var radius := _radius(node)   # before the destroyed state hides the model
	_set_destroyed(node, stage == STAGES.size() - 1)
	var id := -1
	var group := 0
	if stage > 0:
		for g in 5:
			id = _effect_id(_sets[best], "part_%d_%s" % [g, FIELDS[stage]])
			if id >= 0:
				group = g
				break
		if stage == STAGES.size() - 1 and group == 0:
			var rnd := _effect_id(_sets[best], "random_destroy_effect")
			var flags := int(_sets[best].fields.get("DamageEffectSet.set_flags", {}).get("raw", 0))
			if rnd >= 0 and (id < 0 or (not flags & 1 and _rng.randf() < 0.5)):
				id = rnd
				group = 0
	if id >= 0 and _registry.effects.has(id):
		var p := EffectPlayer.new()
		p.owner_radius = radius
		p.sound = _sound
		p.owner_meshes = _alive_meshes(node)
		p.setup(_registry.effects[id], _registry.effects, _sprites, false)
		node.add_child(p)
		_playing[best] = p
	var rec_name := str(node.get_meta("class", ""))
	var what := ""
	if id >= 0:
		what = " · effect %d" % id + (" (weapon damage type %d)" % (1 << group) if group > 0 else "")
	elif stage > 0:
		what = " · no effect"
	_show("Actor %d (class %s): %s%s" % [best, rec_name, STAGES[stage], what])


static func _effect_id(ds: Dictionary, field: String) -> int:
	var v := str(ds.fields.get("DamageEffectSet." + field, {}).get("value", ""))
	return int(v.trim_prefix("effect ")) if v.begins_with("effect ") else -1


func _set_destroyed(node: Node3D, destroyed: bool) -> void:
	if destroyed and not node.get_meta("no_wreck_collision", false):
		_add_wreck_collision(node)
	for c: Node in node.get_children():
		if c is CollisionObject3D:
			var wreck: bool = c.has_meta("wreck")
			(c as CollisionObject3D).process_mode = Node.PROCESS_MODE_INHERIT \
				if wreck == destroyed else Node.PROCESS_MODE_DISABLED
		elif c is Node3D and str(c.name).begins_with("cobj"):
			var wreck: bool = c.has_meta("wreck")
			(c as Node3D).visible = (wreck and destroyed) or (not wreck and not destroyed)


static func _add_wreck_collision(node: Node3D) -> void:
	## Convex hulls for the wreck models, like terrain_collision.gd's props (built once).
	if node.has_meta("wreck_collision"):
		return
	node.set_meta("wreck_collision", true)
	for part: Node in node.get_children():
		if not (part is Node3D and part.has_meta("wreck")):
			continue
		var meshes := part.find_children("*", "MeshInstance3D", true, false)
		if part is MeshInstance3D:
			meshes.append(part)
		for mi: MeshInstance3D in meshes:
			if mi.mesh == null or mi.get_aabb().size.length() < 0.05:
				continue
			var body := StaticBody3D.new()
			body.name = "WreckCollision"
			body.set_meta("wreck", true)
			body.process_mode = Node.PROCESS_MODE_DISABLED
			var cs := CollisionShape3D.new()
			cs.shape = TerrainCollision.solid_hull(mi)
			if cs.shape == null:
				continue
			cs.transform = node.global_transform.affine_inverse() * mi.global_transform
			body.add_child(cs)
			node.add_child(body)


static func _alive_meshes(node: Node3D) -> Array:
	var out := []
	for c: Node in node.get_children():
		if c is Node3D and str(c.name).begins_with("cobj") and not c.has_meta("wreck"):
			out.append_array(c.find_children("*", "MeshInstance3D", true, false))
			if c is MeshInstance3D:
				out.append(c)
	return out


static func _radius(node: Node3D) -> float:
	var r := 0.25
	for mi: MeshInstance3D in node.find_children("*", "MeshInstance3D", true, false):
		if mi.mesh and mi.is_visible_in_tree():
			var s := mi.get_aabb().size
			r = maxf(r, maxf(s.x, s.z) / 2.0)
	return r


func _show(text: String) -> void:
	_label.text = text
	_label.visible = true
	_label_t = 3.0


func _process(delta: float) -> void:
	if _label_t > 0.0:
		_label_t -= delta
		_label.visible = _label_t > 0.0
