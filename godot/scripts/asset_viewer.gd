## Asset browser for everything the extractors write to parsed/<LEVEL>/ (and
## parsed/video/). Loads straight from disk at runtime, so every level is
## viewable without copying it into res://assets/ or running an import.
##
## Run:  godot --path godot res://scenes/asset_viewer.tscn
## Optional user args (after `--`), handy for scripted screenshots:
##   --level HK  --tab Models  --item cobj015  --yaw 30 --pitch 20  --dist 4
##   --anim 0  --seek 0.5  --shot /abs/out.png  --parsed /abs/parsed
##
## Categories: Models (models/*.glb with animations, else objects/*.obj),
## Terrain, Textures, Particles, Fonts, Level (terrain + every actor laid out
## by level_layout.gd; click an actor to inspect it), Audio (music/sfx/voices),
## Video (parsed/video/*.ogv) and Data (any *.json, as a tree).
##
## Controls: left-drag orbit, right/middle-drag pan, wheel zoom, F reframe,
## click (no drag) in Level view selects the nearest actor.
extends Node3D

const ObjLoader := preload("res://scripts/obj_loader.gd")
const LevelLayout := preload("res://scripts/level_layout.gd")

const TABS := ["Models", "Terrain", "Textures", "Images", "Fonts", "Level", "Audio", "Video", "Data"]
const PANEL_WIDTH := 360
const IMAGE_DIRS := ["particles", "loading", "anims", "frontend", "frontend/sprites"]
const MEDIA_TABS := ["Textures", "Images", "Fonts", "Audio", "Video", "Data"]

var _parsed := ""
var _level := ""
var _tab := "Models"
var _entries: Array = []          # [{label, key}] for the current tab
var _actors: Array = []           # parsed actors.json of the current level
var _decoded: Dictionary = {}     # actor id -> actors_decoded.json record (if present)
var _obj_users: Dictionary = {}   # "cobj015.obj" -> [actor dicts]
var _tex_users: Dictionary = {}   # "cbmp009.png" -> ["objects/cobj015", ...]
var _meshes: Dictionary = {}      # abs path -> ArrayMesh (per level)
var _actor_nodes: Dictionary = {} # actor id -> Node3D in Level view
var _nets: Array = []             # nets.json "nets" of the current level
var _net_boxes: Dictionary = {}   # net id -> AABB in world space
var _net_mesh: MeshInstance3D
var _strip: AtlasTexture          # animated CANM frame strip in Images
var _strip_time := 0.0
var _args: Dictionary = {}

# Render options
var _flip_y := true
var _two_sided := true
var _nearest := true
var _lit := true
var _show_paths := true

# Scene
var _cam: Camera3D
var _content: Node3D
var _marker: MeshInstance3D
var _player: AudioStreamPlayer
var _anim: AnimationPlayer
var _target := Vector3.ZERO
var _yaw := deg_to_rad(35.0)
var _pitch := deg_to_rad(25.0)
var _dist := 3.0
var _frame_aabb := AABB(Vector3(-1, -1, -1), Vector3(2, 2, 2))
var _press_pos := Vector2.ZERO

# UI
var _level_btn: OptionButton
var _tab_btn: OptionButton
var _filter: LineEdit
var _list: ItemList
var _info: RichTextLabel
var _opts_row: HFlowContainer
var _media_row: HBoxContainer
var _anim_btn: OptionButton
var _media_pos: Label
var _view: Control
var _tex_view: Control
var _tex_rect: TextureRect
var _font_label: Label
var _video: VideoStreamPlayer
var _video_box: AspectRatioContainer
var _tree: Tree


func _ready() -> void:
	# Must happen before any mesh loads, or wireframe debug draw shows nothing.
	RenderingServer.set_debug_generate_wireframes(true)
	DisplayServer.window_set_title("Future Cop asset viewer")
	_args = _parse_args()
	_parsed = String(_args.get("parsed", ProjectSettings.globalize_path("res://").path_join("../parsed").simplify_path()))
	_build_world()
	_build_ui()

	var levels := _scan_levels()
	for l in levels:
		_level_btn.add_item(l)
	if levels.is_empty():
		_info.text = "No extracted levels found in %s.\nRun the tools/ extractors first." % _parsed
		return

	var idx := maxi(levels.find(String(_args.get("level", "CONFT")).to_upper()), 0)
	_level_btn.select(idx)
	if _args.has("tab"):
		_tab = TABS[maxi(TABS.find(String(_args.tab).capitalize()), 0)]
		_tab_btn.select(TABS.find(_tab))
	if _args.has("yaw"):
		_yaw = deg_to_rad(float(_args.yaw))
	if _args.has("pitch"):
		_pitch = deg_to_rad(float(_args.pitch))
	_load_level(levels[idx])

	if _args.has("item"):
		for i in _list.item_count:
			if String(_args.item).to_lower() in _list.get_item_text(i).to_lower():
				_list.select(i)
				_list.ensure_current_is_visible()
				_on_item_selected(i)
				break
	if _args.has("dist"):
		_dist = float(_args.dist)
		_update_camera()
	if _args.has("anim") and _anim_btn.item_count > 0:
		_anim_btn.select(clampi(int(_args.anim), 0, _anim_btn.item_count - 1))
		_play_anim(_anim_btn.selected)
	if _args.has("shot"):
		_take_screenshot(String(_args.shot))


func _parse_args() -> Dictionary:
	var out := {}
	var a := OS.get_cmdline_user_args()
	var i := 0
	while i < a.size():
		if a[i].begins_with("--") and i + 1 < a.size():
			out[a[i].substr(2)] = a[i + 1]
			i += 2
		else:
			i += 1
	return out


func _take_screenshot(path: String) -> void:
	for _i in 20:
		await get_tree().process_frame
	if _anim and _args.has("seek"):
		_anim.seek(float(_args.seek) * _anim.current_animation_length, true)
		_anim.pause()
		for _i in 3:
			await get_tree().process_frame
	get_viewport().get_texture().get_image().save_png(path)
	print("Saved screenshot → ", path)
	get_tree().quit()


# ─── World ────────────────────────────────────────────────────────────────────

func _build_world() -> void:
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-50, 30, 0)
	sun.light_energy = 1.5
	sun.shadow_enabled = true
	add_child(sun)

	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.16, 0.18, 0.22)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.55, 0.58, 0.65)
	env.ambient_light_energy = 0.8
	var we := WorldEnvironment.new()
	we.environment = env
	add_child(we)

	_cam = Camera3D.new()
	_cam.fov = 50
	add_child(_cam)
	_cam.make_current()

	_content = Node3D.new()
	add_child(_content)

	_player = AudioStreamPlayer.new()
	add_child(_player)

	var beam := CylinderMesh.new()
	beam.top_radius = 0.12
	beam.bottom_radius = 0.12
	beam.height = 14.0
	beam.material = _flat_material(Color(1.0, 0.85, 0.1, 0.55))
	_marker = MeshInstance3D.new()
	_marker.mesh = beam
	_marker.visible = false
	add_child(_marker)


func _flat_material(c: Color) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	m.albedo_color = c
	if c.a < 1.0:
		m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	return m


func _clear_content() -> void:
	for c in _content.get_children():
		_content.remove_child(c)
		c.queue_free()
	_anim = null
	_actor_nodes = {}
	_marker.visible = false


# ─── UI ───────────────────────────────────────────────────────────────────────

func _build_ui() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var root := HBoxContainer.new()
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	root.add_theme_constant_override("separation", 0)
	layer.add_child(root)

	var panel := PanelContainer.new()
	panel.custom_minimum_size.x = PANEL_WIDTH
	root.add_child(panel)
	var col := VBoxContainer.new()
	panel.add_child(col)

	var top := HBoxContainer.new()
	col.add_child(top)
	_level_btn = OptionButton.new()
	_level_btn.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_level_btn.item_selected.connect(func(i: int) -> void: _load_level(_level_btn.get_item_text(i)))
	top.add_child(_level_btn)
	_tab_btn = OptionButton.new()
	_tab_btn.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	for t in TABS:
		_tab_btn.add_item(t)
	_tab_btn.item_selected.connect(func(i: int) -> void:
		_tab = TABS[i]
		_refresh_tab())
	top.add_child(_tab_btn)

	_filter = LineEdit.new()
	_filter.placeholder_text = "Filter (e.g. cobj01, class 36)"
	_filter.clear_button_enabled = true
	_filter.text_changed.connect(func(_t: String) -> void: _fill_list())
	col.add_child(_filter)

	var split := VSplitContainer.new()
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	col.add_child(split)
	_list = ItemList.new()
	_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_list.item_selected.connect(_on_item_selected)
	split.add_child(_list)
	_info = RichTextLabel.new()
	_info.bbcode_enabled = true
	_info.selection_enabled = true
	_info.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_info.size_flags_stretch_ratio = 0.9
	split.add_child(_info)

	_media_row = HBoxContainer.new()
	col.add_child(_media_row)
	_anim_btn = OptionButton.new()
	_anim_btn.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_anim_btn.item_selected.connect(_play_anim)
	_media_row.add_child(_anim_btn)
	var play := Button.new()
	play.text = "Play"
	play.pressed.connect(_media_play)
	_media_row.add_child(play)
	var stop := Button.new()
	stop.text = "Stop"
	stop.pressed.connect(_media_stop)
	_media_row.add_child(stop)
	_media_pos = Label.new()
	_media_row.add_child(_media_pos)

	_opts_row = HFlowContainer.new()
	col.add_child(_opts_row)
	_add_toggle("Flip Y", _flip_y, func(on: bool) -> void:
		_flip_y = on
		_reselect())
	_add_toggle("Two-sided", _two_sided, func(on: bool) -> void:
		_two_sided = on
		_style_meshes(_content))
	_add_toggle("Nearest", _nearest, func(on: bool) -> void:
		_nearest = on
		_style_meshes(_content))
	_add_toggle("Lit", _lit, func(on: bool) -> void:
		_lit = on
		_style_meshes(_content))
	_add_toggle("Paths", _show_paths, func(on: bool) -> void:
		_show_paths = on
		if is_instance_valid(_net_mesh):
			_net_mesh.visible = on)
	_add_toggle("Wireframe", false, func(on: bool) -> void:
		get_viewport().debug_draw = Viewport.DEBUG_DRAW_WIREFRAME if on else Viewport.DEBUG_DRAW_DISABLED)

	_view = Control.new()
	_view.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_view.mouse_filter = Control.MOUSE_FILTER_STOP
	_view.gui_input.connect(_on_view_input)
	root.add_child(_view)

	_tex_view = Control.new()
	_tex_view.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_tex_view.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_tex_view.visible = false
	_view.add_child(_tex_view)
	var checker := TextureRect.new()
	checker.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	checker.texture = _checker_texture()
	checker.stretch_mode = TextureRect.STRETCH_TILE
	checker.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_tex_view.add_child(checker)
	_tex_rect = TextureRect.new()
	_tex_rect.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT, Control.PRESET_MODE_MINSIZE, 24)
	_tex_rect.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_tex_rect.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_tex_rect.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	_tex_rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_tex_view.add_child(_tex_rect)
	_font_label = Label.new()
	_font_label.set_anchors_and_offsets_preset(Control.PRESET_TOP_WIDE, Control.PRESET_MODE_MINSIZE, 16)
	_font_label.autowrap_mode = TextServer.AUTOWRAP_WORD
	_font_label.add_theme_font_size_override("font_size", 40)
	_font_label.add_theme_color_override("font_outline_color", Color.BLACK)
	_font_label.add_theme_constant_override("outline_size", 8)
	_font_label.visible = false
	_tex_view.add_child(_font_label)

	# FMVs are 320×192; keep that aspect instead of stretching to the view.
	_video_box = AspectRatioContainer.new()
	_video_box.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT, Control.PRESET_MODE_MINSIZE, 24)
	_video_box.ratio = 320.0 / 192.0
	_video_box.visible = false
	_video_box.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_view.add_child(_video_box)
	_video = VideoStreamPlayer.new()
	_video.expand = true
	_video.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_video_box.add_child(_video)

	_tree = Tree.new()
	_tree.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_tree.columns = 2
	_tree.set_column_expand_ratio(0, 1)
	_tree.set_column_expand_ratio(1, 3)
	_tree.visible = false
	_tree.item_collapsed.connect(_on_tree_collapsed)
	_view.add_child(_tree)


func _add_toggle(label: String, on: bool, cb: Callable) -> void:
	var c := CheckBox.new()
	c.text = label
	c.button_pressed = on
	c.toggled.connect(cb)
	_opts_row.add_child(c)


func _checker_texture() -> ImageTexture:
	var img := Image.create(16, 16, false, Image.FORMAT_RGB8)
	for y in 16:
		for x in 16:
			var dark := ((x / 8) + (y / 8)) % 2 == 0
			img.set_pixel(x, y, Color(0.2, 0.2, 0.22) if dark else Color(0.27, 0.27, 0.3))
	return ImageTexture.create_from_image(img)


# ─── Level / tab loading ──────────────────────────────────────────────────────

func _ldir() -> String:
	return _parsed.path_join(_level)


func _scan_levels() -> PackedStringArray:
	var out := PackedStringArray()
	if DirAccess.dir_exists_absolute(_parsed):
		for d in DirAccess.get_directories_at(_parsed):
			if d != "video":
				out.append(d)
	out.sort()
	return out


func _files(dir: String, exts: Array) -> PackedStringArray:
	var out := PackedStringArray()
	if not DirAccess.dir_exists_absolute(dir):
		return out
	for f in DirAccess.get_files_at(dir):
		if f.get_extension().to_lower() in exts:
			out.append(f)
	out.sort()
	return out


func _read_json(path: String) -> Variant:
	var f := FileAccess.open(path, FileAccess.READ)
	return JSON.parse_string(f.get_as_text()) if f else null


func _load_level(level: String) -> void:
	_level = level
	_media_stop()
	_actors = []
	_decoded = {}
	_obj_users = {}
	_tex_users = {}
	_meshes = {}

	var data: Variant = _read_json(_ldir().path_join("actors.json"))
	if data is Dictionary and data.has("actors"):
		_actors = data.actors
	for a in _actors:
		for o in a.get("objects", []):
			if not _obj_users.has(o):
				_obj_users[o] = []
			_obj_users[o].append(a)
	var dec: Variant = _read_json(_ldir().path_join("actors_decoded.json"))
	var recs: Variant = dec.get("actors", []) if dec is Dictionary else dec
	if recs is Array:
		for r in recs:
			if r is Dictionary and r.has("id"):
				_decoded[int(r.id)] = r

	for sub in ["objects", "terrain"]:
		for mtl in _files(_ldir().path_join(sub), ["mtl"]):
			var mf := FileAccess.open(_ldir().path_join(sub).path_join(mtl), FileAccess.READ)
			while mf and not mf.eof_reached():
				var line := mf.get_line().strip_edges()
				if line.begins_with("map_Kd"):
					var tex := line.get_file()
					if not _tex_users.has(tex):
						_tex_users[tex] = []
					_tex_users[tex].append(sub + "/" + mtl.get_basename())
	_refresh_tab()


func _refresh_tab() -> void:
	if _level == "":
		return
	_clear_content()
	_media_stop()
	_entries = []
	var media: bool = _tab in MEDIA_TABS
	_tex_view.visible = _tab in ["Textures", "Images", "Fonts", "Audio"]
	_tex_rect.texture = null
	_font_label.visible = _tab == "Fonts"
	_video_box.visible = _tab == "Video"
	_tree.visible = _tab == "Data"
	_opts_row.visible = not media
	_anim_btn.clear()
	_anim_btn.visible = _tab == "Models"
	_media_row.visible = _tab in ["Models", "Audio", "Video"]
	var d := _ldir()

	match _tab:
		"Models":
			var names := {}
			for f in _files(d.path_join("objects"), ["obj"]):
				names[f.get_basename()] = true
			for f in _files(d.path_join("models"), ["glb"]):
				names[f.get_basename()] = true
			var keys := names.keys()
			keys.sort()
			for n: String in keys:
				var users: Array = _obj_users.get(n + ".obj", [])
				var classes := {}
				for a in users:
					classes[int(a["class"])] = true
				var anim := "  ▶" if FileAccess.file_exists(d.path_join("models/%s.glb" % n)) else ""
				var suffix := "  ×%d  class %s" % [users.size(), ",".join(classes.keys().map(str))] if users else "  (unplaced)"
				_entries.append({"label": n + anim + suffix, "key": n})
		"Terrain":
			for f in _files(d.path_join("terrain"), ["obj"]):
				_entries.append({"label": f.get_basename() + ("  (stitched level)" if f == "map.obj" else ""), "key": f})
		"Textures":
			for f in _files(d.path_join("textures"), ["png"]):
				_entries.append({"label": f.get_basename() + "  used by %d" % _tex_users.get(f, []).size(), "key": "textures/" + f})
		"Images":
			for sub in IMAGE_DIRS:
				for f in _files(d.path_join(sub), ["png"]):
					_entries.append({"label": sub + "/" + f.get_basename(), "key": sub + "/" + f})
		"Fonts":
			for f in _files(d.path_join("fonts"), ["ttf"]):
				_entries.append({"label": f.get_basename(), "key": "fonts/" + f})
		"Level":
			_build_level()
			_entries.append({"label": "(whole level)", "key": null})
			for net: Dictionary in _nets:
				_entries.append({"label": "Cnet %d  path · %d nodes · %d actors" % [net.id, net.node_count, net.get("actors", []).size()], "key": net})
			for a in _actors:
				var objs: Array = a.get("objects", [])
				var name: String = _decoded.get(int(a.id), {}).get("class_name", "")
				var label := "#%d  class %d%s  %s" % [a.id, a["class"], " " + name if name else "",
					"+".join(objs.map(func(o: String) -> String: return o.get_basename().trim_prefix("cobj"))) if objs else "(no mesh)"]
				_entries.append({"label": label, "key": a})
		"Audio":
			for sub in ["music", "sfx", "sfx_tones", "voices"]:
				for f in _files(d.path_join(sub), ["wav"]):
					_entries.append({"label": sub + "/" + f.get_basename(), "key": sub + "/" + f})
		"Video":
			for f in _files(_parsed.path_join("video"), ["ogv"]):
				_entries.append({"label": f.get_basename(), "key": f})
		"Data":
			for sub in ["", "demos", "frontend", "anims"]:
				for f in _files(d.path_join(sub), ["json"]):
					_entries.append({"label": sub.path_join(f), "key": sub.path_join(f)})

	_fill_list()
	_info.text = "[b]%s[/b] — %s\n%d entries.\n\n%s" % [_level if _tab != "Video" else "All levels", _tab, _entries.size(), _tab_help()]
	if _tab == "Level":
		_frame(_frame_aabb)


func _tab_help() -> String:
	match _tab:
		"Models":
			return "Cobj models. ▶ = glTF export (with skeleton/animation when the model has one); others load the OBJ. 'Flip Y' undoes the PS1 Y inversion; the grid is at the model origin."
		"Terrain":
			return "Ctil chunks (±8 units each) and the stitched map. Vertex colours carry the PS1 baked shading — untick 'Lit' to see it unmodified."
		"Textures":
			return "Cbmp texture pages, nearest filtering."
		"Images":
			return "Cpyr particles, loading screens (.LSC), radio-panel animations (SWVR CANM, as frame sheets) and front-end screens/sprites (Cmdc, Ctdt+Ctin, Cmic)."
		"Fonts":
			return "Cfnt fonts: the extracted atlas, plus a sample rendered with the converted TTF."
		"Level":
			return "Terrain + every actor placed with level_layout.gd (the same code generate_scene.gd uses). Actors without a mesh are shown as coloured dots. Click one in the view or the list."
		"Audio":
			return "Music (MGAV), SFX samples (Cvkb, raw at 11025 Hz), sfx_tones (each Cvkh tone at its in-game pitch) and voice clips (BGAV)."
		"Video":
			return "FMV movies decoded by tools/extract_videos.py."
		"Data":
			return "Every JSON the extractors wrote for this level, as an expandable tree."
	return ""


func _fill_list() -> void:
	_list.clear()
	var q := _filter.text.strip_edges().to_lower()
	for e in _entries:
		if q == "" or q in String(e.label).to_lower():
			var i := _list.add_item(e.label)
			_list.set_item_metadata(i, e)


func _reselect() -> void:
	if _tab == "Level":
		_refresh_tab()
		return
	var sel := _list.get_selected_items()
	if sel.size() > 0:
		_on_item_selected(sel[0])


func _on_item_selected(i: int) -> void:
	var e: Dictionary = _list.get_item_metadata(i)
	match _tab:
		"Models":
			_show_model(e.key)
		"Terrain":
			_show_mesh_file(_ldir().path_join("terrain/" + e.key), false, false)
		"Textures", "Images":
			_show_image(e.key)
		"Fonts":
			_show_font(e.key)
		"Level":
			_focus_actor(e.key)
		"Audio":
			_show_audio(e.key)
		"Video":
			_show_video(e.key)
		"Data":
			_show_json(e.key)


# ─── Models / terrain ─────────────────────────────────────────────────────────

func _mesh(path: String) -> ArrayMesh:
	if not _meshes.has(path):
		_meshes[path] = ObjLoader.load_mesh(path) if FileAccess.file_exists(path) else null
	return _meshes[path]


func _show_model(name: String) -> void:
	var glb := _ldir().path_join("models/%s.glb" % name)
	if FileAccess.file_exists(glb):
		_show_gltf(glb)
	else:
		_show_mesh_file(_ldir().path_join("objects/%s.obj" % name), _flip_y, true)
		_info.text += _model_users(name + ".obj")


func _show_mesh_file(path: String, flip: bool, grid: bool) -> void:
	_clear_content()
	var mesh := _mesh(path)
	if mesh == null:
		_info.text = "Could not load %s" % path
		return
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	if flip:
		mi.scale = Vector3(1, -1, 1)
	_content.add_child(mi)
	_style_meshes(mi)
	var aabb := mi.transform * mesh.get_aabb()
	if grid:
		_content.add_child(_make_grid(aabb))
	_frame(aabb)
	_info.text = _mesh_info(path.trim_prefix(_ldir() + "/"), mesh)


func _gltf_scene(path: String) -> Node3D:
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	if doc.append_from_file(path, state) != OK:
		return null
	return doc.generate_scene(state) as Node3D


func _show_gltf(path: String) -> void:
	_clear_content()
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	if doc.append_from_file(path, state) != OK:
		_info.text = "Could not load %s" % path
		return
	var scene := doc.generate_scene(state) as Node3D
	# No Flip Y here: export_gltf.py applies the root-bone rotation that the
	# raw OBJ skips, so .glb models are already upright.
	var holder := Node3D.new()
	holder.add_child(scene)
	_content.add_child(holder)
	_style_meshes(holder)

	var aabb := AABB()
	var first := true
	var verts := 0
	var tris := 0
	for mi: MeshInstance3D in holder.find_children("*", "MeshInstance3D", true, false):
		var b: AABB = mi.global_transform * mi.get_aabb()
		aabb = b if first else aabb.merge(b)
		first = false
		for s in mi.mesh.get_surface_count():
			var n: int = mi.mesh.surface_get_array_len(s)
			var idx: int = mi.mesh.surface_get_array_index_len(s)
			verts += n
			tris += (idx if idx > 0 else n) / 3
	_content.add_child(_make_grid(aabb))
	_frame(aabb)

	var skins := state.get_skins().size()
	var bones := 0
	for sk in state.get_skins():
		bones += (sk as GLTFSkin).joints.size()
	var morphs := 0
	for m in state.get_meshes():
		morphs = maxi(morphs, (m as GLTFMesh).blend_weights.size())
	var players := scene.find_children("*", "AnimationPlayer", true, false)
	_anim = players[0] if players.size() > 0 else null
	var anims := PackedStringArray()
	_anim_btn.clear()
	if _anim:
		for a in _anim.get_animation_list():
			anims.append("%s (%.2fs)" % [a, _anim.get_animation(a).length])
			_anim_btn.add_item(a)
			_anim.get_animation(a).loop_mode = Animation.LOOP_LINEAR
		if _anim_btn.item_count > 0:
			_play_anim(0)
	var b := aabb
	_info.text = "[b]models/%s[/b] (glTF)\nverts %d · tris %d · skins %d · bones %d · morph targets %d\nsize (%.3f, %.3f, %.3f)\nanimations: %s\n" % [
		path.get_file(), verts, tris, skins, bones, morphs, b.size.x, b.size.y, b.size.z,
		", ".join(anims) if anims else "none"] + _model_users(path.get_file().get_basename() + ".obj")


func _play_anim(i: int) -> void:
	if _anim and i >= 0 and i < _anim_btn.item_count:
		_anim.play(_anim_btn.get_item_text(i))


func _mesh_info(rel: String, mesh: Mesh) -> String:
	var tris := 0
	var mats := PackedStringArray()
	for s in mesh.get_surface_count():
		tris += mesh.surface_get_array_len(s) / 3
		var m := mesh.surface_get_material(s)
		if m is BaseMaterial3D and (m as BaseMaterial3D).albedo_texture:
			mats.append(String((m as BaseMaterial3D).albedo_texture.get_meta("source_path", "?")).get_file())
		else:
			mats.append("untextured")
	var b := mesh.get_aabb()
	return "[b]%s[/b]\nsurfaces %d · tris %d\nAABB min (%.3f, %.3f, %.3f)\n     max (%.3f, %.3f, %.3f)\nsize (%.3f, %.3f, %.3f)\nmaterials: %s\n" % [
		rel, mesh.get_surface_count(), tris,
		b.position.x, b.position.y, b.position.z, b.end.x, b.end.y, b.end.z,
		b.size.x, b.size.y, b.size.z, ", ".join(mats)]


func _model_users(obj: String) -> String:
	var users: Array = _obj_users.get(obj, [])
	if users.is_empty():
		return "\nNot referenced by any actor in this level."
	var lines := PackedStringArray()
	for a in users.slice(0, 12):
		lines.append("#%d class %d  [%s]" % [a.id, a["class"], ", ".join(a.get("objects", []))])
	if users.size() > 12:
		lines.append("… %d more" % (users.size() - 12))
	return "\nUsed by %d actors:\n%s" % [users.size(), "\n".join(lines)]


func _style_meshes(node: Node) -> void:
	var meshes := node.find_children("*", "MeshInstance3D", true, false)
	if node is MeshInstance3D:
		meshes.append(node)
	for mi: MeshInstance3D in meshes:
		if mi.mesh == null or mi.has_meta("no_style"):
			continue
		for s in mi.mesh.get_surface_count():
			var src := mi.mesh.surface_get_material(s)
			var m: StandardMaterial3D = (src as StandardMaterial3D).duplicate() if src is StandardMaterial3D else StandardMaterial3D.new()
			m.cull_mode = BaseMaterial3D.CULL_DISABLED if _two_sided else BaseMaterial3D.CULL_BACK
			m.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST if _nearest else BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS
			m.shading_mode = BaseMaterial3D.SHADING_MODE_PER_PIXEL if _lit else BaseMaterial3D.SHADING_MODE_UNSHADED
			mi.set_surface_override_material(s, m)


func _make_grid(aabb: AABB) -> MeshInstance3D:
	# Grid on y=0 through the model origin; step is a power of ten below the model size.
	var size := maxf(aabb.get_longest_axis_size(), 0.01)
	var step := pow(10.0, floor(log(size) / log(10.0))) / 2.0
	var n := 12
	var im := ImmediateMesh.new()
	im.surface_begin(Mesh.PRIMITIVE_LINES)
	for i in range(-n, n + 1):
		im.surface_set_color(Color(0.45, 0.45, 0.5) if i != 0 else Color(0.8, 0.8, 0.85))
		im.surface_add_vertex(Vector3(i * step, 0, -n * step))
		im.surface_add_vertex(Vector3(i * step, 0, n * step))
		im.surface_add_vertex(Vector3(-n * step, 0, i * step))
		im.surface_add_vertex(Vector3(n * step, 0, i * step))
	# Axes: X red, Y green, Z blue
	for ax in [[Vector3.RIGHT, Color.RED], [Vector3.UP, Color.GREEN], [Vector3.BACK, Color.DODGER_BLUE]]:
		im.surface_set_color(ax[1])
		im.surface_add_vertex(Vector3.ZERO)
		im.surface_add_vertex(ax[0] * step * 3.0)
	im.surface_end()
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.vertex_color_use_as_albedo = true
	var mi := MeshInstance3D.new()
	mi.mesh = im
	mi.material_override = mat
	mi.set_meta("no_style", true)
	return mi


# ─── Images / fonts ───────────────────────────────────────────────────────────

func _show_image(rel: String) -> void:
	var path := _ldir().path_join(rel)
	var tex := ObjLoader.load_texture(path)
	_tex_rect.texture = tex
	if tex == null:
		_info.text = "Could not load " + rel
		return
	var users: Array = _tex_users.get(rel.get_file(), [])
	_info.text = "[b]%s[/b]\n%d × %d · alpha: %s" % [rel, tex.get_width(), tex.get_height(),
		"yes" if tex.get_image().detect_alpha() != Image.ALPHA_NONE else "no"]
	if rel.begins_with("textures/"):
		_info.text += "\nUsed by %d meshes:\n%s" % [users.size(), ", ".join(users)]
	# CANM radio panels are vertical strips of 64x48 frames: play them at the game's 10 fps.
	_strip = null
	if rel.begins_with("anims/") and tex.get_width() == 64 and tex.get_height() % 48 == 0:
		_strip = AtlasTexture.new()
		_strip.atlas = tex
		_strip.region = Rect2(0, 0, 64, 48)
		_strip_time = 0.0
		_tex_rect.texture = _strip
		_info.text += "\n%d frames of 64×48 at 10 fps (CANM updater 0x80029748). The game plays it once; the viewer loops it." % (tex.get_height() / 48)


func _show_font(rel: String) -> void:
	var path := _ldir().path_join(rel)
	var font := FontFile.new()
	if font.load_dynamic_font(path) != OK:
		_info.text = "Could not load " + rel
		return
	_font_label.add_theme_font_override("font", font)
	_font_label.text = "FUTURE COP L.A.P.D.\nThe quick brown fox jumps over the lazy dog\n0123456789 !?.,:;-+()"
	var atlas := path.get_basename() + ".png"
	_tex_rect.texture = ObjLoader.load_texture(atlas) if FileAccess.file_exists(atlas) else null
	var glyphs := font.get_supported_chars().length()
	_info.text = "[b]%s[/b]\n%d glyphs in the TTF.\nTop: sample text in the converted TTF.\nBelow: the original atlas (%s)." % [rel, glyphs, atlas.get_file()]


# ─── Level view ───────────────────────────────────────────────────────────────

func _build_level() -> void:
	_clear_content()
	var d := _ldir()
	var hm := FileAccess.get_file_as_string(d.path_join("heightmap.json"))
	var layout = LevelLayout.new(hm)
	var nets_data: Variant = _read_json(d.path_join("nets.json"))
	layout.nets = nets_data.get("nets", []) if nets_data is Dictionary else []
	var root := Node3D.new()
	_content.add_child(root)
	_frame_aabb = AABB(Vector3(-50, -2, -50), Vector3(100, 10, 100))

	var map := _mesh(d.path_join("terrain/map.obj"))
	if map:
		var mi := MeshInstance3D.new()
		mi.mesh = map
		root.add_child(mi)
		_frame_aabb = map.get_aabb()

	var dot := SphereMesh.new()
	dot.radius = 0.25
	dot.height = 0.5
	for a: Dictionary in _actors:
		var node := Node3D.new()
		node.position = layout.actor_position(a)
		var rot: float = layout.actor_yaw_degrees(a)
		if not is_nan(rot):
			node.rotation_degrees.y = rot
		root.add_child(node)
		_actor_nodes[int(a.id)] = node
		# Weapon definitions (98/99) carry a projectile model but are never placed: show a dot.
		var objects: Array = a.get("objects", []) if LevelLayout.is_placed(a) else []
		var part_yaws: Dictionary = LevelLayout.part_yaw_offsets(a)
		var offsets: Dictionary = LevelLayout.part_y_offsets(a, func(obj: String) -> Variant:
			var m := _mesh(d.path_join("objects/" + obj))
			return m.get_aabb() if m else null)
		var shown := 0
		if LevelLayout.is_x1(a):
			node.add_child(LevelLayout.assemble_x1(a, func(obj: String) -> Node3D:
				return _gltf_scene(d.path_join("models/%s.glb" % obj.get_basename()))))
			shown = objects.size()
			objects = []
		for i in objects.size():
			var o: String = objects[i]
			var mesh := _mesh(d.path_join("objects/" + o))
			if mesh == null:
				continue
			var mi := MeshInstance3D.new()
			mi.mesh = mesh
			mi.position.y = offsets.get(o, 0.0)
			mi.rotation_degrees.y = part_yaws.get(i, 0.0)
			node.add_child(mi)
			shown += 1
		if shown == 0:
			var mi := MeshInstance3D.new()
			mi.mesh = dot
			mi.material_override = _flat_material(Color.from_hsv(fmod(int(a["class"]) * 0.137, 1.0), 0.8, 1.0))
			mi.set_meta("no_style", true)
			node.add_child(mi)
	_build_nets(root, layout)
	# Frame the play area (where the actors are) rather than the border filler around it.
	if _actor_nodes.size() > 1:
		var box := AABB((_actor_nodes.values()[0] as Node3D).position, Vector3.ZERO)
		for n: Node3D in _actor_nodes.values():
			box = box.expand(n.position)
		_frame_aabb = box.grow(4.0)
	_style_meshes(root)


func _build_nets(root: Node3D, layout: RefCounted) -> void:
	# Cnet AI path graphs (tools/extract_nets.py). Nodes use the actors.json
	# pos_x/pos_z convention, so they go through the same layout as actors.
	_net_boxes = {}
	var data: Variant = _read_json(_ldir().path_join("nets.json"))
	_nets = data.get("nets", []) if data is Dictionary else []
	if _nets.is_empty():
		return
	var im := ImmediateMesh.new()
	im.surface_begin(Mesh.PRIMITIVE_LINES)
	for net: Dictionary in _nets:
		var c := Color.from_hsv(fmod(int(net.id) * 0.173, 1.0), 0.9, 1.0)
		var pts: Array[Vector3] = []
		for n: Dictionary in net.nodes:
			var p: Vector3 = layout.actor_position(n)
			if n.get("height_world") != null:
				p.y = float(n.height_world)
			elif n.get("height_mode_name", "") == "raycast_low":
				p.y = layout.sample_y(p.x, p.z, false)
			pts.append(p + Vector3(0, 0.15, 0))
		var box := AABB(pts[0], Vector3.ZERO) if pts else AABB()
		im.surface_set_color(c)
		for p in pts:
			box = box.expand(p)
			# Small cross at each node
			im.surface_add_vertex(p + Vector3(-0.2, 0, 0))
			im.surface_add_vertex(p + Vector3(0.2, 0, 0))
			im.surface_add_vertex(p + Vector3(0, 0, -0.2))
			im.surface_add_vertex(p + Vector3(0, 0, 0.2))
		for e: Array in net.edges:
			im.surface_add_vertex(pts[int(e[0])])
			im.surface_add_vertex(pts[int(e[1])])
		_net_boxes[int(net.id)] = box.grow(2.0)
	im.surface_end()
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.vertex_color_use_as_albedo = true
	mat.no_depth_test = true  # paths stay visible through terrain
	_net_mesh = MeshInstance3D.new()
	_net_mesh.mesh = im
	_net_mesh.material_override = mat
	_net_mesh.set_meta("no_style", true)
	_net_mesh.visible = _show_paths
	root.add_child(_net_mesh)


func _focus_net(net: Dictionary) -> void:
	_marker.visible = false
	_frame(_net_boxes.get(int(net.id), _frame_aabb))
	var users := PackedStringArray()
	for a: Dictionary in net.get("actors", []):
		users.append("#%d %s class %d (start node %d)" % [a.id, a.kind, a["class"], a.start_node])
	var disabled: int = net.nodes.filter(func(n: Dictionary) -> bool: return n.disabled).size()
	_info.text = "[b]Cnet %d[/b]\n%d nodes · %d edges · %d start disabled\nActors on this path:\n%s" % [
		net.id, net.node_count, net.edges.size(), disabled, "\n".join(users) if users else "(none)"]


func _focus_actor(a: Variant) -> void:
	if a is Dictionary and a.has("nodes"):
		_focus_net(a)
		return
	if not a is Dictionary:
		_marker.visible = false
		_frame(_frame_aabb)
		return
	var node: Node3D = _actor_nodes.get(int(a.id))
	var p: Vector3 = node.global_position if node else Vector3.ZERO
	_marker.position = p + Vector3(0, 7, 0)
	_marker.visible = true
	_target = p
	_dist = 9.0
	_update_camera()

	var same := _actors.filter(func(x: Dictionary) -> bool: return x["class"] == a["class"]).size()
	var text := "[b]Actor #%d[/b]  %s  class %d  (%d of this class)\npos_x %.4f  pos_z %.4f  → world (%.1f, %.1f, %.1f)\nobjects: %s\ntextures: %s\n" % [
		a.id, a.kind, a["class"], same, a.pos_x, a.pos_z, p.x, p.y, p.z,
		", ".join(a.get("objects", [])), ", ".join(a.get("textures", []))]
	if _decoded.has(int(a.id)):
		text += "\n[b]Decoded[/b] (actors_decoded.json)\n[code]%s[/code]\n" % JSON.stringify(_decoded[int(a.id)], "  ")
	var rows := PackedStringArray()
	var params: Array = a.get("params", [])
	for i in params.size():
		var v: int = String(params[i]).hex_to_int()
		rows.append("[%2d] %10s  %11d  hi %5d lo %5d" % [i, params[i], v, (v >> 16) & 0xFFFF, v & 0xFFFF])
	text += "\n[b]Raw params[/b]\n[code]%s[/code]" % "\n".join(rows)
	_info.text = text


func _pick_actor(screen: Vector2) -> void:
	var best := 24.0
	var best_id := -1
	for id: int in _actor_nodes:
		var p: Vector3 = (_actor_nodes[id] as Node3D).global_position
		if _cam.is_position_behind(p):
			continue
		var d := _cam.unproject_position(p).distance_to(screen)
		if d < best:
			best = d
			best_id = id
	if best_id < 0:
		return
	for i in _list.item_count:
		var e: Dictionary = _list.get_item_metadata(i)
		if e.key is Dictionary and int(e.key.id) == best_id:
			_list.select(i)
			_list.ensure_current_is_visible()
			_focus_actor(e.key)
			return


# ─── Audio / video ────────────────────────────────────────────────────────────

func _show_audio(rel: String) -> void:
	var path := _ldir().path_join(rel)
	_player.stream = AudioStreamWAV.load_from_file(path)
	var wav := _read_wav(path)
	_tex_rect.texture = ImageTexture.create_from_image(wav.image) if wav.has("image") else null
	var length := _player.stream.get_length() if _player.stream else 0.0
	_info.text = "[b]%s[/b]\n%s Hz · %s ch · %d-bit · %.2f s" % [
		rel, wav.get("rate", "?"), wav.get("channels", "?"), wav.get("bits", 0), length]
	_player.play()


func _show_video(file: String) -> void:
	var stream := VideoStreamTheora.new()
	stream.file = _parsed.path_join("video").path_join(file)
	_video.stream = stream
	_video.play()
	_info.text = "[b]%s[/b]\nlength %.1f s" % [file, _video.get_stream_length()]


func _media_play() -> void:
	match _tab:
		"Audio":
			_player.play()
		"Video":
			_video.play()
		"Models":
			_play_anim(_anim_btn.selected)


func _media_stop() -> void:
	if _player:
		_player.stop()
	if _video:
		_video.stop()
	if _anim:
		_anim.stop()


func _read_wav(path: String) -> Dictionary:
	# Minimal RIFF/PCM reader so the waveform comes from the file itself.
	var bytes := FileAccess.get_file_as_bytes(path)
	var out := {}
	if bytes.size() < 12 or bytes.slice(0, 4).get_string_from_ascii() != "RIFF":
		return out
	var p := 12
	var data := PackedByteArray()
	while p + 8 <= bytes.size():
		var id := bytes.slice(p, p + 4).get_string_from_ascii()
		var sz := bytes.decode_u32(p + 4)
		if id == "fmt ":
			out.channels = bytes.decode_u16(p + 10)
			out.rate = bytes.decode_u32(p + 12)
			out.bits = bytes.decode_u16(p + 22)
		elif id == "data":
			data = bytes.slice(p + 8, p + 8 + sz)
		p += 8 + sz + (sz & 1)
	if data.is_empty() or out.get("bits", 0) != 16:
		return out
	var ch: int = out.channels
	var frames := data.size() / (2 * ch)
	var w := 1400
	var h := 160 * ch
	var img := Image.create(w, h, false, Image.FORMAT_RGBA8)
	img.fill(Color(0.1, 0.11, 0.13))
	for c in ch:
		var mid := 80 + c * 160
		for x in w:
			var f0 := frames * x / w
			var f1 := maxi(frames * (x + 1) / w, f0 + 1)
			var lo := 0
			var hi := 0
			for f in range(f0, mini(f1, frames), maxi((f1 - f0) / 64, 1)):
				var s := data.decode_s16((f * ch + c) * 2)
				lo = mini(lo, s)
				hi = maxi(hi, s)
			for y in range(mid - hi * 75 / 32768, mid - lo * 75 / 32768 + 1):
				img.set_pixel(x, clampi(y, 0, h - 1), Color(0.35, 0.75, 1.0))
	out.image = img
	return out


func _process(delta: float) -> void:
	if _strip and _tab == "Images" and _tex_rect.texture == _strip:
		_strip_time += delta
		var frames: int = _strip.atlas.get_height() / 48
		_strip.region.position.y = (int(_strip_time * 10.0) % frames) * 48
	if not _media_row.visible:
		return
	match _tab:
		"Audio":
			if _player.stream:
				_media_pos.text = "%.1f / %.1f s" % [_player.get_playback_position() if _player.playing else 0.0, _player.stream.get_length()]
		"Video":
			_media_pos.text = "%.1f / %.1f s" % [_video.stream_position, _video.get_stream_length()] if _video.stream else ""
		"Models":
			_media_pos.text = "%.2f s" % _anim.current_animation_position if _anim and _anim.is_playing() else ""


# ─── Data (JSON tree) ─────────────────────────────────────────────────────────

func _show_json(file: String) -> void:
	_tree.clear()
	var data: Variant = _read_json(_ldir().path_join(file))
	var root := _tree.create_item()
	root.set_text(0, file)
	_tree_fill(root, data)
	root.set_collapsed(false)
	_info.text = "[b]%s[/b]\n%s\n\nExpand nodes in the tree; large arrays load as you open them." % [file, _summary(data)]


func _summary(v: Variant) -> String:
	if v is Dictionary:
		return "object · %d keys" % v.size()
	if v is Array:
		return "array · %d items" % v.size()
	return str(v)


func _tree_fill(item: TreeItem, v: Variant) -> void:
	# Children are built lazily: a container gets one placeholder child until expanded.
	item.set_metadata(0, v)
	if v is Dictionary or v is Array:
		item.set_text(1, _summary(v))
		item.create_child().set_text(0, "…")
		item.set_collapsed(true)
	else:
		item.set_text(1, str(v))


func _on_tree_collapsed(item: TreeItem) -> void:
	if item.is_collapsed() or item.has_meta("built"):
		return
	item.set_meta("built", true)
	for c in item.get_children():
		c.free()
	var v: Variant = item.get_metadata(0)
	if v is Dictionary:
		for k in v:
			var c := item.create_child()
			c.set_text(0, str(k))
			_tree_fill(c, v[k])
	elif v is Array:
		for i in v.size():
			var c := item.create_child()
			var e: Variant = v[i]
			var hint := ""
			if e is Dictionary:
				for key in ["name", "id", "class_name", "type", "text"]:
					if e.has(key):
						hint = "  %s" % e[key]
						break
			c.set_text(0, "[%d]%s" % [i, hint])
			_tree_fill(c, e)


# ─── Camera ───────────────────────────────────────────────────────────────────

func _frame(aabb: AABB) -> void:
	_target = aabb.get_center()
	_dist = maxf(aabb.size.length() * 1.1, 0.05)
	_update_camera()


func _update_camera() -> void:
	_pitch = clampf(_pitch, -1.5, 1.5)
	var dir := Vector3(cos(_pitch) * sin(_yaw), sin(_pitch), cos(_pitch) * cos(_yaw))
	_cam.near = maxf(_dist * 0.005, 0.001)
	_cam.far = maxf(_dist * 50.0, 500.0)
	_cam.look_at_from_position(_target + dir * _dist, _target, Vector3.UP)


func _on_view_input(ev: InputEvent) -> void:
	if _tab in MEDIA_TABS:
		return
	if ev is InputEventMouseMotion:
		var m := ev as InputEventMouseMotion
		if m.button_mask & MOUSE_BUTTON_MASK_LEFT:
			_yaw -= m.relative.x * 0.008
			_pitch += m.relative.y * 0.008
			_update_camera()
		elif m.button_mask & (MOUSE_BUTTON_MASK_RIGHT | MOUSE_BUTTON_MASK_MIDDLE):
			var b := _cam.global_transform.basis
			_target += (-b.x * m.relative.x + b.y * m.relative.y) * _dist * 0.0015
			_update_camera()
	elif ev is InputEventMouseButton:
		var mb := ev as InputEventMouseButton
		if mb.pressed:
			if mb.button_index == MOUSE_BUTTON_WHEEL_UP:
				_dist *= 0.9
				_update_camera()
			elif mb.button_index == MOUSE_BUTTON_WHEEL_DOWN:
				_dist /= 0.9
				_update_camera()
			elif mb.button_index == MOUSE_BUTTON_LEFT:
				_press_pos = mb.position
			_view.grab_focus()
		elif mb.button_index == MOUSE_BUTTON_LEFT and _tab == "Level" and mb.position.distance_to(_press_pos) < 4.0:
			_pick_actor(_view.global_position + mb.position)
	elif ev is InputEventKey and ev.pressed and (ev as InputEventKey).keycode == KEY_F:
		_reselect()
