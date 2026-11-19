"""Render one 3D airliner shot for Reel Studio's 3D library (1080x1920, 30 fps, 6 s).
blender -b --python shot3d.py -- <glb> <shot: orbit|takeoff|nose> <out.mp4> [frames] [preview.png]"""
import bpy, sys, math, mathutils
from mathutils import Vector

glb, shot, out = sys.argv[-5], sys.argv[-4], sys.argv[-3]
frames, preview = int(sys.argv[-2]), sys.argv[-1]
NAVY, GOLD = (0.012, 0.03, 0.12, 1), (0.85, 0.62, 0.18, 1)

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
bpy.ops.import_scene.gltf(filepath=glb)
meshes = [o for o in sc.objects if o.type == "MESH"]

# --- repaint: orange/red livery parts -> We One navy; leave lights (strobe, lamps) alone
for m in bpy.data.materials:
    if not m.use_nodes or "strobe" in m.name.lower() or "light" in m.name.lower():
        continue
    b = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if not b or b.inputs["Base Color"].links:
        continue
    r, g, bl = b.inputs["Base Color"].default_value[:3]
    if r >= 0.95 and g <= 0.35 and bl <= 0.05:
        b.inputs["Base Color"].default_value = NAVY
        b.inputs["Metallic"].default_value = 0.35; b.inputs["Roughness"].default_value = 0.3
    elif r >= 0.95 and g >= 0.9 and bl >= 0.9:               # fuselage white: a touch of clear-coat sheen
        b.inputs["Roughness"].default_value = 0.25

# --- one parent, real size, nose along +X, wheels on the ground
root = bpy.data.objects.new("AC", None); sc.collection.objects.link(root)
for o in meshes:
    if o.parent is None or o.parent.type != "MESH":
        mw = o.matrix_world.copy(); o.parent = root; o.matrix_world = mw
for o in sc.objects:
    if o.type == "EMPTY" and o is not root and o.parent is None:
        mw = o.matrix_world.copy(); o.parent = root; o.matrix_world = mw
bpy.context.view_layer.update()
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
mn = Vector([min(p[i] for p in pts) for i in range(3)]); mx = Vector([max(p[i] for p in pts) for i in range(3)])
span = mx - mn
long_ax = 0 if span.x > span.y else 1
# tail = the end of the long axis where the fin (highest point) is
top = max(pts, key=lambda p: p.z)
mid = (mn[long_ax] + mx[long_ax]) / 2
tail_positive = top[long_ax] > mid
scale = 39.5 / span[long_ax]
yaw = {(0, True): math.pi, (0, False): 0.0, (1, True): math.pi / 2, (1, False): -math.pi / 2}[(long_ax, tail_positive)]   # tail to -X, nose to +X
root.scale = (scale,) * 3; root.rotation_euler = (0, 0, yaw)
bpy.context.view_layer.update()
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
mn = Vector([min(p[i] for p in pts) for i in range(3)]); mx = Vector([max(p[i] for p in pts) for i in range(3)])
root.location -= Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z))
bpy.context.view_layer.update()
nose_x = 39.5 / 2

# --- world: golden-hour sky, grass, runway with centre-line
w = bpy.data.worlds.new("sky"); sc.world = w; w.use_nodes = True
nt = w.node_tree; sky = nt.nodes.new("ShaderNodeTexSky")
for t in ("NISHITA", "MULTIPLE_SCATTERING", "SINGLE_SCATTERING"):
    try:
        sky.sky_type = t; break
    except Exception:  # noqa: BLE001
        continue
for k, v in (("sun_elevation", math.radians(3.5)), ("sun_rotation", math.radians(200)), ("air_density", 1.6), ("dust_density", 3.0),
             ("aerosol_density", 3.0), ("ozone_density", 1.2)):
    try:
        setattr(sky, k, v)
    except Exception:  # noqa: BLE001
        pass
print("SKY", sky.sky_type)
nt.links.new(sky.outputs[0], nt.nodes["Background"].inputs[0]); nt.nodes["Background"].inputs[1].default_value = 1.3
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN")); sc.collection.objects.link(sun)
sun.data.energy = 7.0; sun.data.color = (1.0, 0.58, 0.30); sun.data.angle = math.radians(1.5)
sun.rotation_euler = (math.radians(86.5), 0, math.radians(200 + 90))

def mat(name, col, rough=0.9):
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]; b.inputs["Base Color"].default_value = col; b.inputs["Roughness"].default_value = rough
    return m
def plane(name, size, loc, m):
    bpy.ops.mesh.primitive_plane_add(size=1, location=loc); o = bpy.context.active_object; o.name = name
    o.scale = (size[0], size[1], 1); o.data.materials.append(m); return o
def textured(name, c1, c2, scale, rough):
    m = mat(name, c1, rough); t = m.node_tree; b = t.nodes["Principled BSDF"]
    nz = t.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = scale; nz.inputs["Detail"].default_value = 8
    ramp = t.nodes.new("ShaderNodeValToRGB"); ramp.color_ramp.elements[0].color = c1; ramp.color_ramp.elements[1].color = c2
    t.links.new(nz.outputs["Fac"], ramp.inputs["Fac"]); t.links.new(ramp.outputs["Color"], b.inputs["Base Color"])
    bump = t.nodes.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.25
    t.links.new(nz.outputs["Fac"], bump.inputs["Height"]); t.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    return m
plane("grass", (4000, 4000), (0, 0, -0.02), textured("grass", (0.018, 0.035, 0.010, 1), (0.06, 0.075, 0.025, 1), 0.08, 0.95))
plane("runway", (3000, 45), (0, 0, -0.01), textured("asphalt", (0.022, 0.022, 0.025, 1), (0.06, 0.058, 0.055, 1), 0.6, 0.7))
paint = mat("paint", (0.85, 0.85, 0.82, 1), 0.6)
for i in range(-50, 51):
    plane(f"cl{i}", (30, 0.9), (i * 60, 0, 0.0), paint)
for s in (-1, 1):
    plane(f"edge{s}", (3000, 0.9), (0, s * 21.5, 0.0), paint)

# --- camera and motion per shot
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); sc.collection.objects.link(cam); sc.camera = cam
tgt = bpy.data.objects.new("tgt", None); sc.collection.objects.link(tgt)
def aim(world_pt):                     # the camera looks at a point fixed on the aircraft, so it follows it when it moves
    tgt.parent = root; tgt.matrix_parent_inverse = root.matrix_world.inverted(); tgt.location = world_pt
tc = cam.constraints.new("TRACK_TO"); tc.target = tgt; tc.track_axis = "TRACK_NEGATIVE_Z"; tc.up_axis = "UP_Y"
cam.data.sensor_width = 36
sc.frame_start, sc.frame_end = 1, frames
prefs = bpy.context.preferences.edit
def key(o, f, **kv):
    prefs.keyframe_new_interpolation_type = "QUAD" if o.name == "AC" else "SINE"   # aircraft speeds up; camera eases
    prefs.keyframe_new_handle_type = "AUTO_CLAMPED"
    for k, v in kv.items():
        setattr(o, k, v); o.keyframe_insert(k, frame=f)

if shot == "orbit":                    # slow golden-hour orbit around the parked aircraft
    cam.data.lens = 30; aim(Vector((2, 0, 4.0)))
    for f, ang in ((1, 24), (frames, 62)):           # low front three-quarter, swinging round the nose
        a = math.radians(ang); key(cam, f, location=(42 * math.cos(a), -42 * math.sin(a), 1.7))
elif shot == "takeoff":                # rolls past a low camera and rotates into the air
    cam.data.lens = 35; cam.location = (60, -40, 1.0); aim(Vector((0, 0, 2.5)))
    k = root.location.copy()
    key(root, 1, location=k + Vector((-330, 0, 0)), rotation_euler=(0, 0, yaw))
    key(root, int(frames * 0.62), location=k + Vector((10, 0, 0)), rotation_euler=(0, 0, yaw))
    key(root, frames, location=k + Vector((150, 0, 14)), rotation_euler=(0, math.radians(-9), yaw))
    sc.render.use_motion_blur = True
elif shot == "nose":                   # slow push-in on the cockpit windows, shallow focus
    cam.data.lens = 85; aim(Vector((nose_x - 3.0, 0, 4.2)))
    key(cam, 1, location=(nose_x + 30, -22, 2.8)); key(cam, frames, location=(nose_x + 19, -14, 3.3))
    cam.data.dof.use_dof = True; cam.data.dof.focus_object = tgt; cam.data.dof.aperture_fstop = 2.0
# --- render
eng = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
sc.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in eng else "BLENDER_EEVEE"
try:
    sc.eevee.taa_render_samples = 32; sc.eevee.use_shadows = True
except Exception:
    pass
sc.view_settings.view_transform = "AgX"
for lk in ("AgX - Punchy", "AgX - High Contrast", "AgX - Medium High Contrast"):
    try:
        sc.view_settings.look = lk; break
    except Exception:  # noqa: BLE001
        continue
sc.render.resolution_x, sc.render.resolution_y, sc.render.fps = 1080, 1920, 30
if preview != "-":                     # one still from the middle, to check the look
    sc.frame_set(frames // 2); sc.render.filepath = preview; sc.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
else:
    sc.render.image_settings.file_format = "FFMPEG"; sc.render.ffmpeg.format = "MPEG4"; sc.render.ffmpeg.codec = "H264"
    sc.render.ffmpeg.constant_rate_factor = "HIGH"; sc.render.filepath = out
    bpy.ops.render.render(animation=True)
print("DONE", shot, yaw, scale)
