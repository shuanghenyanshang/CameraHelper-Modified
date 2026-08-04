import bpy

from .public_gizmo import PublicGizmo
from ..camera_thumbnails import CameraThumbnails
from ..utils.gizmo import offset_2d_gizmo


class Gizmos:
    def create_gizmo(self, name) -> bpy.types.Gizmo:
        gz = self.gizmos.new(name)
        gz.icon = "VIEW_PERSPECTIVE"
        gz.draw_options = {"BACKDROP", "OUTLINE"}
        gz.use_tooltip = True
        gz.use_draw_modal = True
        gz.alpha = 0.8
        gz.color = 0.08, 0.08, 0.08
        gz.color_highlight = 0.28, 0.28, 0.28
        gz.alpha_highlight = 0.8

        gz.scale_basis = (80 * 0.35) / 2  # Same as buttons defined in C
        return gz

    def create_adjust_camera(self, context):
        # 调整焦距控件
        from ..ops.adjust_camera_lens import AdjustCameraLens

        gz = self.create_gizmo("GIZMO_GT_button_2d")
        gz.icon = "VIEW_PERSPECTIVE"
        gz.target_set_operator(AdjustCameraLens.bl_idname)
        self.gz_move = gz

    def create_camera_settings(self, context):
        # 相机设置
        gz = self.create_gizmo("GIZMO_GT_button_2d")
        gz.icon = "PROPERTIES"
        props = gz.target_set_operator("wm.call_menu")
        props.name = "CAMHP_MT_popup_cam_settings"
        self.gz_setttings = gz

    def create_add_camera(self, context):
        from ..ops.add_camera import AddCamera

        gz = self.create_gizmo("GIZMO_GT_button_2d")
        gz.icon = "ADD"
        gz.target_set_operator(AddCamera.bl_idname)
        self.gz_add_cam = gz

    def create_camera_preview(self, context):
        from ..ops.preview_camera import PreviewCamera

        gz = self.create_gizmo("GIZMO_GT_button_2d")
        gz.use_event_handle_all = True
        gz.icon = "IMAGE_PLANE"
        gz.target_set_operator(PreviewCamera.bl_idname)
        self.gz_cam_pv = gz

    def create_camera_switch(self, context):
        from ..ops.switch_camera import SwitchCameraMenu

        gz = self.create_gizmo("GIZMO_GT_button_2d")
        gz.use_event_handle_all = True
        gz.icon = "VIEW_CAMERA"
        gz.target_set_operator(SwitchCameraMenu.bl_idname)
        self.gz_cam_switch = gz

    def create_lock_camera_toggle(self, context):
        from ..ops.toggle_lock_camera import ToggleLockCamera

        gz = self.create_gizmo("GIZMO_GT_button_2d")
        gz.use_event_handle_all = True
        gz.icon = "LOCKED"
        gz.target_set_operator(ToggleLockCamera.bl_idname)
        self.gz_lock_cam = gz


class Button2DGizmos(bpy.types.GizmoGroup, Gizmos, PublicGizmo):
    bl_idname = "Button_UI_2D_gizmos"

    bl_label = "2D Button Gizmos"

    def setup(self, context):
        self.create_add_camera(context)
        self.create_camera_preview(context)
        self.create_camera_settings(context)
        self.create_adjust_camera(context)
        self.create_camera_switch(context)
        try:
            self.create_lock_camera_toggle(context)
        except Exception:
            import traceback
            traceback.print_exc()

    def draw_prepare(self, context):
        for i, gz in enumerate(self.gizmos):
            gz.hide = True

        region_3d = context.space_data.region_3d
        view_perspective = region_3d.view_perspective

        gizmos = self.gizmos
        if view_perspective == "CAMERA":  # 相机
            gizmos = [
                self.gz_move,
                self.gz_setttings,
                self.gz_cam_switch,
                self.gz_cam_pv,
                self.gz_lock_cam if hasattr(self, "gz_lock_cam") else None,
            ]
        else:
            gizmos = [
                self.gz_add_cam,
                self.gz_cam_switch,
                self.gz_cam_pv,
                self.gz_lock_cam if hasattr(self, "gz_lock_cam") else None,
            ]

        for i, gz in enumerate(gizmos):
            if gz is None:
                continue
            offset_2d_gizmo(context, gz, i)
            gz.hide = False

        if hasattr(self, "gz_lock_cam"):
            if context.space_data.lock_camera:
                self.gz_lock_cam.color = 0.5, 0.1, 0.1
                self.gz_lock_cam.color_highlight = 0.7, 0.2, 0.2
                self.gz_lock_cam.alpha = 1.0
            else:
                self.gz_lock_cam.color = 0.08, 0.08, 0.08
                self.gz_lock_cam.color_highlight = 0.28, 0.28, 0.28
                self.gz_lock_cam.alpha = 0.8
        context.area.tag_redraw()
        self.refresh(context)

    def refresh(self, context):
        CameraThumbnails.update_2d_button_color(context, self.gz_cam_pv)

        if hasattr(self, "gz_lock_cam"):
            if context.space_data.lock_camera:
                self.gz_lock_cam.color = 0.5, 0.1, 0.1
                self.gz_lock_cam.color_highlight = 0.7, 0.2, 0.2
                self.gz_lock_cam.alpha = 1.0
            else:
                self.gz_lock_cam.color = 0.08, 0.08, 0.08
                self.gz_lock_cam.color_highlight = 0.28, 0.28, 0.28
                self.gz_lock_cam.alpha = 0.8
        context.area.tag_redraw()
