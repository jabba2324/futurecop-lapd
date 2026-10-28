## Top-down minimap for the viewer's Level view: the heightmap as a shaded
## terrain image, every actor's decoded map icon (BaseEntity.map_icon colour and
## shape; NeutralTurret uses map_icon_a), and the camera's position. Clicking
## moves the camera there (the `picked` signal carries the world x/z).
extends Control

signal picked(world: Vector3)

const ICON_COLORS := {
	"red": Color(1, 0.15, 0.15), "dark_red": Color(0.6, 0.05, 0.05), "red_pulse": Color(1, 0.3, 0.3),
	"green": Color(0.2, 1, 0.3), "flashing_green": Color(0.5, 1, 0.5), "blue": Color(0.3, 0.5, 1),
	"cyan": Color(0.2, 1, 1), "yellow": Color(1, 0.95, 0.2), "gold": Color(1, 0.75, 0.2),
}

var _image: ImageTexture
var _img_origin := Vector2.ZERO # world x/z of the image's top-left (whole heightmap grid)
var _img_extent := Vector2.ONE  # world size covered by the image
var _origin := Vector2.ZERO     # world x/z of the shown area's top-left (the play area)
var _extent := Vector2.ONE      # world size of the shown area
var _icons: Array = []          # [world Vector3, Color, shape, flashing]
var _camera := Vector3.ZERO
var _time := 0.0


func setup(hm: Variant, icons: Array) -> void:
	## hm = heightmap.json; icons = [[world position, {"color", "shape"}], ...].
	_icons.clear()
	for e: Array in icons:
		var icon: Dictionary = e[1]
		var col: String = icon.get("color", "")
		_icons.append([e[0], ICON_COLORS.get(col, Color.WHITE), icon.get("shape", "circle"),
			col.begins_with("flashing") or col.ends_with("pulse")])
	_image = null
	if not hm is Dictionary:
		return
	var gw := int(hm.grid_w)
	var gh := int(hm.grid_h)
	# Same grid placement as level_overlays.build_height_layers / level_layout.sample_y.
	_img_origin = Vector2(-gw / 2.0 * 16.0 - 8.5, -gh / 2.0 * 16.0 - 8.5)
	_img_extent = Vector2(gw * 16.0 + 1.0, gh * 16.0 + 1.0)
	# Show the play area (where the icons are) rather than the border filler.
	_origin = _img_origin
	_extent = _img_extent
	if _icons.size() > 1:
		var lo := Vector2(INF, INF)
		var hi := -lo
		for e: Array in _icons:
			lo = Vector2(minf(lo.x, e[0].x), minf(lo.y, e[0].z))
			hi = Vector2(maxf(hi.x, e[0].x), maxf(hi.y, e[0].z))
		var side := maxf(hi.x - lo.x, hi.y - lo.y) + 16.0
		_origin = (lo + hi) / 2.0 - Vector2(side, side) / 2.0
		_extent = Vector2(side, side)
	var img := Image.create(gw * 16 + 1, gh * 16 + 1, false, Image.FORMAT_RGB8)
	img.fill(Color(0.08, 0.09, 0.11))
	for key: String in hm.tiles:
		var rc := key.split(",")
		var row := int(rc[0])
		var col := int(rc[1])
		var tile: Array = hm.tiles[key]
		for i in tile.size():
			for j in tile[i].size():
				var best := -1000
				for ch in 3:
					var v := int(tile[i][j][ch])
					if v != -128:
						best = maxi(best, v)
				if best == -1000:
					continue
				var h := clampf((best / 32.0 + 4.0) / 10.0, 0.0, 1.0)
				img.set_pixel(clampi(col * 16 + j, 0, gw * 16), clampi(row * 16 + i, 0, gh * 16),
					Color(0.25 + 0.5 * h, 0.27 + 0.5 * h, 0.3 + 0.45 * h))
	_image = ImageTexture.create_from_image(img)
	queue_redraw()


func set_camera(world: Vector3) -> void:
	_camera = world


func _process(delta: float) -> void:
	if visible:
		_time += delta
		queue_redraw()


func _to_map(world: Vector3) -> Vector2:
	var uv := (Vector2(world.x, world.z) - _origin) / _extent
	return _rect().position + uv * _rect().size


func _rect() -> Rect2:
	# Keep the map's aspect inside the control.
	var aspect := _extent.x / _extent.y
	var s := size
	var w := minf(s.x, s.y * aspect)
	var h := w / aspect
	return Rect2((s - Vector2(w, h)) / 2.0, Vector2(w, h))


func _draw() -> void:
	draw_rect(Rect2(Vector2.ZERO, size), Color(0, 0, 0, 0.55))
	if _image:
		var src := Rect2((_origin - _img_origin), _extent)   # image pixels = world units
		draw_texture_rect_region(_image, _rect(), src)
	for e: Array in _icons:
		if e[3] and fmod(_time, 0.8) < 0.4:
			continue
		var p := _to_map(e[0])
		if e[2] == "triangle":
			draw_colored_polygon(PackedVector2Array([p + Vector2(0, -4), p + Vector2(3.5, 3), p + Vector2(-3.5, 3)]), e[1])
		else:
			draw_circle(p, 2.5, e[1])
	var c := _to_map(_camera)
	draw_arc(c, 6.0, 0, TAU, 16, Color.WHITE, 1.5)
	draw_rect(_rect(), Color(1, 1, 1, 0.3), false)


func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		var r := _rect()
		if r.has_point(event.position):
			var uv: Vector2 = (event.position - r.position) / r.size
			var w: Vector2 = _origin + uv * _extent
			picked.emit(Vector3(w.x, _camera.y, w.y))
			accept_event()
