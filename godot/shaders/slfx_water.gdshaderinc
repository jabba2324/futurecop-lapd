// Type-3 terrain polygons (mostly water) lit by the chunk's SLFX light table, as in
// SLUS_007.39: the game rebuilds one 16x16 table T[cx][cy] from the SLFX word
// (0x800654f8, noise cells 0x80063568) and uses T at each polygon corner as its
// Gouraud colour (0x800648e8, 128 = texture unchanged). extract_terrain.py stores the
// corner's cell in the vertex colour, ((cx + .5) / 16, (cy + .5) / 16).
//   wave  (w >> 28 == 0): s = w>>16 &15, a = w>>20 &15, yf = w>>24 &15, b = w>>8 &255,
//     spd = w &255, step = 0x100 << s;
//     ph = (clock*spd + (15-cy)*step + (yf ? (15-cx)*step : 0)) & ((0x1000 << s) - 1)
//     T = clamp(b + (ph < 0x1000 ? rsin(ph) >> (a + 8) : 0), 0, 255)
//     with clock the 300 Hz clock latched each frame (+5 per vsync) and rsin 65536 sin.
//   noise (w >> 28 == 1): each cell counts down from 2p at 75/s, p = 64 + rand & 63,
//     v = 0..p..0; T = (((b << k) + v) >> k) & 255, k = w & 3. The game draws a new
//     random p each cycle; here each cell keeps a hashed p and phase.
uniform sampler2D tex : source_color, filter_nearest, repeat_enable;
uniform vec2 uv_offset;
uniform uint word;
varying float light;

uint cell_hash(uint x) {
	x ^= x >> 16u;
	x *= 0x7feb352du;
	x ^= x >> 15u;
	x *= 0x846ca68bu;
	x ^= x >> 16u;
	return x;
}

void vertex() {
	int cx = clamp(int(COLOR.r * 16.0), 0, 15);
	int cy = clamp(int(COLOR.g * 16.0), 0, 15);
	uint w = word;
	uint b = (w >> 8u) & 255u;
	float t;
	if ((w >> 28u) == 0u) {
		uint s = (w >> 16u) & 15u;
		uint a = (w >> 20u) & 15u;
		uint yf = (w >> 24u) & 15u;
		uint step = 0x100u << s;
		uint clock = uint(floor(TIME * 60.0)) * 5u;
		uint ph = (clock * (w & 255u) + uint(15 - cy) * step
			+ (yf != 0u ? uint(15 - cx) * step : 0u)) & ((0x1000u << s) - 1u);
		float add = 0.0;
		if (ph < 0x1000u) {
			add = floor(floor(65536.0 * sin(float(ph) * TAU / 4096.0)) / exp2(float(a + 8u)));
		}
		t = clamp(float(b) + add, 0.0, 255.0);
	} else {
		uint k = w & 3u;
		uint h = cell_hash(uint(cx * 16 + cy) + w);
		float p = float(64u + (h & 63u));
		float period = 2.0 * p / 75.0;
		float cur = 2.0 * p * (1.0 - fract(TIME / period + float(h >> 8u) / 16777216.0));
		float v = floor(cur > p ? 2.0 * p - cur : cur);
		t = float((((b << k) + uint(v)) >> k) & 255u);
	}
	light = t / 128.0;
}
