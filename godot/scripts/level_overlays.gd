## Builders for the asset viewer's Level-view overlays, all drawn from decoded
## data (tools/decode_actors.py, extract_dcs.py, extract_rpns.py, extract_fun.py,
## export_heightmap.py) — nothing here simulates the game.
##   decals        class-97 DCSQuad textured quads (dcs.json rectangles)
##   zones         class-95 Trigger boxes, class-14 CollisionTrigger cylinders,
##                 class-38 AttachedCollisionBox boxes on their attach actor
##   ranges        engage/look range circles (fov is always a full circle: 0 BAM)
##   height layers the three Ctil height channels as coloured points
##   wiring        who-messages-whom graph from actor scripts, level functions,
##                 trigger and attach references
## Sizes in the decoded data are fx12 game units; 1.0 = 8 Godot units.
extends RefCounted

const ObjLoader := preload("res://scripts/obj_loader.gd")
const U := 8.0
# Faint fill (zones can be huge and the camera often ends up inside one) plus solid edges.
const ZONE_COLORS := {95: Color(1.0, 0.85, 0.1, 0.07), 14: Color(0.2, 1.0, 0.4, 0.07), 38: Color(1.0, 0.3, 1.0, 0.1)}


static func field(rec: Dictionary, name: String, default: Variant = null) -> Variant:
	## Value of the first field whose name ends in "." + name (segment-agnostic).
	for k: String in rec.get("fields", {}):
		if k.ends_with("." + name):
			return rec.fields[k].get("value", default)
	return default


static func _flat(c: Color) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	m.albedo_color = c
	m.cull_mode = BaseMaterial3D.CULL_DISABLED
	if c.a < 1.0:
		m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	return m


static func _line_material() -> StandardMaterial3D:
	var m := _flat(Color.WHITE)
	m.vertex_color_use_as_albedo = true
	m.no_depth_test = true
	return m


# ─── Decals ───────────────────────────────────────────────────────────────────

static func build_decals(decoded: Array, dcs: Variant, ldir: String, layout: RefCounted) -> Node3D:
	var root := Node3D.new()
	root.name = "Decals"
	var rects := {}
	if dcs is Dictionary:
		for table: Dictionary in dcs.get("cdcs", []):
			for e: Dictionary in table.get("entries", []):
				if not e.get("placeholder", false) and e.get("texture_exists", false):
					rects[int(e.dcs_id)] = e
	var mats := {}
	for rec: Dictionary in decoded:
		if int(rec.class_id) != 97:
			continue
		var e: Variant = rects.get(int(field(rec, "dcs_id", -1)))
		if e == null:
			continue
		var tex_path: String = ldir.path_join(e.texture)
		if not mats.has(tex_path):
			var m := StandardMaterial3D.new()
			m.albedo_texture = ObjLoader.load_texture(tex_path)
			m.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
			m.vertex_color_use_as_albedo = true
			m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
			m.cull_mode = BaseMaterial3D.CULL_DISABLED
			mats[tex_path] = m
		var w: float = float(field(rec, "width", 0.1)) * U
		var h: float = float(field(rec, "height", 0.1)) * U
		var tint := Color(float(field(rec, "red", 128)) / 128.0, float(field(rec, "green", 128)) / 128.0,
			float(field(rec, "blue", 128)) / 128.0)
		var mi := MeshInstance3D.new()
		mi.mesh = _quad(w, h, e.uv, tint)
		mi.material_override = mats[tex_path]
		mi.set_meta("no_style", true)
		var pos: Vector3 = layout.actor_position({"pos_x": rec.header.pos_x.value, "pos_z": rec.header.pos_z.value})
		pos.y += float(field(rec, "height_offset", 0.0)) * U + 0.02
		mi.position = pos
		# Same yaw mapping as level_layout (90 - BAM degrees); x/z tilt as decoded.
		mi.rotation_degrees = Vector3(float(field(rec, "rotation_x", 0.0)),
			90.0 - float(field(rec, "rotation_y", 0.0)), float(field(rec, "rotation_z", 0.0)))
		mi.set_meta("actor_id", int(rec.id))
		root.add_child(mi)
	return root


static func _quad(w: float, h: float, uv: Array, tint: Color) -> ArrayMesh:
	# Flat on XZ, centred; uv = [u0, v0, u1, v1] into the texture page.
	var v := PackedVector3Array([Vector3(-w / 2, 0, -h / 2), Vector3(w / 2, 0, -h / 2),
		Vector3(w / 2, 0, h / 2), Vector3(-w / 2, 0, h / 2)])
	var t := PackedVector2Array([Vector2(uv[0], uv[1]), Vector2(uv[2], uv[1]),
		Vector2(uv[2], uv[3]), Vector2(uv[0], uv[3])])
	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = PackedVector3Array([v[0], v[1], v[2], v[0], v[2], v[3]])
	arrays[Mesh.ARRAY_TEX_UV] = PackedVector2Array([t[0], t[1], t[2], t[0], t[2], t[3]])
	arrays[Mesh.ARRAY_NORMAL] = PackedVector3Array([Vector3.UP, Vector3.UP, Vector3.UP, Vector3.UP, Vector3.UP, Vector3.UP])
	arrays[Mesh.ARRAY_COLOR] = PackedColorArray([tint, tint, tint, tint, tint, tint])
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	return mesh


# ─── Trigger zones ────────────────────────────────────────────────────────────

static func build_zones(decoded: Array, actor_nodes: Dictionary) -> Node3D:
	var root := Node3D.new()
	root.name = "Zones"
	for rec: Dictionary in decoded:
		var cls := int(rec.class_id)
		if not ZONE_COLORS.has(cls):
			continue
		var anchor: Node3D = actor_nodes.get(int(rec.id))
		var mesh: Mesh
		var lift := 0.0
		if cls == 95:
			var box := BoxMesh.new()
			box.size = Vector3(float(field(rec, "width", 0.1)), float(field(rec, "height", 0.1)),
				float(field(rec, "length", 0.1))) * U
			mesh = box
			lift = box.size.y / 2.0
		elif cls == 14:
			var cyl := CylinderMesh.new()
			cyl.top_radius = float(field(rec, "radius", 0.1)) * U
			cyl.bottom_radius = cyl.top_radius
			cyl.height = 1.0
			mesh = cyl
			lift = 0.5
		else:
			# Attached to another actor (and one of its bones, which we can't place).
			var target := str(field(rec, "attach_actor", "")).trim_prefix("actor ")
			if target.is_valid_int() and actor_nodes.has(int(target)):
				anchor = actor_nodes[int(target)]
			var box := BoxMesh.new()
			box.size = Vector3(float(field(rec, "size_x", 0.05)), float(field(rec, "size_y", 0.05)),
				float(field(rec, "size_z", 0.05))) * U
			mesh = box
			lift = box.size.y / 2.0
		if anchor == null:
			continue
		var mi := MeshInstance3D.new()
		# Big area triggers (some cover whole sections and overlap heavily) get edges
		# only; stacked fills would tint the whole view.
		var footprint := mesh.get_aabb().size
		if footprint.x * footprint.z <= 64.0:
			mi.mesh = mesh
		var fill := _flat(ZONE_COLORS[cls])
		fill.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
		# Outside faces only: from inside a (possibly huge, overlapping) zone you
		# see just its edges instead of a wash of stacked fills.
		fill.cull_mode = BaseMaterial3D.CULL_BACK
		mi.material_override = fill
		mi.set_meta("no_style", true)
		mi.set_meta("actor_id", int(rec.id))
		mi.transform = anchor.transform
		mi.position += Vector3(0, lift, 0)
		var edges := MeshInstance3D.new()
		edges.mesh = _outline(mesh.get_aabb())
		var line := _flat(Color(ZONE_COLORS[cls], 0.9))
		edges.material_override = line
		edges.set_meta("no_style", true)
		mi.add_child(edges)
		root.add_child(mi)
	return root


static func _outline(b: AABB) -> ImmediateMesh:
	## The 12 edges of a box (cylinders get their bounding box).
	var im := ImmediateMesh.new()
	im.surface_begin(Mesh.PRIMITIVE_LINES)
	for i in 12:
		var e: Array = [[0, 1], [1, 3], [3, 2], [2, 0], [4, 5], [5, 7], [7, 6], [6, 4], [0, 4], [1, 5], [2, 6], [3, 7]][i]
		im.surface_add_vertex(b.get_endpoint(e[0]))
		im.surface_add_vertex(b.get_endpoint(e[1]))
	im.surface_end()
	return im


# ─── Ranges ───────────────────────────────────────────────────────────────────

static func ranges_of(rec: Dictionary) -> Array:
	## [[radius_godot, Color], ...] for the actor's engage/look ranges.
	var out := []
	var engage: Variant = field(rec, "engage_range")
	var look: Variant = field(rec, "look_range")
	if engage != null and float(engage) > 0.0:
		out.append([float(engage) * U, Color(1.0, 0.25, 0.2)])
	if look != null and float(look) > 0.0 and look != engage:
		out.append([float(look) * U, Color(1.0, 0.65, 0.2)])
	return out


static func build_ranges(decoded: Array, actor_nodes: Dictionary, only_id: int = -1) -> MeshInstance3D:
	var im := ImmediateMesh.new()
	im.surface_begin(Mesh.PRIMITIVE_LINES)
	var any := false
	for rec: Dictionary in decoded:
		if only_id >= 0 and int(rec.id) != only_id:
			continue
		var node: Node3D = actor_nodes.get(int(rec.id))
		if node == null:
			continue
		for r: Array in ranges_of(rec):
			any = true
			im.surface_set_color(r[1])
			var c := node.position + Vector3(0, 0.15, 0)
			for i in 48:
				var a0 := TAU * i / 48.0
				var a1 := TAU * (i + 1) / 48.0
				im.surface_add_vertex(c + Vector3(cos(a0), 0, sin(a0)) * r[0])
				im.surface_add_vertex(c + Vector3(cos(a1), 0, sin(a1)) * r[0])
	if not any:
		im.surface_add_vertex(Vector3.ZERO)
		im.surface_add_vertex(Vector3.ZERO)
	im.surface_end()
	var mi := MeshInstance3D.new()
	mi.name = "Ranges"
	mi.mesh = im
	mi.material_override = _line_material()
	mi.set_meta("no_style", true)
	return mi


# ─── Terrain height layers ────────────────────────────────────────────────────

static func build_height_layers(hm: Variant) -> MeshInstance3D:
	## Ctil height channels as points: R/G/B = channels 0/1/2 (-128 = no surface).
	## Grid position mirrors level_layout.sample_y: tile[i][j] is at
	## x = (col - grid_w/2)*16 - 8.5 + j, z = (row - grid_h/2)*16 - 8.5 + i.
	var im := ImmediateMesh.new()
	im.surface_begin(Mesh.PRIMITIVE_POINTS)
	var colors := [Color(1, 0.3, 0.3), Color(0.3, 1, 0.3), Color(0.4, 0.6, 1)]
	var any := false
	if hm is Dictionary:
		var gw := float(hm.grid_w)
		var gh := float(hm.grid_h)
		for key: String in hm.tiles:
			var rc := key.split(",")
			var row := int(rc[0])
			var col := int(rc[1])
			var tile: Array = hm.tiles[key]
			for i in tile.size():
				for j in tile[i].size():
					for ch in 3:
						var v := int(tile[i][j][ch])
						if v == -128:
							continue
						any = true
						im.surface_set_color(colors[ch])
						im.surface_add_vertex(Vector3((col - gw / 2.0) * 16.0 - 8.5 + j, v / 32.0 + 0.05,
							(row - gh / 2.0) * 16.0 - 8.5 + i))
	if not any:
		im.surface_add_vertex(Vector3.ZERO)
	im.surface_end()
	var mi := MeshInstance3D.new()
	mi.name = "HeightLayers"
	mi.mesh = im
	var m := _line_material()
	m.use_point_size = true
	m.point_size = 3.0
	m.no_depth_test = false
	mi.material_override = m
	mi.set_meta("no_style", true)
	return mi


# ─── Wiring ───────────────────────────────────────────────────────────────────

static func build_wiring(decoded: Array, rpns: Variant, fun: Variant) -> Dictionary:
	## {edges: [{from, to, label, kind}], functions: [...]}
	## kind: "script" (actor script → actor), "level" (level function → actor; from = -1 - fn index),
	##       "trigger" (triggering actor → trigger), "attach" (box → attached actor).
	var edges := []
	if rpns is Dictionary:
		for s: Dictionary in rpns.get("rpns", {}).get("scripts", []):
			for owner: Dictionary in s.get("referenced_by", []):
				for r: Dictionary in s.get("refs", []):
					if r.get("kind") == "actor" and r.get("exists", false):
						edges.append({"from": int(owner.id), "to": int(r.id), "kind": "script",
							"label": "%s → %s" % [owner.get("name", "script"), r.get("message", "msg")]})
	var functions := []
	if fun is Dictionary:
		for table: Dictionary in fun.get("cfun", []):
			for fn: Dictionary in table.get("functions", []):
				functions.append(fn)
				for part in ["condition", "action"]:
					for r: Dictionary in fn.get(part, {}).get("refs", []):
						if r.get("kind") == "actor" and r.get("exists", false):
							edges.append({"from": -1 - int(fn.index), "to": int(r.id), "kind": "level",
								"label": "level fn %d %s → %s" % [fn.index, part, r.get("message", "msg")]})
	for rec: Dictionary in decoded:
		var trig := str(field(rec, "triggering_actor", ""))
		if int(rec.class_id) == 95 and trig.begins_with("actor "):
			edges.append({"from": int(trig.trim_prefix("actor ")), "to": int(rec.id), "kind": "trigger",
				"label": "enters trigger"})
		var att := str(field(rec, "attach_actor", ""))
		if att.begins_with("actor "):
			edges.append({"from": int(rec.id), "to": int(att.trim_prefix("actor ")), "kind": "attach",
				"label": "attached to"})
	return {"edges": edges, "functions": functions}


static func build_wiring_lines(edges: Array, actor_nodes: Dictionary, focus: int = -2) -> MeshInstance3D:
	## All actor→actor edges (focus = -2), or only those touching one actor:
	## outgoing orange, incoming cyan. Level-function edges have no source position.
	var im := ImmediateMesh.new()
	im.surface_begin(Mesh.PRIMITIVE_LINES)
	var any := false
	for e: Dictionary in edges:
		if focus != -2 and e.from != focus and e.to != focus:
			continue
		var a: Node3D = actor_nodes.get(e.from)
		var b: Node3D = actor_nodes.get(e.to)
		if a == null or b == null or a == b:
			continue
		var c := Color(0.6, 0.6, 0.7, 0.6)
		if focus != -2:
			c = Color(1.0, 0.6, 0.1) if e.from == focus else Color(0.2, 0.9, 1.0)
		im.surface_set_color(c)
		im.surface_add_vertex(a.position + Vector3(0, 0.6, 0))
		im.surface_set_color(c)
		im.surface_add_vertex(b.position + Vector3(0, 0.6, 0))
		any = true
	if not any:
		im.surface_add_vertex(Vector3.ZERO)
		im.surface_add_vertex(Vector3.ZERO)
	im.surface_end()
	var mi := MeshInstance3D.new()
	mi.name = "Wiring"
	mi.mesh = im
	mi.material_override = _line_material()
	mi.set_meta("no_style", true)
	return mi
