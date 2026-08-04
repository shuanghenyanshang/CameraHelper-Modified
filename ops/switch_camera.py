import bpy


class SwitchCamera(bpy.types.Operator):
    bl_idname = 'camhp.switch_camera'
    bl_label = 'Switch Camera'

    @classmethod
    def poll(cls, context):
        camera = getattr(context, "camera", None)
        return camera and camera.type == "CAMERA"

    def invoke(self, context, event):
        if camera := getattr(context, "camera", None):
            context.space_data.camera = camera
            context.space_data.region_3d.view_perspective = 'CAMERA'
            context.scene.camera = camera
            try:
                bpy.ops.view3d.view_center_camera()
            except Exception:
                import traceback
                traceback.print_exc()
        return {"FINISHED"}


class SwitchCameraMenu(bpy.types.Operator):
    bl_idname = 'camhp.switch_camera_menu'
    bl_label = 'Switch Camera Menu'
    bl_description = '切换相机'

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        from ..ui.menu import CAMERA_HELPER_MT_Switch_Camera
        bpy.ops.wm.call_menu(name=CAMERA_HELPER_MT_Switch_Camera.__name__)
        return {"FINISHED"}
