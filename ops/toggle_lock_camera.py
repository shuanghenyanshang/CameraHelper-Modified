import bpy


class ToggleLockCamera(bpy.types.Operator):
    bl_idname = "camhp.toggle_lock_camera"
    bl_label = "Toggle Lock Camera"
    bl_description = '锁定相机到视图'
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.space_data)

    def execute(self, context):
        context.space_data.lock_camera = not context.space_data.lock_camera
        self.report({"INFO"}, "Lock Camera: ON" if context.space_data.lock_camera else "Lock Camera: OFF")
        return {"FINISHED"}
