import bpy
import math

from ..utils import get_operator_bl_idname


def camera_frame_zoom_factor(scn, region, sensor_fit='AUTO'):
    """按原生 viewplane 计算边框缩放，只依赖宽高比，不依赖输出像素数或百分比。"""
    xsch = scn.render.resolution_x
    ysch = scn.render.resolution_y
    ycor = scn.render.pixel_aspect_y / scn.render.pixel_aspect_x
    if region is None or xsch <= 0.0 or ysch <= 0.0:
        return 1.0
    viewfac = (region.width if sensor_fit == 'HORIZONTAL' else region.height
               if sensor_fit == 'VERTICAL' else max(region.width, region.height))
    renderfac = (xsch if sensor_fit == 'HORIZONTAL' else ysch * ycor
                 if sensor_fit == 'VERTICAL' else max(xsch, ysch * ycor))
    fac = min(region.width / (viewfac * xsch / renderfac),
              region.height / (viewfac * ysch * ycor / renderfac))
    zoom = max(-30.0, min((2.0 * math.sqrt(fac) - math.sqrt(2.0)) * 50.0, 600.0))
    return (math.sqrt(2.0) + zoom / 50.0) ** 2 / 4.0


def fit_camera_frame(scn, region, rv3d):
    """原生相机框贴边；返回实际缩放，供共用的视角补偿使用。"""
    sensor_fit = scn.camera.data.sensor_fit if scn.camera else 'AUTO'
    fac = camera_frame_zoom_factor(scn, region, sensor_fit)
    rv3d.view_camera_offset = (0.0, 0.0)
    rv3d.view_camera_zoom = (2.0 * math.sqrt(fac) - math.sqrt(2.0)) * 50.0
    return fac


class AddCamera(bpy.types.Operator):
    """Add View Camera\nCtrl Left Click: Add Motion Camera\nShift Left Click: Add Proxy Camera"""
    bl_idname = get_operator_bl_idname("add_camera")
    bl_label = 'Add View Camera'
    bl_description = ("按设置好的输出分辨率创建当前视角的相机，不改写分辨率；"
                      "Ctrl 点击创建运动相机；Shift 点击创建代理相机，"
                      "需要先在偏好设置启用代理相机，代理才使用视口分辨率")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        # Shift+左键：创建代理相机（视口=相机=渲染；误删 __ProxyCamera 后的手动恢复手段）
        if event.shift:
            return self._create_proxy_camera(context)
        has_camera = any(o.type == 'CAMERA' for o in context.scene.objects)
        if not has_camera and event.ctrl:
            return self._execute_with_report(context)
        elif event.ctrl:
            bpy.ops.camhp.add_motion_cams('INVOKE_DEFAULT')
            bpy.ops.ed.undo_push()
            return {'FINISHED'}
        else:
            return self._execute_with_report(context)

    def _execute_with_report(self, context):
        ret = self.execute(context)
        if ret == {'FINISHED'}:
            self.report({'INFO'}, "已创建相机")
        return ret

    def _create_proxy_camera(self, context):
        from .viewport_proxy import acquire, get_proxy_obj, feature_enabled

        if not feature_enabled():
            self.report({'WARNING'}, "代理相机已关闭，请在相机助手偏好设置中启用“代理相机”")
            return {'CANCELLED'}

        area = context.area
        v3d = context.space_data
        rv3d = v3d.region_3d if v3d else None
        if rv3d is None or not rv3d.is_perspective:
            self.report({'WARNING'}, "代理相机创建失败：需在透视视口下创建（正交视口不支持接管）")
            return {'CANCELLED'}
        # 接管条件分级提示：渲染模式 + 合成器预览-总是
        if v3d.shading.type != 'RENDERED':
            self.report({'WARNING'}, "代理相机创建失败：需要处于渲染模式且开启合成器预览-总是")
            return {'CANCELLED'}
        if v3d.shading.use_compositor != 'ALWAYS':
            self.report({'WARNING'}, "代理相机创建失败：当前已处于渲染模式，还需开启合成器预览-总是")
            return {'CANCELLED'}
        # 已接管/未接管统一走 acquire（已接管时内部直接返回，代理仍存在，不重复创建）
        acquire(context.scene, area)
        if get_proxy_obj() is None:
            self.report({'ERROR'}, "代理相机创建失败")
            return {'CANCELLED'}
        self.report({'INFO'}, "已创建代理相机")
        return {'FINISHED'}

    def execute(self, context):
        return create_view_camera(context)


def create_view_camera(context, *, proxy=False):
    """共用相机对齐；仅代理调用保留接管状态，分辨率由代理模块管理。"""
    region_3d = context.space_data.region_3d
    v3d = context.space_data
    region = context.region
    if region is None or region.type != 'WINDOW':
        region = next((r for r in context.area.regions if r.type == 'WINDOW'), None)

    # 先捕获当前视角；退出代理接管会恢复原场景相机与分辨率。
    scn = context.scene
    view_inv = region_3d.view_matrix.inverted()
    view_dist = region_3d.view_distance
    perspective = region_3d.view_perspective != 'ORTHO' and region_3d.is_perspective
    src = v3d.camera or scn.camera
    source_camera_view = region_3d.view_perspective == 'CAMERA' and src and src.type == 'CAMERA'
    source_projection = None
    source_zoom = region_3d.view_camera_zoom
    source_offset = tuple(region_3d.view_camera_offset)
    if source_camera_view:
        data = src.evaluated_get(context.evaluated_depsgraph_get()).data
        source_projection = {p: getattr(data, p) for p in (
            'type', 'lens', 'sensor_width', 'sensor_height', 'sensor_fit',
            'shift_x', 'shift_y', 'ortho_scale', 'clip_start', 'clip_end')}
    lens = (src.data.lens if region_3d.view_perspective == 'CAMERA'
            and src and src.type == 'CAMERA' else v3d.lens / 2.0)
    if not proxy:
        from . import viewport_proxy
        if viewport_proxy.state['active']:
            viewport_proxy.release(scn)
        # 普通创建是用户明确选择：代理监控不得把新相机换回旧相机。
        viewport_proxy.state['yielded'] = True

    if perspective:
        bpy.ops.object.camera_add()
        cam = context.active_object
        cam.name = "Camera"
        cam.data.show_name = True
        context.scene.camera = cam

        # 进入相机视图前不动视图：直接写相机变换（等价 camera_to_view，
        # 但不强制 rv3d->persp=RV3D_CAMOB，避免"先跳后收"的抖动）
        cam.matrix_world = view_inv
        cam.data.sensor_width = 36
        cam.data.lens = lens
    else:
        cam_data = bpy.data.cameras.new('Camera')
        cam_data.type = 'ORTHO'
        cam_data.ortho_scale = 2.0 * view_dist * 36.0 / v3d.lens
        cam = bpy.data.objects.new('Camera', cam_data)
        context.collection.objects.link(cam)
        cam.data.show_name = True
        context.scene.camera = cam

        cam.location = view_inv.translation
        cam.rotation_euler = view_inv.to_euler()

    # 保留当前视角，按既定输出宽高比显示相机框。
    v3d.camera = cam
    region_3d.view_perspective = 'CAMERA'
    if source_projection is not None:
        # 已有相机视图（含代理）保留投影和视图平移/缩放，不重复贴框。
        for prop, value in source_projection.items():
            setattr(cam.data, prop, value)
        region_3d.view_camera_zoom = source_zoom
        region_3d.view_camera_offset = source_offset
    else:
        cam.data.clip_start = v3d.clip_start
        cam.data.clip_end = v3d.clip_end
        fac = fit_camera_frame(context.scene, region, region_3d)
        # CAMERA 的投影倍率为 1/fac，自由视图为 2；补偿后屏幕内容不缩放。
        if perspective:
            cam.data.lens = lens / fac
        else:
            cam.data.ortho_scale *= fac
            # 自由正交视口以观察平面为中心，近裁剪面为负；相机只支持正近裁剪。
            # 沿视线后移并平移裁剪区间，保持同一可见体积，避免把物体从中间切开。
            back = v3d.clip_end * 0.5 + v3d.clip_start
            cam.location += view_inv.col[2].xyz * back
            cam.data.clip_end = v3d.clip_end + v3d.clip_start

    context.region.tag_redraw()

    return {"FINISHED"}
