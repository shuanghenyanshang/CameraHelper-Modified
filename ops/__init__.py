import bpy

from .add_camera import AddCamera
from .adjust_camera_lens import AdjustCameraLens
from .preview_camera import PreviewCamera
from .snapshot import Snapshot
from .switch_camera import SwitchCamera, SwitchCameraMenu
from .toggle_lock_camera import ToggleLockCamera
from . import motion, viewport_proxy
register_class, unregister_class = bpy.utils.register_classes_factory((
    AddCamera,
    PreviewCamera,
    AdjustCameraLens,

    Snapshot,
    SwitchCamera,
    SwitchCameraMenu,
    ToggleLockCamera,
))


def register():
    register_class()
    motion.register()
    viewport_proxy.register()

def unregister():
    unregister_class()
    motion.unregister()
    viewport_proxy.unregister()
