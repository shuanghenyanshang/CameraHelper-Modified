import bpy

from ..utils import get_operator_bl_idname


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

        if region_3d.is_perspective:
            bpy.ops.object.camera_add()
            cam = context.active_object
            cam.name = "Camera"
            cam.data.show_name = True
            context.scene.camera = cam

            bpy.ops.view3d.camera_to_view()
            cam.data.lens = 26.2
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

        region_3d.view_perspective = 'CAMERA'
        try:
            bpy.ops.view3d.view_center_camera()
        except:
            pass

        context.region.tag_redraw()

        return {"FINISHED"}
