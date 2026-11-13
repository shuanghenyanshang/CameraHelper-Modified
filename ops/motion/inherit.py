"""Camera settings sampled with the generator's own evaluated Bezier curve.

The vector (index, index squared, 0) is linearly interpolated by Sample Curve.
Its two moments identify the adjacent source pair, including the cyclic seam.
No spatial search or separate approximation of Bezier arc lengths is involved.
"""
import math
import bpy
from bpy.app.handlers import persistent

ATTRIBUTE = "camhp_source_moments"
VERSION = 2
_busy = False


def modifier_input(modifier, identifier):
    # Official Blender and NPR expose different interfaces even at the same
    # version number. Prefer the typed API only when this host provides it.
    properties = getattr(modifier, 'properties', None)
    inputs = getattr(properties, 'inputs', None)
    return getattr(inputs, identifier, None) if inputs is not None else None


def socket_value(modifier, identifier):
    socket = modifier_input(modifier, identifier)
    if socket is not None and hasattr(socket, 'value'):
        return socket.value
    return modifier[identifier]


def set_socket_value(modifier, identifier, value):
    socket = modifier_input(modifier, identifier)
    if socket is not None and hasattr(socket, 'value'):
        socket.value = value
    else:
        modifier[identifier] = value
    modifier.id_data.update_tag()


def is_generator(group):
    return (group is not None and group.name.startswith('MotionCamera')
            and 'Sample Curve.002' in group.nodes
            and group.nodes['Sample Curve.002'].bl_idname == 'GeometryNodeSampleCurve')


def connect_sampling_switch(group):
    """The real input chooses sampled geometry or the unchanged motion mesh."""
    store = group.nodes['Camera Helper Settings Result']
    geometry = store.inputs['Geometry'].links[0].from_socket
    frame = store.parent
    control = group.nodes.get('Camera Helper Settings Input')
    if control is None:
        control = group.nodes.new('NodeGroupInput')
        control.name = 'Camera Helper Settings Input'
        control.parent = frame
        control.location = (550, -220)
    gate = group.nodes.get('Camera Helper Settings Switch')
    if gate is None:
        gate = group.nodes.new('GeometryNodeSwitch')
        gate.name = 'Camera Helper Settings Switch'
        gate.input_type = 'GEOMETRY'
        gate.parent = frame
        gate.location = (1010, 0)
    output = next(n for n in group.nodes if n.type == 'GROUP_OUTPUT' and n.is_active_output)
    enabled = next(s for s in control.outputs if s.identifier == group['camhp_inherit_socket'])
    group.links.new(enabled, gate.inputs['Switch'])
    group.links.new(geometry, gate.inputs['False'])
    group.links.new(store.outputs['Geometry'], gate.inputs['True'])
    group.links.new(gate.outputs['Output'], output.inputs['Geometry'])


def ensure_group(group):
    """Add only a parallel sampling branch; existing motion links stay intact."""
    if group.get('camhp_inherit_version') == VERSION:
        return
    if group.get('camhp_inherit_version') == 1:
        # Retain the existing socket identifier and every modifier value.
        connect_sampling_switch(group)
        group['camhp_inherit_version'] = VERSION
        return
    sample_motion = group.nodes['Sample Curve.002']
    output = next(n for n in group.nodes if n.type == 'GROUP_OUTPUT' and n.is_active_output)
    geometry = output.inputs['Geometry'].links[0].from_socket
    frame = group.nodes.new('NodeFrame')
    frame.name = 'Camera Helper Image Settings'
    frame.label = '画面参数路径采样'
    frame.location = (output.location.x - 700, output.location.y - 700)

    def node(kind, name, location):
        n = group.nodes.new(kind)
        n.name = name
        n.parent = frame
        n.location = location
        return n

    index = node('GeometryNodeInputIndex', 'Camera Helper Source Index', (0, 0))
    square = node('ShaderNodeMath', 'Camera Helper Index Squared', (180, -100))
    square.operation = 'MULTIPLY'
    combine = node('ShaderNodeCombineXYZ', 'Camera Helper Source Moments', (360, 0))
    sample = node('GeometryNodeSampleCurve', 'Camera Helper Sample Settings', (550, 0))
    sample.data_type = 'FLOAT_VECTOR'
    sample.mode = sample_motion.mode
    store = node('GeometryNodeStoreNamedAttribute', 'Camera Helper Settings Result', (790, 0))
    store.data_type = 'FLOAT_VECTOR'
    store.domain = 'POINT'
    store.inputs['Name'].default_value = ATTRIBUTE
    links = group.links
    links.new(index.outputs['Index'], combine.inputs['X'])
    links.new(index.outputs['Index'], square.inputs[0])
    links.new(index.outputs['Index'], square.inputs[1])
    links.new(square.outputs[0], combine.inputs['Y'])
    links.new(combine.outputs[0], sample.inputs['Value'])
    for name in ('Curves', 'Factor', 'Length', 'Curve Index'):
        original = sample_motion.inputs.get(name)
        if original is None or name not in sample.inputs:
            continue
        if original.is_linked:
            links.new(original.links[0].from_socket, sample.inputs[name])
        elif hasattr(original, 'default_value'):
            sample.inputs[name].default_value = original.default_value
    links.new(geometry, store.inputs['Geometry'])
    links.new(sample.outputs['Value'], store.inputs['Value'])
    links.new(store.outputs['Geometry'], output.inputs['Geometry'])
    switch = group.interface.new_socket(name='继承画面参数', in_out='INPUT', socket_type='NodeSocketBool')
    switch.default_value = True
    switch.description = ('随路径连续继承来源相机的焦距、镜头位移、感光元件、正交尺度和景深；'
                          '类型、景深开关与光圈叶片数在段中点切换。对焦对象换算为对焦距离。'
                          '关闭后保留当前值，可手动调整')
    # Keep the single switch beside the existing offset, with no new panel.
    offset = next(s for s in group.interface.items_tree if getattr(s, 'identifier', None) == 'Input_10')
    group.interface.move_to_parent(switch, offset.parent, offset.position + 1)
    group['camhp_inherit_socket'] = switch.identifier
    connect_sampling_switch(group)
    group['camhp_inherit_version'] = VERSION
    # Existing modifiers receive the socket before its default is assigned.
    # Initialize those instances exactly once during this migration.
    for obj in bpy.data.objects:
        for modifier in obj.modifiers:
            if modifier.type == 'NODES' and modifier.node_group == group:
                set_socket_value(modifier, switch.identifier, True)


def source_objects(controller, generator, render=False):
    """Same order as the existing Join Geometry / Collection Info branch."""
    group = generator.node_group
    menu = next((s for s in group.interface.items_tree if getattr(s, 'socket_type', '') == 'NodeSocketMenu'), None)
    if menu and socket_value(generator, menu.identifier) in (1, '集合内相机'):
        collection = socket_value(generator, 'Socket_0')
        # Collection Info with Separate Children sorts the direct objects by name.
        # Child collections are instances too, and are not camera sources.
        if collection:
            return sorted(list(collection.objects) + list(collection.children), key=lambda o: o.name)
        return []
    sources = []
    for mod in controller.modifiers:
        if mod == generator:
            break
        if (mod.type == 'NODES' and mod.node_group
                and mod.node_group.name.startswith('MotionCameraSource')
                and (mod.show_render if render else mod.show_viewport)):
            item = next((s for s in mod.node_group.interface.items_tree
                         if getattr(s, 'socket_type', '') == 'NodeSocketObject' and s.in_out == 'INPUT'), None)
            obj = socket_value(mod, item.identifier) if item else None
            if obj:
                sources.append(obj)
    return sources


def source_pair(moment, count):
    if count <= 1:
        return 0, 0, 0.0
    mean, second = moment[:2]
    last = count - 1
    mean = max(0.0, min(last, mean))
    # Variance distinguishes the last->first seam from interior segments.
    if count > 2 and 1e-6 < mean < last - 1e-6 and abs(second - last * mean) < 2e-5:
        return last, 0, 1.0 - mean / last
    a = min(int(math.floor(mean)), last - 1)
    return a, a + 1, mean - a


FLOAT_CAMERA = ('lens', 'shift_x', 'shift_y', 'sensor_width', 'sensor_height',
                'ortho_scale', 'clip_start', 'clip_end')
FLOAT_DOF = ('aperture_fstop', 'aperture_rotation', 'aperture_ratio')
PANO_FLOAT = ('fisheye_fov', 'fisheye_lens', 'latitude_min', 'latitude_max',
              'longitude_min', 'longitude_max', 'fisheye_polynomial_k0',
              'fisheye_polynomial_k1', 'fisheye_polynomial_k2',
              'fisheye_polynomial_k3', 'fisheye_polynomial_k4')


def focus_distance(obj, depsgraph):
    dof = obj.data.dof
    target = dof.focus_object
    if target is None:
        return dof.focus_distance
    target = target.evaluated_get(depsgraph)
    position = target.matrix_world.translation
    if dof.focus_subtarget and target.type == 'ARMATURE':
        bone = target.pose.bones.get(dof.focus_subtarget)
        if bone:
            position = target.matrix_world @ bone.matrix.translation
    # Blender focus is distance along the optical axis, rather than Euclidean range.
    axis = obj.matrix_world.col[2].to_3d().normalized()
    return abs((position - obj.matrix_world.translation).dot(axis))


def apply_settings(camera, a, b, weight, depsgraph):
    if camera.data == a.data or camera.data == b.data or camera.data.users > 1:
        camera.data = camera.data.copy()
    data = camera.data
    discrete = a.data if weight < 0.5 else b.data

    def assign(owner, name, value):
        if getattr(owner, name) != value:
            setattr(owner, name, value)

    for name in FLOAT_CAMERA:
        assign(data, name, (1-weight)*getattr(a.data, name) + weight*getattr(b.data, name))
    for name in ('type', 'sensor_fit'):
        assign(data, name, getattr(discrete, name))
    if data.type == 'PANO':
        for name in PANO_FLOAT:
            if hasattr(data, name):
                assign(data, name, (1-weight)*getattr(a.data, name) + weight*getattr(b.data, name))
        if hasattr(data, 'panorama_type'):
            assign(data, 'panorama_type', discrete.panorama_type)
    for name in FLOAT_DOF:
        assign(data.dof, name, (1-weight)*getattr(a.data.dof, name) + weight*getattr(b.data.dof, name))
    assign(data.dof, 'use_dof', discrete.dof.use_dof)
    assign(data.dof, 'aperture_blades', discrete.dof.aperture_blades)
    assign(data.dof, 'focus_object', None)
    assign(data.dof, 'focus_distance', (1-weight)*focus_distance(a, depsgraph) + weight*focus_distance(b, depsgraph))


def update(scene, depsgraph):
    global _busy
    if _busy:
        return
    _busy = True
    try:
        for camera in scene.objects:
            controller = camera.parent
            if camera.type != 'CAMERA' or not controller or camera.parent_type != 'VERTEX_3':
                continue
            generator = next((m for m in controller.modifiers if m.type == 'NODES' and is_generator(m.node_group)), None)
            if generator is None or not generator.node_group.get('camhp_inherit_version'):
                continue
            if not (generator.show_render if depsgraph.mode == 'RENDER' else generator.show_viewport):
                continue
            evaluated = controller.evaluated_get(depsgraph)
            evaluated_generator = evaluated.modifiers.get(generator.name)
            if not socket_value(evaluated_generator, generator.node_group['camhp_inherit_socket']):
                continue
            sources = source_objects(evaluated, evaluated_generator, depsgraph.mode == 'RENDER')
            if not sources:
                continue
            attribute = evaluated.data.attributes.get(ATTRIBUTE)
            if not attribute or not len(attribute.data):
                continue
            i, j, weight = source_pair(attribute.data[0].vector, len(sources))
            a, b = sources[i], sources[j]
            if getattr(a, 'type', '') != 'CAMERA' or getattr(b, 'type', '') != 'CAMERA':
                continue
            if a.original == camera or b.original == camera:
                continue
            apply_settings(camera, a.evaluated_get(depsgraph), b.evaluated_get(depsgraph), weight, depsgraph)
    finally:
        _busy = False


@persistent
def on_update(scene, depsgraph):
    update(scene, depsgraph)


@persistent
def on_load(_):
    for obj in bpy.data.objects:
        for modifier in obj.modifiers:
            if modifier.type == 'NODES':
                group = modifier.node_group
                if is_generator(group) and group.library is None:
                    ensure_group(group)


@persistent
def on_render(scene, *_):
    update(scene, bpy.context.evaluated_depsgraph_get())


def migrate_existing():
    # Add-on registration runs inside RestrictBlend. The timer executes once
    # after normal BlendData/context access has been restored.
    on_load(None)
    return None


def register():
    for name, callback in (('depsgraph_update_post', on_update), ('frame_change_post', on_update),
                           ('load_post', on_load), ('render_pre', on_render)):
        handlers = getattr(bpy.app.handlers, name)
        if callback not in handlers:
            handlers.append(callback)

    if not bpy.app.timers.is_registered(migrate_existing):
        bpy.app.timers.register(migrate_existing, first_interval=0.0)


def unregister():
    if bpy.app.timers.is_registered(migrate_existing):
        bpy.app.timers.unregister(migrate_existing)
    for name, callback in (('depsgraph_update_post', on_update), ('frame_change_post', on_update),
                           ('load_post', on_load), ('render_pre', on_render)):
        handlers = getattr(bpy.app.handlers, name)
        if callback in handlers:
            handlers.remove(callback)
