## Level music: the game streams the track's blocks in order and wraps to the first
## (SLUS_007.39 0x8002a398) with no gap, so loop the whole WAV seamlessly.
extends AudioStreamPlayer


func _ready() -> void:
	var wav := stream as AudioStreamWAV
	if wav:
		wav.loop_mode = AudioStreamWAV.LOOP_FORWARD
		wav.loop_begin = 0
		wav.loop_end = int(wav.get_length() * wav.mix_rate)
	else:
		finished.connect(play)
