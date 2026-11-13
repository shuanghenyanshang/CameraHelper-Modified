import bpy
from . import inherit
from .add import CAMHP_PT_add_motion_cams

register_class, unregister_class = bpy.utils.register_classes_factory(
    (
        CAMHP_PT_add_motion_cams,
    )
)


def register():
    register_class()
    inherit.register()


def unregister():
    inherit.unregister()
    unregister_class()
