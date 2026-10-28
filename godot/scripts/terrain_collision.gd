## Builds collision for a generated level at load time, so the scene files stay
## small: the stitched terrain becomes one concave shape (both faces collide,
## because the PS1 walls are single-sided), and each placed prop under
## "Actors" gets a convex hull of its meshes. Attached to the scene's Terrain node.
extends Node3D

@export var props_root: NodePath = ^"../Actors"
## Nodes under props_root in these groups are skipped. The player (a
## CharacterBody3D) is always skipped: its own capsule is its collision, and this
## runs before player.gd's _ready, so group membership can't be relied on here.
@export var skip_groups: PackedStringArray = ["player"]


func _ready() -> void:
	var terrain := get_node_or_null("TerrainMesh") as MeshInstance3D
	if terrain and terrain.mesh:
		var shape := terrain.mesh.create_trimesh_shape() as ConcavePolygonShape3D
		shape.backface_collision = true
		_add_body(self, "TerrainCollision", shape, Transform3D.IDENTITY)

	var actors := get_node_or_null(props_root)
	if actors == null:
		return
	for actor: Node in actors.get_children():
		if not actor is Node3D or actor is CharacterBody3D or _skipped(actor):
			continue
		for mi: MeshInstance3D in actor.find_children("*", "MeshInstance3D", true, false):
			if mi.mesh == null or mi.get_aabb().size.length() < 0.05:
				continue
			# Convex hull per prop mesh: cheap and good enough to walk around.
			var hull := mi.mesh.create_convex_shape(true, true)
			_add_body(actor, "Collision", hull, (actor as Node3D).global_transform.affine_inverse() * mi.global_transform)


func _skipped(node: Node) -> bool:
	for g in skip_groups:
		if node.is_in_group(g):
			return true
	return false


func _add_body(parent: Node, name: String, shape: Shape3D, xform: Transform3D) -> void:
	var body := StaticBody3D.new()
	body.name = name
	var cs := CollisionShape3D.new()
	cs.shape = shape
	cs.transform = xform
	body.add_child(cs)
	parent.add_child(body)
