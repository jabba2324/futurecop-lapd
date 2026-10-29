shader_type spatial;
render_mode unshaded, cull_disabled;
#include "slfx_water.gdshaderinc"

void fragment() {
	vec4 c = texture(tex, UV + uv_offset);
	if (c.a < 0.5) {
		discard;
	}
	ALBEDO = c.rgb * light;
}
