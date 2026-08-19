import bpy
import math

from ..utils import get_operator_bl_idname


def fit_camera_frame(scn, region, rv3d):
    """让相机视图边框精确贴合视窗（替代 view_center_camera 的 +4px 边距与中间态）。

    复刻 C 层 view3d_center_camera_exec + BKE_screen_view3d_zoom_from_fac：
      边框宽 = xsch·zoom_to_fac(camzoom)，贴满视窗 → zoom_to_fac = min(winx/xsch, winy/(ysch·yasp/xasp))
      camzoom = (2√fac − √2) × 50
    """
    xsch = scn.render.resolution_x * scn.render.resolution_percentage / 100.0
    ysch = scn.render.resolution_y * scn.render.resolution_percentage / 100.0
    ycor = scn.render.pixel_aspect_y / scn.render.pixel_aspect_x
    if region is None or xsch <= 0.0 or ysch <= 0.0:
        return
    fac = min(region.width / xsch, region.height / (ysch * ycor))
    if fac <= 0.0:
        return
    rv3d.view_camera_offset = (0.0, 0.0)
    rv3d.view_camera_zoom = max(-30.0, min((2.0 * math.sqrt(fac) - math.sqrt(2.0)) * 50.0, 600.0))


class AddCamera(bpy.types.Operator):
    """Add View Camera\nCtrl Left Click: Add Motion Camera"""
    bl_idname = get_operator_bl_idname("add_camera")
    bl_label = 'Add View Camera'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        has_camera = any(o.type == 'CAMERA' for o in context.scene.objects)
        if not has_camera and event.ctrl:
            return self.execute(context)
        elif event.ctrl:
            bpy.ops.camhp.add_motion_cams('INVOKE_DEFAULT')
            bpy.ops.ed.undo_push()
            return {'FINISHED'}
        else:
            return self.execute(context)

    def execute(self, context):
        region_3d = context.space_data.region_3d
        v3d = context.space_data
        region = context.region
        if region is None or region.type != 'WINDOW':
            region = next((r for r in context.area.regions if r.type == 'WINDOW'), None)

        # 统一公式 · 分辨率 = 视窗：相机边框宽高比由渲染分辨率决定，
        # 只有同步后 camzoom 贴边才能在任意视窗分辨率下成立（与代理接管方案一致）
        scn = context.scene
        if (region is not None and
                (scn.render.resolution_x != region.width or scn.render.resolution_y != region.height or
                 scn.render.resolution_percentage != 100)):
            scn.render.resolution_x = region.width
            scn.render.resolution_y = region.height
            scn.render.resolution_percentage = 100

        if region_3d.is_perspective:
            bpy.ops.object.camera_add()
            cam = context.active_object
            cam.name = "Camera"
            cam.data.show_name = True
            context.scene.camera = cam

            # 进入相机视图前不动视图：直接写相机变换（等价 camera_to_view，
            # 但不强制 rv3d->persp=RV3D_CAMOB，避免"先跳后收"的抖动）
            cam.matrix_world = region_3d.view_matrix.inverted()
            cam.data.sensor_width = 36
            # 焦距：自由透视视口 zoom=2.0 → 相机焦距 = v3d.lens/2（numpy 验证，
            # 使渲染与自由视口内容一致；替代早期硬编码 26.23 经验值）
            if region_3d.view_perspective == 'CAMERA':
                # 从相机视图创建（如代理相机）→ 沿用源相机焦距保证视图一致
                src = v3d.camera or context.scene.camera
                cam.data.lens = src.data.lens if src and src.type == 'CAMERA' else v3d.lens / 2.0
            else:
                cam.data.lens = v3d.lens / 2.0
        else:
            view_inv = region_3d.view_matrix.inverted()
            view_dist = region_3d.view_distance

            cam_data = bpy.data.cameras.new('Camera')
            cam_data.type = 'ORTHO'
            cam_data.ortho_scale = view_dist * 1.373
            cam = bpy.data.objects.new('Camera', cam_data)
            context.collection.objects.link(cam)
            cam.data.show_name = True
            context.scene.camera = cam

            cam.location = view_inv.translation
            cam.rotation_euler = view_inv.to_euler()

        # 进入相机视图：矩阵已与当前视图一致 → 视图不动、无抖动
        region_3d.view_perspective = 'CAMERA'
        # 精确贴边：直接按渲染边框计算 camzoom（替代 view_center_camera 的 +4px 与中间态）
        fit_camera_frame(context.scene, region, region_3d)

        context.region.tag_redraw()

        return {"FINISHED"}
