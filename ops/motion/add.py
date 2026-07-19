import bpy
import gpu
from bpy.app.translations import pgettext_iface as tip_
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from ..old.draw_utils.bl_ui_button import BL_UI_Button
from ..old.draw_utils.bl_ui_drag_panel import BL_UI_Drag_Panel
from ..old.draw_utils.bl_ui_draw_op import BL_UI_OT_draw_operator
from ..old.draw_utils.bl_ui_label import BL_UI_Label
from ...utils.asset import AssetDir, get_asset_dir


def get_obj_2d_loc(obj, context):
    r3d = context.space_data.region_3d
    loc = location_3d_to_region_2d(context.region, r3d, obj.matrix_world.translation)
    return loc


def load_asset(name: str, asset_type: str, filepath: str) -> bpy.types.NodeTree | bpy.types.Object:
    """load asset into current scene from giving asset type"""
    if asset_type == 'objects':
        attr = 'objects'
    elif asset_type == 'node_groups':
        attr = 'node_groups'
    else:
        raise ValueError('asset_type not support')

    # reuse existing data
    data_lib = getattr(bpy.data, attr)
    if name in data_lib and asset_type in {'node_groups'}:
        return data_lib[name]

    with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
        src = getattr(data_from, attr)
        res = [x for x in src if x == name]
        if not res:
            raise ValueError(f'No {name} found in {filepath}')
        setattr(data_to, attr, res)
    # clear asset mark
    obj = getattr(data_to, attr)[0]
    obj.asset_clear()
    return obj


class CAMHP_PT_add_motion_cams(BL_UI_OT_draw_operator, bpy.types.Operator):
    bl_idname = 'camhp.add_motion_cams'
    bl_label = 'Add Motion Camera'
    bl_options = {'UNDO_GROUPED', 'INTERNAL', 'BLOCKING', 'GRAB_CURSOR'}

    buttons = list()
    controller = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.buttons.clear()

        # 面板提示
        self.panel = BL_UI_Drag_Panel(100, 300, 300, 290)
        self.panel.bg_color = 0.2, 0.2, 0.2, 0.9

        self.label_tip1 = BL_UI_Label(20, 120, 40, 50)
        self.label_tip1.text = tip_('Left Click->Camera Name to Add Source')
        self.label_tip1.text_size = 22

        self.label_tip2 = BL_UI_Label(20, 95, 40, 50)
        self.label_tip2.text = tip_('Right Click->End Add Mode')
        self.label_tip2.text_size = 22

        self.label_tip3 = BL_UI_Label(20, 70, 40, 50)
        self.label_tip3.text = tip_('ESC->Cancel')
        self.label_tip3.text_size = 22

        # 为每个相机添加一个按钮
        for obj in bpy.context.view_layer.objects:
            if obj.type != 'CAMERA': continue

            loc = get_obj_2d_loc(obj, bpy.context)
            if loc is None: continue  # 相机处于较远的位置
            x, y = loc

            btn = BL_UI_Button(x, y, 120, 30)
            btn.bg_color = (0.1, 0.1, 0.1, 0.8)
            btn.hover_bg_color = (0.6, 0.6, 0.6, 0.8)
            btn.text = obj.name
            # button1.set_image("//img/scale_24.png")
            # self.button1.set_image_size((24,24))
            # button1.set_image_position((4, 2))
            btn.set_mouse_down(self.add_motion_cam, obj)
            setattr(btn, 'bind_obj', obj.name)

            self.buttons.append(btn)

    def cancel(self, context):
        if getattr(self, 'new_cam', None) is not None:
            bpy.data.objects.remove(self.new_cam)
        if getattr(self, 'controller', None) is not None:
            bpy.data.objects.remove(self.controller)
        return {'CANCELLED'}

    def on_invoke(self, context, event):
        self._added_cameras = []  # 记录已选择的相机（按顺序）
        widgets_panel = [self.label_tip1, self.label_tip2, self.label_tip3]
        widgets = [self.panel]
        widgets += widgets_panel

        self.init_widgets(context, widgets_panel + self.buttons)

        self.panel.add_widgets(widgets_panel)

        # Open the panel at the mouse location
        self.panel.set_location(context.area.width * 0.8,
                                context.area.height * 1)

        # 创建相机
        cam_data = bpy.data.cameras.new(name='Camera')
        cam_obj = bpy.data.objects.new('Camera', cam_data)

        if bpy.app.version >= (4, 3, 0):
            asset_motioncam = get_asset_dir(AssetDir.ASSET_BLEND_WITH_GIZMO.value)
        else:
            asset_motioncam = get_asset_dir(AssetDir.ASSET_BLEND.value)
        controller_obj = load_asset(name='Controller', asset_type='objects', filepath=str(asset_motioncam))
        asset_motion_src = load_asset(name='MotionCameraSource', asset_type='node_groups',
                                      filepath=str(asset_motioncam))
        asset_motion_gen = load_asset(name='MotionCamera', asset_type='node_groups',
                                      filepath=str(asset_motioncam))

        context.collection.objects.link(cam_obj)
        context.collection.objects.link(controller_obj)
        # 设置
        cam_obj.data.show_name = True
        cam_obj.name = 'Motion Camera'
        # deselect all
        bpy.ops.object.select_all(action='DESELECT')
        context.view_layer.objects.active = controller_obj
        controller_obj.select_set(True)
        cam_obj.select_set(True)
        # toggle edit mode
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.object.vertex_parent_set()
        bpy.ops.object.mode_set(mode='OBJECT')

        self.controller = controller_obj
        self.new_cam = cam_obj
        self.ng_motion_src = asset_motion_src
        self.ng_motion_gen = asset_motion_gen

    def _get_socket_identifier(self, node_group, socket_type='OBJECT'):
        """动态查找节点组中指定类型的输入socket的identifier"""
        for item in node_group.interface.items_tree:
            if hasattr(item, 'is_output'):
                if item.is_output:
                    continue
            elif hasattr(item, 'in_out') and item.in_out == 'OUTPUT':
                continue
            if hasattr(item, 'socket_type') and item.socket_type == socket_type:
                return item.identifier
        return None

    def _set_modifier_socket_value(self, mod, socket_id, value):
        """设置修改器socket的值，兼容Blender 5.x的新API"""
        if bpy.app.version >= (5, 0, 0):
            # Blender 5.x: mod.properties.inputs.<socket_id>.value = value
            try:
                sock = getattr(mod.properties.inputs, socket_id)
                sock.value = value
                return
            except Exception:
                pass
        # Fallback: old API (Blender 4.x) - direct IDProperty access
        try:
            mod[socket_id] = value
        except Exception:
            pass

    def add_motion_cam(self, obj):
        bpy.context.view_layer.objects.active = self.controller
        self.controller.select_set(True)

        if 'MotionCamera' not in self.controller.modifiers:
            mod_gen = self.controller.modifiers.new(type='NODES', name='MotionCamera')
            mod_gen.node_group = self.ng_motion_gen
            if hasattr(mod_gen, 'show_group_selector'):
                mod_gen.show_group_selector = False

        mod = self.controller.modifiers.new(type='NODES', name='MotionCameraSource')
        mod.node_group = self.ng_motion_src
        if hasattr(mod, 'show_group_selector'):
            mod.show_group_selector = False

        socket_id = self._get_socket_identifier(self.ng_motion_src, 'OBJECT')
        if not socket_id:
            socket_id = 'Socket_2'

        self._set_modifier_socket_value(mod, socket_id, obj)

        mod.show_viewport = False
        mod.show_viewport = True

        # 记录已选择的相机名称（去重）
        if obj.name not in self._added_cameras:
            self._added_cameras.append(obj.name)

        # 更新提示文字显示已选数量
        self.label_tip1.text = tip_(f'Selected: {len(self._added_cameras)} cams | Left Click->Add Source')

        # move to bottom
        for i, m in enumerate(self.controller.modifiers):
            if m.name == 'MotionCamera':
                self.controller.modifiers.move(i, len(self.controller.modifiers) - 1)
                self.controller.modifiers.active = m
                break
        bpy.context.area.tag_redraw()

    def draw_callback_px(self, op, context):
        """重写draw_callback_px：画UI控件 + 运动路径可视化"""
        # 1. 先画父类UI控件
        for widget in self.widgets:
            widget.draw()

        # 2. 画运动相机路径可视化（箭头+虚线+标记点）
        if hasattr(self, '_added_cameras') and len(self._added_cameras) >= 1:
            self._draw_motion_path(context)

    def _draw_motion_path(self, context):
        """在视口中绘制运动相机路径：箭头指示器+虚线+标记点"""
        if context.area.type != 'VIEW_3D':
            return

        # 获取所有已选相机的2D屏幕坐标
        points_2d = []
        for cam_name in self._added_cameras:
            obj = bpy.data.objects.get(cam_name)
            if obj is None:
                continue
            loc = get_obj_2d_loc(obj, context)
            if loc is None:
                continue
            points_2d.append(loc)

        if not points_2d:
            return

        shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')

        # ---------- 虚线连接线（选中的相机之间） ----------
        if len(points_2d) >= 2:
            gpu.state.line_width_set(2.0)
            shader.bind()
            shader.uniform_float("color", (1.0, 0.6, 0.0, 0.7))  # 橙色半透明

            vertices = []
            for i in range(len(points_2d) - 1):
                p1 = Vector(points_2d[i])
                p2 = Vector(points_2d[i + 1])
                direction = p2 - p1
                total_length = direction.length
                if total_length < 1.0:
                    continue
                direction.normalize()
                dash_len = 8.0
                gap_len = 5.0
                step = dash_len + gap_len
                pos = 0.0
                while pos < total_length:
                    start_p = p1 + direction * pos
                    end_p = p1 + direction * min(pos + dash_len, total_length)
                    vertices.extend([(start_p.x, start_p.y), (end_p.x, end_p.y)])
                    pos += step

            if vertices:
                batch = batch_for_shader(shader, 'LINES', {"pos": vertices})
                batch.draw(shader)

        # ---------- 方向箭头（每两点中间） ----------
        if len(points_2d) >= 2:
            shader.bind()
            shader.uniform_float("color", (1.0, 0.8, 0.0, 1.0))  # 亮黄色
            gpu.state.line_width_set(2.5)

            arrow_verts = []
            for i in range(len(points_2d) - 1):
                p1 = Vector(points_2d[i])
                p2 = Vector(points_2d[i + 1])
                mid = (p1 + p2) * 0.5
                direction = (p2 - p1).normalized()
                if direction.length < 0.001:
                    continue
                perp = Vector((-direction.y, direction.x))
                arrow_size = 12.0
                tip = mid + direction * arrow_size
                left = mid - direction * arrow_size * 0.3 + perp * arrow_size * 0.5
                right = mid - direction * arrow_size * 0.3 - perp * arrow_size * 0.5
                arrow_verts.extend([
                    (tip.x, tip.y), (left.x, left.y),
                    (tip.x, tip.y), (right.x, right.y),
                ])

            if arrow_verts:
                batch = batch_for_shader(shader, 'LINES', {"pos": arrow_verts})
                batch.draw(shader)

        # ---------- 标记点（每个已选相机位置） ----------
        gpu.state.line_width_set(1.5)
        shader.bind()
        shader.uniform_float("color", (0.0, 1.0, 1.0, 0.9))  # 青色

        marker_verts = []
        marker_size = 6.0
        for p in points_2d:
            marker_verts.extend([
                (p.x - marker_size, p.y), (p.x + marker_size, p.y),
                (p.x, p.y - marker_size), (p.x, p.y + marker_size),
            ])

        if marker_verts:
            batch = batch_for_shader(shader, 'LINES', {"pos": marker_verts})
            batch.draw(shader)

        # ---------- 顺序序号 ----------
        import blf
        blf.size(0, 14)
        for i, p in enumerate(points_2d):
            blf.color(0, 0.0, 0.0, 0.0, 0.8)
            blf.position(0, p.x + 8, p.y + 4, 0)
            blf.draw(0, str(i + 1))
            blf.color(0, 1.0, 1.0, 0.0, 1.0)
            blf.position(0, p.x + 7, p.y + 3, 0)
            blf.draw(0, str(i + 1))

        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')

    def modal(self, context, event):

        for i, btn in enumerate(self.buttons):
            if not hasattr(btn, 'bind_obj'): continue

            obj_name = getattr(btn, 'bind_obj')
            obj = bpy.data.objects.get(obj_name)

            if obj is None: continue
            x, y = get_obj_2d_loc(obj, context)
            btn.update(x, y)
            context.area.tag_redraw()

        return super().modal(context, event)
