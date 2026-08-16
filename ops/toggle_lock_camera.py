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
        # 🔒 锁定保护（2026-08-17 集成视口代理相机）：代理相机视图 + 合成器生效 +
        # 渲染结果窗口打开 → 禁止启用锁定，防止代理相机锁定视图被误操作
        from .viewport_proxy import lock_blocked
        if lock_blocked():
            self.report(
                {"WARNING"},
                "当前为代理相机视图，且已启用合成效果，启用会导致渲染错误。请先关闭渲染结果窗口。")
            return {"CANCELLED"}
        context.space_data.lock_camera = not context.space_data.lock_camera
        self.report({"INFO"}, "Lock Camera: ON" if context.space_data.lock_camera else "Lock Camera: OFF")
        return {"FINISHED"}
