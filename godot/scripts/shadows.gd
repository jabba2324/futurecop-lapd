## Blob shadows, as SLUS_007.39 draws them (0x80062eb8 → 0x800164f4): under every
## actor with entity flag 0x80000 (has_shadow) and under the X1 (0x8005920c sets it on
## the legs). level_ambience.gd adds one node for the level.
##   - shape: an octagon fan around the model's box centre, half-extents 1.25 × the
##     box's in x and z, the four axis points plus four at 0.75 × both (1.5 × the edge
##     midpoints), turning with the actor's yaw, flat at one ground height
##   - colour: centre I = 96 − 12 h (h = height above the ground in Godot units), rim
##     0; subtractive with a 16/31 grey texel, so the ground darkens by up to
##     0.52 × I / 128 (0.39 on the ground), fading out by 8 units up
##   - ground: the floor under the actor (0x80066f78), one height, not draped
## The game uses the model's first collision box; this uses its mesh bounds.
extends Node3D

const LIFT := 0.01          # above the floor, against z-fighting
const GREY := 16.0 / 31.0

var _static: Array = []     # [actor, shadow]: placed once
var _moving: Array = []     # [actor, shadow]: the X1 and elevators, every frame
var _material: StandardMaterial3D
var _floor: Callable        # (Vector3) -> floor height at or below it, or NAN


func setup(actors: Array, moving: Array, floor_at: Callable) -> void:
	name = "Shadows"
	_floor = floor_at
	_material = StandardMaterial3D.new()
	_material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_material.blend_mode = BaseMaterial3D.BLEND_MODE_SUB
	_material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	_material.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
	_material.cull_mode = BaseMaterial3D.CULL_DISABLED
	_material.vertex_color_use_as_albedo = true
	for a: Node3D in actors:
		var s := _make(a)
		if s:
			(_moving if a in moving else _static).append([a, s])
			_place(a, s)


func _make(actor: Node3D) -> MeshInstance3D:
	var box := AABB()
	var inv := actor.global_transform.affine_inverse()
	for mi: MeshInstance3D in actor.find_children("*", "MeshInstance3D", true, false):
		if mi.mesh == null or not mi.is_visible_in_tree() or mi.has_meta("shadow"):
			continue
		var b: AABB = (inv * mi.global_transform) * mi.get_aabb()
		box = b if box.size == Vector3.ZERO else box.merge(b)
	if box.size.x <= 0.0 or box.size.z <= 0.0:
		return null
	var c := box.get_center()
	var ex := box.size.x / 2.0 * 1.25
	var ez := box.size.z / 2.0 * 1.25
	var rim := [Vector2(ex, 0), Vector2(0.75 * ex, 0.75 * ez), Vector2(0, ez), Vector2(-0.75 * ex, 0.75 * ez),
		Vector2(-ex, 0), Vector2(-0.75 * ex, -0.75 * ez), Vector2(0, -ez), Vector2(0.75 * ex, -0.75 * ez)]
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for i in 8:
		var a: Vector2 = rim[i]
		var b: Vector2 = rim[(i + 1) % 8]
		st.set_color(Color.WHITE)
		st.add_vertex(Vector3(c.x, 0, c.z))
		st.set_color(Color.BLACK)
		st.add_vertex(Vector3(c.x + a.x, 0, c.z + a.y))
		st.add_vertex(Vector3(c.x + b.x, 0, c.z + b.y))
	var s := MeshInstance3D.new()
	s.name = "Shadow"
	s.set_meta("shadow", true)
	s.mesh = st.commit()
	s.material_override = _material.duplicate()
	s.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	s.top_level = true
	actor.add_child(s)
	return s


func _place(actor: Node3D, s: MeshInstance3D) -> void:
	var at := actor.global_position
	var ground: float = _floor.call(at)
	if is_nan(ground):
		s.visible = false
		return
	var h := at.y - ground
	var i := 96.0 - 12.0 * maxf(h, 0.0)
	s.visible = i > 0.0 and actor.is_visible_in_tree()
	var yaw := actor.global_basis.get_euler().y
	s.global_transform = Transform3D(Basis(Vector3.UP, yaw), Vector3(at.x, ground + LIFT, at.z))
	var k := GREY * i / 128.0
	(s.material_override as StandardMaterial3D).albedo_color = Color(k, k, k, 1.0)


func _physics_process(_delta: float) -> void:
	for m: Array in _moving:
		_place(m[0], m[1])
