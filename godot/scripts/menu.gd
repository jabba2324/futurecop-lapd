## Level-select menu for the walkable level scenes (godot/scenes/<LEVEL>_level.tscn).
## Lists the scenes that exist, grouped by the disc folder they come from (CW =
## Crime War, PA = Precinct Assault), previews each level's loading screen, and
## opens the chosen one (Enter, double-click or the Walk button). While the scene
## loads (in the background) the level's original loading screen fills the window.
## In a level, Esc comes back here (player.gd).
## Crime War levels first play their briefing movie (parsed/video/BRF*.ogv, from
## extract_videos.py) while the scene loads; any key or click skips it. The game's
## level table (SLUS_007.39 0x8009b4e4, 16 entries of 36 bytes: name, briefing movie,
## after-mission movie, ...) gives the Precinct Assault maps no briefing, and LAX2
## plays LAX1's (0x80083130 looks "lax2" up as "lax1").
extends Control

const SCENE_DIR := "res://scenes/"
# Disc folders: raw CW/ and PA/ in the extracted PS1 disc.
const CRIME_WAR := ["LAX1", "LAX2", "M1A1", "M2C", "M3A", "M3B", "M4A1", "OV", "UN"]
const PRECINCT_ASSAULT := ["CONFT", "HK", "JOKE", "OVMP", "SLIM"]
const BRIEFINGS := {
	"LAX1": "BRFLAX", "LAX2": "BRFLAX", "M1A1": "BRFM1A1", "M2C": "BRFM2C",
	"M3A": "BRFM3A", "M3B": "BRFM3B", "M4A1": "BRFM4A1", "OV": "BRFOV", "UN": "BRFUN",
}

var _list: ItemList
var _preview: TextureRect
var _caption: Label
var _loading := ""   # scene path being loaded in the background
var _briefing_toggle: CheckBox
var _movie: VideoStreamPlayer   # the briefing while it plays
var _movie_cover: Control
var _movie_level := ""


func _ready() -> void:
	var bg := ColorRect.new()
	bg.color = Color(0.08, 0.09, 0.11)
	bg.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(bg)

	var root := HBoxContainer.new()
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT, Control.PRESET_MODE_MINSIZE, 32)
	root.add_theme_constant_override("separation", 32)
	add_child(root)

	var left := VBoxContainer.new()
	left.custom_minimum_size.x = 300
	root.add_child(left)
	var title := Label.new()
	title.text = "Level explorer"
	title.add_theme_font_size_override("font_size", 32)
	left.add_child(title)
	var hint := Label.new()
	hint.text = "Choose a level to walk around.\nIn a level: W/S walk, A/D turn, Q/E strafe, Shift slow,\nSpace jump (hover: brake), Shift+Space crouch, T transform,\nV camera view, K damage nearest prop, L lighting (PS1/lit), G fog,\nM map, O overlay, Esc returns here."
	hint.modulate = Color(0.75, 0.78, 0.85)
	left.add_child(hint)

	_list = ItemList.new()
	_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_list.add_theme_font_size_override("font_size", 22)
	_list.item_selected.connect(_on_selected)
	_list.item_activated.connect(func(_i: int) -> void: _open_selected())
	left.add_child(_list)

	var walk := Button.new()
	walk.text = "Walk"
	walk.add_theme_font_size_override("font_size", 22)
	walk.pressed.connect(_open_selected)
	left.add_child(walk)
	_briefing_toggle = CheckBox.new()
	_briefing_toggle.text = "Play the briefing first"
	_briefing_toggle.button_pressed = true
	_briefing_toggle.focus_mode = Control.FOCUS_NONE
	left.add_child(_briefing_toggle)

	var right := VBoxContainer.new()
	right.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	root.add_child(right)
	_preview = TextureRect.new()
	_preview.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_preview.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_preview.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	right.add_child(_preview)
	_caption = Label.new()
	_caption.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_caption.add_theme_font_size_override("font_size", 20)
	right.add_child(_caption)

	_fill_list()
	if _list.item_count > 1:
		var first := 1 if _list.is_item_disabled(0) else 0
		_list.select(first)
		_on_selected(first)
	_list.grab_focus()


func _fill_list() -> void:
	var available := {}
	for f in DirAccess.get_files_at(SCENE_DIR):
		if f.ends_with("_level.tscn"):
			available[f.trim_suffix("_level.tscn")] = true
	for group in [["Crime War", CRIME_WAR], ["Precinct Assault", PRECINCT_ASSAULT]]:
		var levels: Array = group[1].filter(func(l: String) -> bool: return available.has(l))
		if levels.is_empty():
			continue
		var header := _list.add_item(group[0])
		_list.set_item_disabled(header, true)
		_list.set_item_selectable(header, false)
		_list.set_item_custom_fg_color(header, Color(1.0, 0.8, 0.3))
		for level: String in levels:
			var i := _list.add_item("    " + level)
			_list.set_item_metadata(i, level)


func _on_selected(i: int) -> void:
	var level: Variant = _list.get_item_metadata(i)
	if level == null:
		return
	var shot := "res://assets/%s/loading/%sLD.png" % [level, level]
	_preview.texture = load(shot) if ResourceLoader.exists(shot) else null
	_caption.text = "%s — %s" % [level, "Crime War" if level in CRIME_WAR else "Precinct Assault"]
	if _briefing_path(level) != "":
		_caption.text += " · briefing %s" % BRIEFINGS[level]


func _open_selected() -> void:
	var sel := _list.get_selected_items()
	if sel.is_empty():
		return
	var level: Variant = _list.get_item_metadata(sel[0])
	if level == null or _loading != "":
		return
	_loading = SCENE_DIR + "%s_level.tscn" % level
	ResourceLoader.load_threaded_request(_loading)
	var movie := _briefing_path(level)
	if _briefing_toggle.button_pressed and movie != "":
		_play_briefing(movie, level)
	else:
		_show_loading_screen(level)


## The extracted briefing movie for a level, or "" if it has none or isn't extracted.
func _briefing_path(level: String) -> String:
	if not BRIEFINGS.has(level):
		return ""
	var path := ProjectSettings.globalize_path("res://").path_join(
		"../parsed/video/%s.ogv" % BRIEFINGS[level]).simplify_path()
	return path if FileAccess.file_exists(path) else ""


func _play_briefing(path: String, level: String) -> void:
	_movie_cover = ColorRect.new()
	(_movie_cover as ColorRect).color = Color.BLACK
	_movie_cover.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(_movie_cover)
	var frame := AspectRatioContainer.new()
	frame.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	frame.ratio = 256.0 / 144.0   # the movies' frame size
	_movie_cover.add_child(frame)
	var stream := VideoStreamTheora.new()
	stream.file = path
	_movie = VideoStreamPlayer.new()
	_movie.stream = stream
	_movie.expand = true
	_movie_level = level
	_movie.finished.connect(_end_briefing)
	frame.add_child(_movie)
	var skip := Label.new()
	skip.text = "Any key skips"
	skip.modulate = Color(1, 1, 1, 0.5)
	skip.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_RIGHT, Control.PRESET_MODE_MINSIZE, 16)
	skip.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	skip.grow_vertical = Control.GROW_DIRECTION_BEGIN
	_movie_cover.add_child(skip)
	_movie.play()


func _end_briefing() -> void:
	if _movie == null:
		return
	_movie.stop()
	_movie_cover.queue_free()
	_movie = null
	_show_loading_screen(_movie_level)


func _input(event: InputEvent) -> void:
	if _movie == null or not event.is_pressed() or event.is_echo():
		return
	if event is InputEventKey or event is InputEventMouseButton or event is InputEventJoypadButton:
		get_viewport().set_input_as_handled()
		_end_briefing()


func _show_loading_screen(level: String) -> void:
	var cover := ColorRect.new()
	cover.color = Color.BLACK
	cover.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(cover)
	var shot := "res://assets/%s/loading/%sLD.png" % [level, level]
	if ResourceLoader.exists(shot):
		var pic := TextureRect.new()
		pic.texture = load(shot)
		pic.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		pic.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		pic.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		cover.add_child(pic)   # the picture itself says "Loading in progress"


func _process(_delta: float) -> void:
	if _loading == "" or _movie != null:
		return   # the scene switches once the briefing is over
	match ResourceLoader.load_threaded_get_status(_loading):
		ResourceLoader.THREAD_LOAD_LOADED:
			get_tree().change_scene_to_packed(ResourceLoader.load_threaded_get(_loading))
			_loading = ""
		ResourceLoader.THREAD_LOAD_FAILED, ResourceLoader.THREAD_LOAD_INVALID_RESOURCE:
			push_error("Could not load " + _loading)
			_loading = ""
