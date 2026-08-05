# 折叠按钮：样式完全对齐系统内置 N 面板折叠 tab（area.cc region_draw_azone_tab_plus /
# region_draw_azone_tab_arrow / draw_azone_arrow）
# 方形背景 + RIGHTARROW 方向箭头，点击切换右侧按钮列的显示/隐藏
#
# 布局（参考 N 面板折叠后的图标）：
#   - 位于视窗右边缘（比按钮列更靠右），垂直居中于按钮列中心
#   - 面板隐藏时箭头朝右（RIGHTARROW，与系统折叠 tab 一致 = 展开方向）
#   - 面板可见时翻转朝左（指向按钮列 = 收起方向）

import bpy
from bpy.types import Gizmo, GizmoGroup
from mathutils import Matrix

from .public_gizmo import PublicGizmo

# --- 形状顶点 ---
# 渲染尺寸 = 形状坐标 × scale_basis × UI_SCALE_FAC（C: wm_gizmo_calculate_scale，
# UI_SCALE_FAC = U.scale_factor = dpi/72）。
# 目标：与系统内置 N 面板折叠 tab 视觉一致（实测 14px 宽 × 27px 高，固定值）。
# 因此形状用 14×27 单位，并在 draw_prepare 中令 scale_basis = 1/UI_SCALE_FAC，
# 使实际渲染 = 14×27 屏幕像素（固定值，不随 UI 缩放变化）。
# （storytools 20×12 盒体是过宽的参照，不采用；N 面板 tab 才是统一视觉基准。）
x_l = -7    # X Left  (-7)
x_r = 7     # X Right (7)
y_d = -13.5 # Y Down
y_u = 13.5  # Y Up
# -> 盒体 14×27 单位，渲染后 = 14×27 px

# 盒体：14×27，仅右缘（外缘/贴屏幕边缘侧）R=4 圆角 —— 复刻系统 azone tab
# （area.cc region_draw_azone_tab_arrow: draw_roundbox_aa(radius=4.0)，AE_RIGHT 侧圆角
#   CNR_TOP_RIGHT|CNR_BOTTOM_RIGHT，内侧直角）。圆角用 8 段 1/4 圆弧逼近。
toggler_shape_verts = (
    # 左矩形
    (-7.0, -13.5), (3.0, -13.5), (3.0, 13.5),
    (-7.0, -13.5), (3.0, 13.5), (-7.0, 13.5),
    # 中带
    (3.0, -9.5), (7.0, -9.5), (7.0, 9.5),
    (3.0, -9.5), (7.0, 9.5), (3.0, 9.5),
    # 右下圆角扇
    (3.0, -9.5), (3.0, -13.5), (3.78, -13.423),
    (3.0, -9.5), (3.78, -13.423), (4.531, -13.196),
    (3.0, -9.5), (4.531, -13.196), (5.222, -12.826),
    (3.0, -9.5), (5.222, -12.826), (5.828, -12.328),
    (3.0, -9.5), (5.828, -12.328), (6.326, -11.722),
    (3.0, -9.5), (6.326, -11.722), (6.696, -11.031),
    (3.0, -9.5), (6.696, -11.031), (6.923, -10.28),
    (3.0, -9.5), (6.923, -10.28), (7.0, -9.5),
    # 右上圆角扇
    (3.0, 9.5), (7.0, 9.5), (6.923, 10.28),
    (3.0, 9.5), (6.923, 10.28), (6.696, 11.031),
    (3.0, 9.5), (6.696, 11.031), (6.326, 11.722),
    (3.0, 9.5), (6.326, 11.722), (5.828, 12.328),
    (3.0, 9.5), (5.828, 12.328), (5.222, 12.826),
    (3.0, 9.5), (5.222, 12.826), (4.531, 13.196),
    (3.0, 9.5), (4.531, 13.196), (3.78, 13.423),
    (3.0, 9.5), (3.78, 13.423), (3.0, 13.5),
)

# 箭头：复刻系统 azone tab 箭头（area.cc draw_azone_arrow，AE_RIGHT_TO_TOPLEFT 朝向右侧）
# 六边形 + 左侧 V 凹口，尺寸 7.25×11.6（size = 0.2*widget_unit，本环境 widget_unit=29）。
# 指向右侧 = 面板收起于右缘（与系统折叠 tab 的 RIGHTARROW 一致）。
right_arrow_verts = (
    (2.9, 0.0), (-2.9, 5.8), (-4.35, 4.35),
    (2.9, 0.0), (-4.35, 4.35), (0.0, 0.0),
    (2.9, 0.0), (0.0, 0.0), (-4.35, -4.35),
    (2.9, 0.0), (-4.35, -4.35), (-2.9, -5.8),
    (2.9, 0.0), (-2.9, -5.8), (-2.9, 5.8),
)

# 按钮列整体左移量（px）：给右侧折叠按钮留出间距，避免误触
# （相机按钮视觉半径 = scale_basis × ui_scale，env 1.5 下为 21px；margin=20 保证 ≥18px 间隙）
BUTTON_COLUMN_MARGIN = 20

# X 轴镜像（面板可见时箭头朝左 = 指向按钮列收起方向；隐藏时保持朝右 = 系统 RIGHTARROW）
horizontal_flip_mat = Matrix.Diagonal((-1, 1, 1, 1))


def _get_button_panel_metrics(context, margin=0):
    """复制 utils/gizmo.py offset_2d_gizmo 的布局起点计算，返回按钮列第一个按钮的位置

    与 offset_2d_gizmo 保持同步（含 margin 左移），确保折叠按钮与按钮列在同一坐标系下。
    """
    ui_scale = bpy.context.preferences.system.dpi * (1 / 72)
    region = context.region
    step = 30 * ui_scale
    icon_scale = (80 * 0.35) / 2  # 14
    start_x = region.width - (icon_scale * ui_scale + step) / 2 - margin
    start_y = region.height

    # 区域重叠：侧面板与标题栏占位
    if context.preferences.system.use_region_overlap:
        for r in context.area.regions:
            if r.type == 'UI':
                start_x -= r.width
            elif r.type == 'HEADER':
                start_y -= r.height

    # 坐标轴占位
    mini_axis = context.preferences.view.mini_axis_type
    if mini_axis == 'MINIMAL':
        size = context.preferences.view.mini_axis_size * ui_scale * 2
        start_y -= size + step * 2
    elif mini_axis == 'GIZMO':
        size = context.preferences.view.gizmo_size_navigate_v3d * ui_scale * 1.2
        start_y -= size + step * 2
    else:  # 'NONE'
        start_y -= step * 2

    # 导航控件占位
    if context.preferences.view.show_navigate_ui:
        start_y -= (icon_scale * ui_scale + step) * 3
    else:
        start_y -= step * 2 * ui_scale

    return start_x, start_y, step, icon_scale, ui_scale


class CAMHP_GT_toggler_shape_widget(Gizmo):
    bl_idname = "CAMHP_GT_toggler_shape_widget"

    __slots__ = (
        "custom_shape",
        "arrow_shape",
        "init_mouse_x",
        "init_mouse_y",
        "mx",
        "my",
    )

    def draw(self, context):
        # 盒体 —— 颜色对齐系统 azone tab（draw_roundbox_aa {0.05,0.05,0.05,0.6} 合成 ≈ rgb(33)）
        self.color = (0.13, 0.13, 0.13)
        self.color_highlight = (0.18, 0.18, 0.18)
        self.draw_custom_shape(self.custom_shape)

        # 方向箭头 —— 颜色对齐系统箭头（{0.8,0.8,0.8,0.4} 合成 ≈ rgb(101)）
        self.color_highlight = self.color = (0.40, 0.40, 0.40)
        self.draw_custom_shape(self.arrow_shape)

    def test_select(self, context, location):
        # 命中区 = 渲染盒体（scale_basis × UI_SCALE_FAC = 1 ⇒ 1 单位 = 1px，
        # matrix_basis 平移量不受 scale_final 影响；旧版 ×px_scale 使命中区比视觉大 1.5 倍，已移除）
        t = self.matrix_basis.to_translation()
        x_min = t.x + x_l
        x_max = t.x + x_r
        y_min = t.y + y_d
        y_max = t.y + y_u
        select = 1 if x_min < location[0] < x_max and y_min < location[1] < y_max else -1

        return select

    def setup(self):
        # 注意：单个 Gizmo 的 setup 无 context 参数（C: wm_gizmo.cc gz->type->setup(gz)，
        # 只有 GizmoGroup.setup 才有 context）。scale_basis 补偿在 draw_prepare 中设置。
        if not hasattr(self, "custom_shape"):
            self.custom_shape = self.new_custom_shape('TRIS', toggler_shape_verts)

        if not hasattr(self, "arrow_shape"):
            self.arrow_shape = self.new_custom_shape('TRIS', right_arrow_verts)

    def invoke(self, context, event):
        self.mx = self.init_mouse_x = event.mouse_x
        self.my = self.init_mouse_y = event.mouse_y

        return {'RUNNING_MODAL'}

    def exit(self, context, cancel):
        # 拖拽超过 10px 视为误操作，不切换（参考 storytools）
        if abs(self.init_mouse_x - self.mx) > 10 or abs(self.init_mouse_y - self.my) > 10:
            return

        context.scene.camhp_show_panel = not context.scene.camhp_show_panel

        # 刷新所有 3D 视图
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    def modal(self, context, event, tweak):
        self.mx = event.mouse_x
        self.my = event.mouse_y

        return {'RUNNING_MODAL'}


class Button2DCollapseGizmos(GizmoGroup, PublicGizmo):
    bl_idname = "Button_UI_2D_collapse_gizmos"
    bl_label = "2D Button Collapse Gizmos"

    def setup(self, context):
        self.gz_toggle = self.gizmos.new(CAMHP_GT_toggler_shape_widget.bl_idname)

    def draw_prepare(self, context):
        # 当前按钮列按钮数量（相机视图 5 个 / 非相机视图 4 个）
        region_3d = context.space_data.region_3d
        if region_3d.view_perspective == "CAMERA":
            count = 5
        else:
            count = 4

        start_x, start_y, step, icon_scale, ui_scale = _get_button_panel_metrics(
            context, margin=BUTTON_COLUMN_MARGIN)

        # 垂直居中于按钮列中心（第 0 个按钮 y - 步距 * (count-1)/2）
        center_y = start_y - step * (count - 1) / 2

        # 贴视窗右边缘（参考 N 面板折叠后的图标），比按钮列更靠右；
        # 区域重叠时随按钮列一起避开侧面板
        edge_x = context.region.width - x_r  # 盒体右缘正好贴边（14px 宽 -> 占 W-14..W）
        if context.preferences.system.use_region_overlap:
            for r in context.area.regions:
                if r.type == 'UI':
                    edge_x -= r.width

        self.gz_toggle.matrix_basis = Matrix.Translation((edge_x, center_y, 0))
        # 抵消 C 侧 UI_SCALE_FAC：scale_final = scale_basis × UI_SCALE_FAC（C: wm_gizmo_calculate_scale）。
        # 令 scale_basis = 1/ui_scale ⇒ 渲染 = 形状单位 = 精确 14×27 px（与 N 面板 tab 一致，不随 UI 缩放变化）
        self.gz_toggle.scale_basis = 1.0 / ui_scale

        # 面板隐藏：箭头朝右（RIGHTARROW，与系统折叠 tab 一致的展开方向）
        # 面板可见：翻转朝左（指向按钮列 = 收起方向）
        if getattr(context.scene, "camhp_show_panel", True):
            self.gz_toggle.matrix_basis = self.gz_toggle.matrix_basis @ horizontal_flip_mat


def register():
    bpy.types.Scene.camhp_show_panel = bpy.props.BoolProperty(
        name="Show Panel",
        description="显示/隐藏 CameraHelper 右侧按钮列",
        default=True,
    )
    bpy.utils.register_class(CAMHP_GT_toggler_shape_widget)
    bpy.utils.register_class(Button2DCollapseGizmos)


def unregister():
    bpy.utils.unregister_class(Button2DCollapseGizmos)
    bpy.utils.unregister_class(CAMHP_GT_toggler_shape_widget)
    del bpy.types.Scene.camhp_show_panel
