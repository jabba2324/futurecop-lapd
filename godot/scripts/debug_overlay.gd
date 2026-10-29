## The walk scenes' debug overlay (O toggles; level_ambience.gd adds it): how the level
## is wired, drawn from the decoded data with the asset viewer's builders
## (level_overlays.gd). Nothing here is triggered or simulated.
##   - trigger zones (class 95 boxes, 14 cylinders, 38 attached boxes), each labelled
##     with its actor id and what its scripts message (e.g. "→ Csac 34 spawn")
##   - Cnet path graphs
##   - special floors coloured by kind: blue deep water, red deadly, orange damaging,
##     pale blue low traction, green conveyor, yellow Precinct Assault team zone
extends Node3D

const LevelOverlays := preload("res://scripts/level_overlays.gd")
const LABEL_RANGE := 25.0   # labels further away than this are hidden
const MAX_TARGETS := 4      # message lines per label


func setup(base: String, decoded: Array, layout: RefCounted) -> void:
	name = "DebugOverlay"
	var anchors := {}
	for rec: Dictionary in decoded:
		var n := Node3D.new()
		n.position = layout.actor_position({"pos_x": rec.header.pos_x.value, "pos_z": rec.header.pos_z.value})
		anchors[int(rec.id)] = n
	var zones := LevelOverlays.build_zones(decoded, anchors)
	add_child(zones)
	_add_labels(zones, decoded, _json(base + "rpns.json"), _json(base + "fun.json"))
	var nets: Variant = _json(base + "nets.json")
	add_child(LevelOverlays.build_nets(nets.get("nets", []) if nets is Dictionary else [], layout).mesh)
	add_child(LevelOverlays.build_floors(_json(base + "terrain/surfaces.json")))
	for a: Node in anchors.values():
		a.free()


func _json(path: String) -> Variant:
	var f := FileAccess.open(path, FileAccess.READ)
	return JSON.parse_string(f.get_as_text()) if f else null


func _add_labels(zones: Node3D, decoded: Array, rpns: Variant, fun: Variant) -> void:
	var names := {}
	for rec: Dictionary in decoded:
		names[int(rec.id)] = str(rec.get("class_name", "actor"))
	var out := {}   # actor id -> ["→ Csac 34 spawn", ...]
	for e: Dictionary in LevelOverlays.build_wiring(decoded, rpns, fun).edges:
		if e.kind == "script":
			var msg: String = e.label.get_slice("→ ", 1)
			out.get_or_add(e.from, []).append("→ %s %d %s" % [names.get(e.to, "actor"), e.to, msg])
	for zone: MeshInstance3D in zones.get_children():
		var id: int = zone.get_meta("actor_id")
		var lines: Array = ["%s %d" % [names.get(id, "zone"), id]]
		var targets: Array = out.get(id, [])
		lines.append_array(targets.slice(0, MAX_TARGETS))
		if targets.size() > MAX_TARGETS:
			lines.append("… %d more" % (targets.size() - MAX_TARGETS))
		var label := Label3D.new()
		label.text = "\n".join(lines)
		label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		label.no_depth_test = true
		label.pixel_size = 0.004
		label.font_size = 32
		label.outline_size = 8
		label.modulate = Color(1.0, 0.95, 0.7)
		label.visibility_range_end = LABEL_RANGE
		label.position = Vector3(0, zone.get_aabb().size.y / 2.0 + 0.2, 0)
		zone.add_child(label)
