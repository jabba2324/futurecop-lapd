// The semi-transparent variant ("_mix"): the PS1's 50/50 blend, from the texture's
// alpha 128 on STP texels.
shader_type spatial;
render_mode unshaded, cull_disabled, blend_mix, depth_draw_opaque;
#include "slfx_water.gdshaderinc"

void fragment() {
	vec4 c = texture(tex, UV + uv_offset);
	ALBEDO = c.rgb * light;
	ALPHA = c.a;
}
