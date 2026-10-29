## Builds collision for a generated level at load time, so the scene files stay
## small: the stitched terrain becomes one concave shape (both faces collide,
## because the PS1 walls are single-sided), and each placed prop under
## "Actors" gets a convex hull of its meshes' solid (non-additive) faces, unless the
## actor has BaseEntity disable_collision. Attached to the scene's Terrain node.
## Terrain surfaces whose material ends in "_pass" are left out: floors the game's
## ground probe ignores (surface byte 0x40, extract_terrain.py). Faces from "_see"
## materials (surface bit 0x80) are listed in see_through, by collision face index,
## for the camera's line-of-sight test (player.gd).
extends Node3D

@export var props_root: NodePath = ^"../Actors"
## Nodes under props_root in these groups are skipped. The player (a
## CharacterBody3D) is always skipped: its own capsule is its collision, and this
## runs before player.gd's _ready, so group membership can't be relied on here.
@export var skip_groups: PackedStringArray = ["player"]

var see_through := PackedByteArray()   # per TerrainCollision face: 1 = camera sees through
var terrain_body: StaticBody3D


func _ready() -> void:
	var terrain := get_node_or_null("TerrainMesh") as MeshInstance3D
	if terrain and terrain.mesh:
		var shape := ConcavePolygonShape3D.new()
		shape.set_faces(_solid_faces(terrain.mesh))
		shape.backface_collision = true
		terrain_body = _add_body(self, "TerrainCollision", shape, Transform3D.IDENTITY)

	var actors := get_node_or_null(props_root)
	if actors == null:
		return
	for actor: Node in actors.get_children():
		if not actor is Node3D or actor is CharacterBody3D or _skipped(actor) \
				or actor.get_meta("no_collision", false):
			continue   # no_collision: BaseEntity disable_collision (generate_scene.gd)
		for mi: MeshInstance3D in actor.find_children("*", "MeshInstance3D", true, false):
			if mi.mesh == null or mi.get_aabb().size.length() < 0.05 or not mi.is_visible_in_tree():
				continue   # hidden: wreck models (generate_scene.gd) collide only once shown
			# Convex hull per prop mesh, cheap and good enough to walk around, of its
			# solid faces only: additive ones ("_add") are light (beams, glows).
			var hull := solid_hull(mi)
			if hull:
				_add_body(actor, "Collision", hull, (actor as Node3D).global_transform.affine_inverse() * mi.global_transform)


static func solid_hull(mi: MeshInstance3D) -> ConvexPolygonShape3D:
	## Convex hull of a mesh's non-additive surfaces, or null if it has none.
	var pts := PackedVector3Array()
	for s in mi.mesh.get_surface_count():
		var mat := mi.get_active_material(s)
		if mat and mat.resource_name.contains("_add"):
			continue
		pts.append_array(mi.mesh.surface_get_arrays(s)[Mesh.ARRAY_VERTEX])
	if pts.size() < 4:
		return null
	var hull := ConvexPolygonShape3D.new()
	hull.points = pts   # the shape keeps their convex hull
	return hull


func _solid_faces(mesh: Mesh) -> PackedVector3Array:
	var faces := PackedVector3Array()
	for s in mesh.get_surface_count():
		var mat := mesh.surface_get_material(s)
		if mat and mat.resource_name.ends_with("_pass"):
			continue
		var arrays := mesh.surface_get_arrays(s)
		var verts: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
		var idx: PackedInt32Array = arrays[Mesh.ARRAY_INDEX] if arrays[Mesh.ARRAY_INDEX] else PackedInt32Array()
		var before := faces.size()
		if idx.is_empty():
			faces.append_array(verts)
		else:
			for i in idx:
				faces.append(verts[i])
		var see := 1 if mat and mat.resource_name.contains("_see") else 0
		for f in (faces.size() - before) / 3:
			see_through.append(see)
	return faces


func is_see_through(face_index: int) -> bool:
	return face_index >= 0 and face_index < see_through.size() and see_through[face_index] == 1


func _skipped(node: Node) -> bool:
	for g in skip_groups:
		if node.is_in_group(g):
			return true
	return false


func _add_body(parent: Node, name: String, shape: Shape3D, xform: Transform3D) -> StaticBody3D:
	var body := StaticBody3D.new()
	body.name = name
	var cs := CollisionShape3D.new()
	cs.shape = shape
	cs.transform = xform
	body.add_child(cs)
	parent.add_child(body)
	return body
