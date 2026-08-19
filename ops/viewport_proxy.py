"""Viewport Proxy Camera —— 集成进 CameraHelper（移植自独立扩展 viewport_proxy_camera）

目标：让「自由视口 = 相机 = 渲染」，使合成器雾/丁达尔等依赖 scene.camera 的
NPR 效果在自由视口下与相机视图一致，无需任何 C++ 改动。

原理（numpy 已验证，见 blender-npr-operations SKILL §11.0）：
  - 代理相机 matrix_world = viewmat⁻¹，clip 同步 v3d，渲染分辨率 = 视口宽高；
  - 焦距动态取 v3d.lens/2（自由透视 zoom=2.0 的等效焦距），保证渲染、相机视图
    与自由视口三者内容像素级一致（替代早期硬编码 26.23 经验值）；
  - 进入相机视图直接写矩阵 + 精确贴边 camzoom，不触发 camera_to_view/view_center_camera
    的中间态 → 创建无抖动、边框贴视窗边缘。

接管规则（铁律）：
  - 仅：渲染模式(RENDERED) + 合成器预览(use_compositor≠DISABLED) + 自由透视视口
  - 让位永远优先；任何不确定信号立即让位，绝不抢回
  - 只动 scene.camera 指针与渲染分辨率，永不修改用户相机/相机数据
  - 代理相机是可选/可切换的特殊视口相机，可从大纲或 CameraHelper 相机菜单手动切入
  - F12 弹窗确认：用代理相机渲染 或 恢复用户相机+输出分辨率渲染

锁定保护（2026-08-17 集成时新增，用户要求）：
  - 代理相机视图 + 合成器生效 + 渲染图窗口打开 → 强制关闭「锁定相机到视图」，
    并禁止手动启用（锁定按钮/ToggleLockCamera 阻止 + gizmo tooltip 警告），
    直至关闭渲染图窗口——避免代理相机锁定视图被误操作。
"""

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty
import os
import traceback

from .add_camera import fit_camera_frame

PROXY_OBJ_NAME = "__ProxyCamera"
PROXY_DATA_NAME = "__ProxyCamera"
PROXY_COLLECTION = "__ProxyCameras"
TAG = "viewport_proxy_camera"
VIEW_CAMERA_SENSOR_WIDTH = 36.0
# 焦距不再硬编码：自由透视视口 zoom=2.0 → 相机焦距 = v3d.lens/2（见 _view_lens）
VIEW_CAMERA_LENS_FALLBACK = 25.0

# 运行时状态
state = {
    'active': False,             # 接管中
    'yielded': False,            # 让位中（等待用户明确回到预览状态）
    'user_camera_name': None,    # 接管前用户相机名
    'user_res': None,            # 接管前 (x, y, percentage)
    'user_view': None,           # 接管前 rv3d.view_perspective
    'rendering': False,          # 渲染进行中（render_pre/post 保护）
    'last_shading': None,        # (type, use_compositor) 用于检测"用户明确回到预览"
    'render_freeze': False,      # 渲染结果弹窗打开期间冻结代理相机（lock_camera=False）
}

# addon keyconfig 中注册的 F12 keymap item（卸载时移除）
_f12_kmi = None


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def find_view3d():
    """返回第一个可见 VIEW_3D area（无则 None）。"""
    screen = bpy.context.screen
    if not screen:
        return None
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            return area
    return None


def _view_lens(v3d=None):
    """自由透视视口的等效焦距：相机焦距 = v3d.lens / 2。

    BKE_camera_params_from_view3d：自由透视 params.zoom = CAMERA_PARAM_ZOOM_INIT_PERSP (2.0)，
    相机渲染 zoom=1.0 → 相机焦距须 = v3d.lens/2 才能使渲染内容与自由视口一致
    （numpy 验证，见 blender-npr-operations SKILL §11.5；替代早期硬编码 26.23 经验值）。
    """
    if v3d is None:
        area = find_view3d()
        v3d = area.spaces[0] if area else None
    if v3d is not None and v3d.region_3d is not None and v3d.region_3d.is_perspective:
        return v3d.lens / 2.0
    return VIEW_CAMERA_LENS_FALLBACK


def render_result_window_open():
    """是否有 Render Result 图像正在被显示（包括独立窗口、主工作区 Image Editor）。

    覆盖：用户拆分出的 Image Editor area、Render Result 弹出的独立 Image Window、
    以及 workspace 里临时切到 Image Editor 的场景。检测所有空间（SpaceImage）
    只要空间.image.type == 'RENDER_RESULT' 即判定打开。
    """
    # 先检查全局：是否有 RENDER_RESULT 图像
    has_render_result = any(i.type == 'RENDER_RESULT' for i in bpy.data.images)
    if not has_render_result:
        return False
    # 扫描每个窗口/屏幕/area 的 Image Editor space
    for wm in bpy.data.window_managers:
        for win in wm.windows:
            if not win.screen:
                continue
            for area in win.screen.areas:
                for sp in area.spaces:
                    # 对任何 space，查是否有 image 属性（比如 Spreadsheet 无此项，跳过）
                    if not hasattr(sp, 'image'):
                        continue
                    img = getattr(sp, 'image', None)
                    if img is not None and img.type == 'RENDER_RESULT':
                        return True
    return False


def get_proxy_obj():
    """按名字查找代理相机对象（不创建）。"""
    obj = bpy.data.objects.get(PROXY_OBJ_NAME)
    if obj and obj.type == 'CAMERA':
        obj.hide_select = False
        return obj
    return None


def get_or_create_proxy(scn):
    """创建/获取代理相机对象（fallback 用；主动接管优先走视口相机创建路径）。"""
    proxy = get_proxy_obj()
    if proxy is None:
        cam_data = bpy.data.cameras.new(PROXY_DATA_NAME)
        cam_data.type = 'PERSP'
        proxy = bpy.data.objects.new(PROXY_OBJ_NAME, cam_data)
        proxy[TAG] = True
        proxy.hide_select = False     # 可从大纲/CameraHelper 菜单手动切入
        # 注意：不设 hide_viewport —— 视口 depsgraph 会排除隐藏对象，
        # 导致 Object Info 节点读不到代理相机，效果失效。
        col = bpy.data.collections.get(PROXY_COLLECTION)
        if col is None:
            col = bpy.data.collections.new(PROXY_COLLECTION)
            if scn.collection:
                scn.collection.children.link(col)
        if proxy.name not in col.objects:
            col.objects.link(proxy)
    return proxy


def create_proxy_from_view_camera(scn, area):
    """用 CameraHelper 已验证的「创建视口相机」操作生成代理相机。

    这样代理相机本质上就是一个特殊视口相机：创建/贴边/进入相机视图完全复用
    `camhp.add_camera`，之后只追加代理 TAG、集合归档和视口分辨率同步。
    """
    if scn is None or area is None:
        return None
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    space = area.spaces[0] if area.spaces else None
    rv3d = space.region_3d if space else None
    if region is None or space is None or rv3d is None:
        return None

    # 先把渲染分辨率同步为视口尺寸，再走创建视口相机路径：
    # 这样创建/贴边时即按最终分辨率计算，接管后不会因分辨率切换再跳一次
    if (scn.render.resolution_x != region.width or scn.render.resolution_y != region.height or
            scn.render.resolution_percentage != 100):
        scn.render.resolution_x = region.width
        scn.render.resolution_y = region.height
        scn.render.resolution_percentage = 100

    selected = [obj for obj in bpy.context.selected_objects]
    active = bpy.context.view_layer.objects.active
    old_proxy = get_proxy_obj()
    if old_proxy is not None:
        bpy.data.objects.remove(old_proxy, do_unlink=True)

    before_names = {obj.name for obj in bpy.data.objects}
    try:
        with bpy.context.temp_override(window=bpy.context.window, screen=bpy.context.screen,
                                       area=area, region=region, space_data=space,
                                       region_data=rv3d):
            bpy.ops.camhp.add_camera('EXEC_DEFAULT')
    except Exception:
        traceback.print_exc()
        return None

    proxy = scn.camera
    if proxy is None or proxy.type != 'CAMERA' or proxy.name in before_names:
        created = [obj for obj in bpy.data.objects
                   if obj.name not in before_names and obj.type == 'CAMERA']
        proxy = created[-1] if created else None
    if proxy is None:
        return None

    proxy.name = PROXY_OBJ_NAME
    proxy.data.name = PROXY_DATA_NAME
    proxy[TAG] = True
    proxy.hide_select = False
    proxy.data.show_name = False
    proxy.data.sensor_width = VIEW_CAMERA_SENSOR_WIDTH
    proxy.data.lens = _view_lens(space)
    _set_camera_custom_resolution(proxy.data, False)

    proxy_col = _get_or_create_proxy_collection(scn)
    if proxy.name not in proxy_col.objects:
        proxy_col.objects.link(proxy)
    for col in list(proxy.users_collection):
        if col is not proxy_col:
            try:
                col.objects.unlink(proxy)
            except Exception:
                pass

    scn.camera = proxy
    try:
        space.camera = proxy
    except Exception:
        pass

    try:
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        for obj in selected:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        if active and active.name in bpy.data.objects:
            bpy.context.view_layer.objects.active = active
    except Exception:
        pass

    return proxy


def _get_or_create_proxy_collection(scn):
    col = bpy.data.collections.get(PROXY_COLLECTION)
    if col is None:
        col = bpy.data.collections.new(PROXY_COLLECTION)
        if scn.collection:
            scn.collection.children.link(col)
    return col


def _camera_uses_custom_resolution(cam_data):
    props = getattr(cam_data, 'per_camera_resolution', None)
    return bool(props and getattr(props, 'use_custom_resolution', False))


def _set_camera_custom_resolution(cam_data, enabled):
    props = getattr(cam_data, 'per_camera_resolution', None)
    if props is None:
        return
    try:
        props.use_custom_resolution = enabled
    except Exception:
        traceback.print_exc()


def _solidify_proxy_camera(scn, area, v3d, rv3d):
    """用户启用代理相机的 per-camera-resolution 时，把当前视角固化为正式相机。

    旧 __ProxyCamera 本身变成用户相机，保留矩阵、焦距、clip 与手动启用的相机分辨率；
    另复制出新的干净 __ProxyCamera 留给后续自动接管使用。
    """
    proxy = get_proxy_obj()
    if proxy is None or scn.camera is not proxy:
        return False
    if not _camera_uses_custom_resolution(proxy.data):
        return False

    user_cam = proxy
    user_data = proxy.data
    proxy_col = bpy.data.collections.get(PROXY_COLLECTION)

    # 先复制出新的代理相机，再把旧代理改成正式相机。
    new_proxy_data = user_data.copy()
    new_proxy = user_cam.copy()
    new_proxy.data = new_proxy_data
    new_proxy[TAG] = True
    new_proxy.hide_select = False
    _set_camera_custom_resolution(new_proxy_data, False)

    if TAG in user_cam:
        del user_cam[TAG]
    user_cam.hide_select = False
    user_cam.name = "Camera"
    user_data.name = user_cam.name
    user_data.show_name = True
    user_data.sensor_width = VIEW_CAMERA_SENSOR_WIDTH
    user_data.lens = _view_lens(v3d)

    new_proxy.name = PROXY_OBJ_NAME
    new_proxy_data.name = PROXY_DATA_NAME
    proxy_col = _get_or_create_proxy_collection(scn)
    proxy_col.objects.link(new_proxy)

    if scn.collection and user_cam.name not in scn.collection.objects:
        scn.collection.objects.link(user_cam)
    if proxy_col and user_cam.name in proxy_col.objects:
        proxy_col.objects.unlink(user_cam)

    scn.camera = user_cam
    if v3d is not None:
        v3d.lock_camera = False
        try:
            v3d.camera = user_cam
        except Exception:
            pass
    if rv3d is not None:
        rv3d.view_perspective = 'CAMERA'
    if area is not None:
        area.tag_redraw()

    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    user_cam.select_set(True)
    bpy.context.view_layer.objects.active = user_cam

    state['active'] = False
    state['yielded'] = True
    state['user_camera_name'] = user_cam.name
    state['user_res'] = None
    state['user_view'] = 'CAMERA'
    state['render_freeze'] = False
    bpy.context.view_layer.update()
    return True


def is_proxy_camera_view():
    """当前是否处于「代理相机视图」（scene.camera 是代理 + CAMERA 视图 + 透视）。"""
    scn = bpy.context.scene
    if scn is None or scn.camera is not get_proxy_obj():
        return False
    area = find_view3d()
    if not area:
        return False
    v3d = area.spaces[0]
    rv3d = v3d.region_3d
    if rv3d is None or rv3d.view_perspective != 'CAMERA':
        return False
    return True


def is_compositor_non_direct():
    """合成器是否为「非直连」= 场景合成器节点树有 3 个及以上节点。

    用户定义（2026-08-17）：直连 = 默认只有 Render Layers + Composite 两个节点，
    且仅一条 图像→图像 连接；存在三个及以上节点即视为非直连（自定义合成器效果，
    如雾/丁达尔等依赖 scene.camera 的 NPR 效果）。
    Blender 5.x：场景合成器节点树为 scene.compositing_node_group（非旧版 scene.node_tree）。
    """
    scn = bpy.context.scene
    if scn is None:
        return False
    nt = getattr(scn, 'compositing_node_group', None)
    return nt is not None and len(nt.nodes) >= 3


def lock_blocked():
    """代理相机视图 + 合成器非直连 + 渲染图窗口打开 → 禁止「锁定相机到视图」。

    用户要求（2026-08-17）：代理相机的自动锁定视角+自适应分辨率"太无感"、
    易误操作，渲染图窗口打开时需强制解锁并禁止手动启用，降低误操作风险。
    不依赖 state['active']：用户手动切到代理相机视图（合成器 CAMERA 模式）也覆盖。
    """
    if not render_result_window_open():
        return False
    if not is_proxy_camera_view():
        return False
    return is_compositor_non_direct()


def _current_shading_key():
    area = find_view3d()
    if not area:
        return None
    shading = area.spaces[0].shading
    return (shading.type, shading.use_compositor)


def center_camera_view(area=None):
    """让当前相机视图边框精确贴合视窗边缘（无 view_center_camera 的 +4px 边距）。"""
    area = area or find_view3d()
    if area is None:
        return False
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    space = area.spaces[0] if area.spaces else None
    rv3d = space.region_3d if space else None
    if region is None or space is None or rv3d is None:
        return False
    fit_camera_frame(bpy.context.scene, region, rv3d)
    return True


def align_camera_to_view(area, camera_obj):
    """把相机对齐到当前视口（直接写矩阵，等价 camera_to_view 但不强制切换相机视图）。"""
    if area is None or camera_obj is None:
        return False
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    space = area.spaces[0] if area.spaces else None
    rv3d = space.region_3d if space else None
    if region is None or space is None or rv3d is None:
        return False

    try:
        space.camera = camera_obj
    except Exception:
        pass

    if not rv3d.is_perspective:
        return False
    camera_obj.matrix_world = rv3d.view_matrix.inverted()
    camera_obj.data.sensor_width = VIEW_CAMERA_SENSOR_WIDTH
    camera_obj.data.lens = _view_lens(space)
    return True


# ---------------------------------------------------------------------------
# 接管 / 让位 / 同步
# ---------------------------------------------------------------------------

def sync_values(scn, force=False, sync_matrix=True):
    """把代理相机参数同步到视口：matrix_world（可选）、统一焦距、clip、分辨率。

    仅在值实际变化（或 force）时写入。接管后矩阵由原生 lock_camera（C 层）驱动，
    sync_matrix=False 时只监控 lens/clip/分辨率（低频轮询用）。返回是否发生了写入。
    """
    area = find_view3d()
    if not area:
        return False
    v3d = area.spaces[0]
    rv3d = v3d.region_3d
    if rv3d is None:
        return False
    proxy = get_proxy_obj()
    if proxy is None:
        return False

    dirty = force

    if sync_matrix:
        # 矩阵：view 矩阵取逆 = 相机 world 矩阵（仅接管对齐时写一次）
        m = rv3d.view_matrix.inverted()
        if dirty or proxy.matrix_world != m:
            proxy.matrix_world = m
            dirty = True

    # 透视参数：代理/固化/普通视口相机统一 sensor/动态焦距；clip 与视口一致（Depth 反投影依赖）
    lens = _view_lens(v3d)
    if dirty or proxy.data.sensor_width != VIEW_CAMERA_SENSOR_WIDTH:
        proxy.data.sensor_width = VIEW_CAMERA_SENSOR_WIDTH
        dirty = True
    if dirty or proxy.data.lens != lens:
        proxy.data.lens = lens
        dirty = True
    if dirty or proxy.data.clip_start != v3d.clip_start:
        proxy.data.clip_start = v3d.clip_start
        dirty = True
    if dirty or proxy.data.clip_end != v3d.clip_end:
        proxy.data.clip_end = v3d.clip_end
        dirty = True

    # 分辨率：视口宽高（Camera Info 用 scene.r.xsch/ysch 算投影矩阵，须与视口 winmat 一致）
    region = None
    for r in area.regions:
        if r.type == 'WINDOW':
            region = r
            break
    if region:
        w, h = region.width, region.height
        if (dirty or scn.render.resolution_x != w or scn.render.resolution_y != h or
                scn.render.resolution_percentage != 100):
            scn.render.resolution_x = w
            scn.render.resolution_y = h
            scn.render.resolution_percentage = 100
            dirty = True
        if dirty:
            # 分辨率/焦距变化后重贴边：相机视图边框始终铺满视窗（视窗 resize 时也生效）
            fit_camera_frame(scn, region, rv3d)
    return dirty


def sync(scn, force=False):
    """完整同步：值（不重写矩阵，矩阵归 lock_camera）+ scene.camera + 立即 depsgraph 更新
    （渲染恢复时用）。"""
    dirty = sync_values(scn, force, sync_matrix=False)
    if dirty:
        proxy = get_proxy_obj()
        if proxy is not None and scn.camera is not proxy:
            scn.camera = proxy
        bpy.context.view_layer.update()


def acquire(scn, area=None):
    """接管：记录用户状态 → 创建特殊视口相机作为代理 → 原生 lock_camera。

    之后相机矩阵由 Blender 原生 lock_camera（C 层）在视口导航时直接驱动，
    零 Python 同步开销、零滞后（解决轮询/每帧 Python 同步卡顿）。
    area 参数：手动创建代理相机（Shift+左键）时传入按钮所在 area，
    避免多视口下 find_view3d 误选第一个视口。
    """
    if state['active']:
        return
    area = area or find_view3d()
    if not area:
        return
    v3d = area.spaces[0]
    rv3d = v3d.region_3d
    if rv3d is None:
        return
    state['user_camera_name'] = scn.camera.name if scn.camera else None
    state['user_res'] = (
        scn.render.resolution_x,
        scn.render.resolution_y,
        scn.render.resolution_percentage,
    )
    state['user_view'] = rv3d.view_perspective

    # 代理相机直接从 CameraHelper「创建视口相机」生成，再打 TAG 变成特殊代理相机。
    # 这条路径已由用户验证为无抖、贴边；代理层只接管分辨率/锁定/让位状态。
    proxy = create_proxy_from_view_camera(scn, area)
    created_from_view_camera = proxy is not None
    if proxy is None:
        proxy = get_or_create_proxy(scn)
        aligned = align_camera_to_view(area, proxy)
        sync_values(scn, force=True, sync_matrix=not aligned)
    else:
        sync_values(scn, force=True, sync_matrix=False)

    if scn.camera is not proxy:
        scn.camera = proxy
    try:
        v3d.camera = proxy
    except Exception:
        pass
    # 进入相机视图 + 锁定：视口视角 = 代理相机视角（无缝切换），漫游由 C 层驱动相机
    rv3d.view_perspective = 'CAMERA'
    if not created_from_view_camera:
        center_camera_view(area)
    # 🔒 渲染图窗口打开时禁止上锁（否则 tick 下一轮强制关锁，锁图标闪烁）
    if not lock_blocked():
        v3d.lock_camera = True
    state['active'] = True
    state['yielded'] = False
    bpy.context.view_layer.update()


def release(scn):
    """让位：恢复用户相机 + 输出分辨率 + 原视口视角。

    ⚠️ 铁律：无论以何种方式切出代理相机，一律把"锁定相机到视图"关掉
    （不恢复接管前的 user_lock_camera）——避免其他相机视图在不知不觉中被
    lock_camera 误操作。
    """
    if not state['active']:
        return
    area = find_view3d()
    if area:
        v3d = area.spaces[0]
        rv3d = v3d.region_3d
        v3d.lock_camera = False
        if rv3d is not None and state.get('user_view') is not None:
            rv3d.view_perspective = state['user_view']
    if state['user_camera_name']:
        cam = bpy.data.objects.get(state['user_camera_name'])
        if cam and cam.type == 'CAMERA':
            scn.camera = cam
    else:
        scn.camera = None
    if state['user_res']:
        (scn.render.resolution_x,
         scn.render.resolution_y,
         scn.render.resolution_percentage) = state['user_res']
    state['active'] = False
    state['user_camera_name'] = None
    state['user_res'] = None
    state['user_view'] = None
    state['render_freeze'] = False


def cleanup_proxy_objects():
    """删除代理相机对象/集合/相机数据（保存前、卸载时调用，避免残留进 .blend）。"""
    proxy = get_proxy_obj()
    if proxy is not None:
        bpy.data.objects.remove(proxy, do_unlink=True)
    col = bpy.data.collections.get(PROXY_COLLECTION)
    if col is not None:
        for o in list(col.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.collections.remove(col)
    cam_data = bpy.data.cameras.get(PROXY_DATA_NAME)
    if cam_data is not None:
        bpy.data.cameras.remove(cam_data, do_unlink=True)


def tick():
    """常驻状态机（0.1s）：接管/让位检测 + 低频参数监控 + 锁定保护。

    接管后相机矩阵由原生 lock_camera（C 层）驱动，不再做 Python 矩阵同步
    → 移动视角零卡顿。本函数只做：接管条件判断、让位、视口 resize/lens/clip 监控、
    渲染图窗口打开时的强制解锁（lock_blocked）。
    """
    try:
        scn = bpy.context.scene
        if scn is None:
            return 0.1
        area = find_view3d()
        v3d = area.spaces[0] if area else None
        rv3d = v3d.region_3d if v3d else None

        # 🔒 锁定保护：代理相机视图 + 合成器生效 + 渲染图窗口打开 → 强制关锁并保持。
        # 独立于 state['active']（用户手动切到代理相机视图、合成器 CAMERA 模式也覆盖）。
        if v3d is not None and rv3d is not None and lock_blocked():
            if v3d.lock_camera:
                v3d.lock_camera = False
                try:
                    # EXEC_DEFAULT 直接执行 execute()，确保 report 信息栏可见
                    bpy.ops.viewport_proxy.warn_lock_blocked(
                        'EXEC_DEFAULT',
                        message="当前为代理相机视图，且已启用合成效果，启用会导致渲染错误。请先关闭渲染结果窗口。")
                except Exception:
                    traceback.print_exc()
                bpy.context.view_layer.update()
            state['render_freeze'] = True
        elif state['render_freeze'] and v3d is not None and rv3d is not None:
            # 弹窗已关闭：仅接管中恢复锁定（非接管时用户锁是被保护机制关的，不强行上锁）
            if state['active']:
                v3d.lock_camera = True
            state['render_freeze'] = False
            bpy.context.view_layer.update()

        if state['rendering']:
            return 0.1

        enabled = getattr(scn, 'proxy_camera_enabled', False)
        preview_active = (
            enabled and v3d is not None and rv3d is not None and
            v3d.shading.type == 'RENDERED' and
            v3d.shading.use_compositor == 'ALWAYS')

        if not preview_active:
            # 渲染模式/合成器预览关闭 → 让位
            if state['active']:
                release(scn)
            # 检测"用户明确回到预览状态"：shading 设置发生过变化 → 解除让位
            key = _current_shading_key()
            if key != state['last_shading']:
                state['yielded'] = False
                state['last_shading'] = key
            return 0.1

        if not state['active']:
            proxy = get_proxy_obj()
            # 被动接管：无视 yielded——用户/其他插件已把 scene.camera=代理 + 在
            # CAMERA 视图，本身就是明确的接管信号（yielded 卡死曾导致这里永远
            # 不触发 → active 恒 False → F12 不弹确认、渲染弹窗不关锁）。
            # 渲染图窗口打开时跳过上锁（lock_blocked），避免与本函数开头保护冲突。
            if (proxy is not None and scn.camera is proxy and
                    rv3d.view_perspective == 'CAMERA' and rv3d.is_perspective):
                if state['user_res'] is None:
                    state['user_res'] = (
                        scn.render.resolution_x,
                        scn.render.resolution_y,
                        scn.render.resolution_percentage,
                    )
                if state['user_view'] is None:
                    state['user_view'] = 'CAMERA'
                state['active'] = True
                if not v3d.lock_camera and not lock_blocked():
                    v3d.lock_camera = True
                    bpy.context.view_layer.update()
                return 0.1
            # 主动接管（仍受 yielded 约束：用户明确切走后不抢回）
            if not state['yielded'] and rv3d.view_perspective != 'CAMERA' and rv3d.is_perspective:
                acquire(scn)
            return 0.1

        if state['active']:
            proxy = get_proxy_obj()
            if (proxy is None or scn.camera is not proxy or
                    rv3d.view_perspective != 'CAMERA'):
                release(scn)
                state['yielded'] = True
            elif _solidify_proxy_camera(scn, area, v3d, rv3d):
                return 0.1
            else:
                # 低频监控：视口 resize / lens / clip 变化时更新代理参数
                sync_values(scn, sync_matrix=False)
        return 0.1
    except Exception:
        traceback.print_exc()
    return 0.1


# ---------------------------------------------------------------------------
# 渲染钩子（F12 保护）
# ---------------------------------------------------------------------------

@persistent
def render_pre_cb(dummy):
    state['rendering'] = True


@persistent
def render_post_cb(dummy):
    state['rendering'] = False


@persistent
def save_pre_cb(dummy):
    """保存前清理代理相机，避免 __ProxyCamera 残留进 .blend。"""
    try:
        scn = bpy.context.scene
        if state['active']:
            release(scn)
        cleanup_proxy_objects()
    except Exception:
        traceback.print_exc()


@persistent
def depsgraph_post_cb(dummy):
    """安全网：接管中用户以任何方式切走 scene.camera（CameraHelper 切换、
    Ctrl+Numpad0 设活动相机等）→ 立即关闭 lock_camera，不等 0.1s 轮询，
    杜绝"新相机视图在不知不觉中被 lock_camera 误操作"的窗口。

    只清锁不调用 release()——完整让位（恢复用户相机/分辨率/视角）仍由
    tick 状态机负责；rendering 期间（F12 渲染临时切换用户相机）不动锁，
    渲染结束 render_post 会重新接管。
    """
    if not state['active'] or state['rendering']:
        return
    try:
        scn = bpy.context.scene
        proxy = get_proxy_obj()
        if scn is None or proxy is None or scn.camera is not proxy:
            area = find_view3d()
            if area:
                area.spaces[0].lock_camera = False
    except Exception:
        traceback.print_exc()


# ---------------------------------------------------------------------------
# F12 弹窗操作符
# ---------------------------------------------------------------------------

class VIEWPORT_PROXY_OT_render(bpy.types.Operator):
    """F12 渲染入口：接管中弹窗确认，否则直接渲染。"""
    bl_idname = "viewport_proxy.render"
    bl_label = "Render (Viewport Proxy Camera)"
    bl_description = "视口代理相机下渲染前确认"

    def invoke(self, context, event):
        scn = context.scene
        if not (state['active'] and scn.camera is get_proxy_obj()):
            bpy.ops.render.render('INVOKE_DEFAULT')
            return {'FINISHED'}
        # 弹窗确认：用 wm.call_menu（菜单机制，与右键菜单同类，规避 3D 视口 +
        # 合成器 ALWAYS 下 invoke_popup 模态不绘制的问题）
        bpy.ops.wm.call_menu(name=VIEWPORT_PROXY_MT_render_confirm.bl_idname)
        return {'FINISHED'}


class VIEWPORT_PROXY_MT_render_confirm(bpy.types.Menu):
    bl_idname = "VIEWPORT_PROXY_MT_render_confirm"
    bl_label = "渲染确认"

    def draw(self, context):
        layout = self.layout
        layout.label(text="当前渲染相机为视口代理相机，是否渲染？", icon='CAMERA_DATA')
        layout.separator()
        layout.operator("viewport_proxy.render_with_proxy", text="渲染")
        layout.operator("viewport_proxy.render_cancel", text="不渲染")


class VIEWPORT_PROXY_OT_render_with_proxy(bpy.types.Operator):
    bl_idname = "viewport_proxy.render_with_proxy"
    bl_label = "Render with Proxy Camera"

    def execute(self, context):
        # 立即强制关锁：F12 渲染开始时就关闭 lock_camera，
        # 避免渲染过程中弹窗打开时合成器回写 Render Result
        area = find_view3d()
        if area:
            v3d = area.spaces[0]
            if v3d.lock_camera:
                v3d.lock_camera = False
                bpy.context.view_layer.update()
        state['rendering'] = True
        bpy.ops.render.render('INVOKE_DEFAULT')
        return {'FINISHED'}


class VIEWPORT_PROXY_OT_render_cancel(bpy.types.Operator):
    """不渲染：关闭确认菜单，不触发任何渲染"""
    bl_idname = "viewport_proxy.render_cancel"
    bl_label = "Cancel Render"

    def execute(self, context):
        return {'CANCELLED'}


class VIEWPORT_PROXY_OT_warn_lock_blocked(bpy.types.Operator):
    """渲染结果弹窗未关闭时，锁定被保护机制阻止 → 信息栏警告"""
    bl_idname = "viewport_proxy.warn_lock_blocked"
    bl_label = "视口代理相机：已自动关闭锁定相机到视图"
    bl_options = {'REGISTER'}

    message: bpy.props.StringProperty(name="message", default="")

    def execute(self, context):
        # 防抖：3 秒内不重复（tick 每 0.1s 检查，避免用户还没关弹窗就连弹）
        import time
        now = time.time()
        last = state.get('last_warn_ts', 0)
        if now - last < 3.0:
            return {'CANCELLED'}
        state['last_warn_ts'] = now
        self.report({'WARNING'}, self.message)
        return {'FINISHED'}


class VIEWPORT_PROXY_OT_lock_blocked_hint(bpy.types.Operator):
    """锁定保护提示 operator：作为锁定按钮 gizmo 的 tooltip 载体。

    Gizmo 无 tooltip 属性（悬停提示来自绑定 operator 的 bl_description），
    故 blocked 时把锁定按钮 gizmo 切换到本 operator 以显示警告文案，
    点击也只警告不切换（真实切换由 ToggleLockCamera 的 lock_blocked 阻止兜底）。
    """
    bl_idname = "viewport_proxy.lock_blocked_hint"
    bl_label = "锁定已被保护机制阻止"
    bl_description = "【当前为代理相机视图，且已启用合成效果，启用会导致渲染错误】"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        self.report({'WARNING'},
                    "当前为代理相机视图，且已启用合成效果，启用会导致渲染错误。请先关闭渲染结果窗口。")
        return {'CANCELLED'}


# ---------------------------------------------------------------------------
# 开关 UI（着色方式弹窗，渲染模式旁）
# ---------------------------------------------------------------------------

def _shading_panel_draw(self, context):
    layout = self.layout
    scn = context.scene
    row = layout.row()
    row.prop(scn, 'proxy_camera_enabled', text="视口代理相机", icon='CAMERA_DATA')


def _on_proxy_camera_enabled(self, context):
    """开关 toggle 时：关 → 立即让位；开 → 检测接管条件并接管。"""
    try:
        scn = context.scene if context else bpy.context.scene
        if scn is None:
            return
        enabled = getattr(scn, 'proxy_camera_enabled', True)
        if not enabled:
            # 关闭 → 让位（清理一切接管状态）
            if state['active']:
                release(scn)
            state['yielded'] = True
        else:
            # 打开 → 清让位标志；tick 在 0.1s 内会按条件自动接管
            state['yielded'] = False
            area = find_view3d()
            if not area:
                return
            v3d = area.spaces[0]
            rv3d = v3d.region_3d
            if rv3d is None:
                return
            preview_active = (
                v3d.shading.type == 'RENDERED' and
                v3d.shading.use_compositor == 'ALWAYS')
            if preview_active and rv3d.is_perspective and not state['active']:
                acquire(scn)
    except Exception:
        traceback.print_exc()


# ---------------------------------------------------------------------------
# F12 键位接管
# ---------------------------------------------------------------------------

def _is_no_mod_f12(kmi):
    """无修饰键的 F12 按下（不含 any 匹配项）。"""
    return (kmi.type == 'F12' and kmi.value == 'PRESS' and not kmi.any and
            not kmi.shift and not kmi.ctrl and not kmi.alt and not kmi.oskey)


def _restore_default_bindings():
    """清理历史遗留（幂等）：移除 default/active keyconfig 上残留的无修饰
    F12→viewport_proxy.render（早期独立插件直接改 keyconfig 造成），
    若因此丢失默认的无修饰 F12→render.render 则恢复。"""
    wm = bpy.context.window_manager
    kcs = []
    if wm.keyconfigs.default is not None:
        kcs.append(wm.keyconfigs.default)
    if wm.keyconfigs.active is not None and \
            not any(kc is wm.keyconfigs.active for kc in kcs):
        kcs.append(wm.keyconfigs.active)
    for kc in kcs:
        km = kc.keymaps.get('Screen')
        if km is None:
            continue
        removed_proxy = False
        for kmi in list(km.keymap_items):
            if _is_no_mod_f12(kmi) and kmi.idname == 'viewport_proxy.render':
                km.keymap_items.remove(kmi)
                removed_proxy = True
        if removed_proxy:
            has_default = any(_is_no_mod_f12(kmi) and kmi.idname == 'render.render'
                              for kmi in km.keymap_items)
            if not has_default:
                km.keymap_items.new('render.render', 'F12', 'PRESS')


def _setup_f12():
    """用官方 addon keyconfig 注册 F12→viewport_proxy.render。

    历史教训（独立插件 v0.2.2→v0.2.3）：直接修改 default/active keyconfig 的
    keymap 在重启后行为不可靠；keyconfigs.addon 是插件专用 keymap 通道，
    其 items 会合并进 active keymap 且优先于默认绑定匹配，卸载时随 keyconfig
    自动清理，无需手动恢复默认 F12。
    """
    global _f12_kmi
    _restore_default_bindings()  # 清理历史遗留（幂等）
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc is None:
        return
    km = kc.keymaps.new(name='Screen', space_type='EMPTY', region_type='WINDOW')
    # 幂等：移除旧 item 再重加
    if _f12_kmi is not None:
        try:
            km.keymap_items.remove(_f12_kmi)
        except Exception:
            pass
        _f12_kmi = None
    for kmi in list(km.keymap_items):
        if kmi.type == 'F12' and kmi.idname == 'viewport_proxy.render':
            km.keymap_items.remove(kmi)
    _f12_kmi = km.keymap_items.new('viewport_proxy.render', 'F12', 'PRESS')


def _restore_f12():
    """移除 addon keyconfig 中注册的 F12 绑定（默认绑定无需改动）。"""
    global _f12_kmi
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc is not None:
        km = kc.keymaps.get('Screen')
        if km is not None:
            for kmi in list(km.keymap_items):
                if kmi.type == 'F12' and kmi.idname == 'viewport_proxy.render':
                    km.keymap_items.remove(kmi)
    _f12_kmi = None
    _restore_default_bindings()  # 兜底清理历史遗留（幂等）


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

_classes = (
    VIEWPORT_PROXY_OT_render,
    VIEWPORT_PROXY_OT_render_with_proxy,
    VIEWPORT_PROXY_OT_render_cancel,
    VIEWPORT_PROXY_MT_render_confirm,
    VIEWPORT_PROXY_OT_warn_lock_blocked,
    VIEWPORT_PROXY_OT_lock_blocked_hint,
)


def register():
    try:
        for cls in _classes:
            bpy.utils.register_class(cls)
        bpy.types.Scene.proxy_camera_enabled = BoolProperty(
            name="视口代理相机",
            description="自由视口 + 渲染模式 + 合成器预览时，用跟随视口的代理相机接管 scene.camera（视口=相机=渲染）。\n关：立即让位；开：条件满足时接管（对齐当前视口 + 动态分辨率）",
            default=True,
            update=_on_proxy_camera_enabled,
        )
        # 2026-08-19：代理相机可从大纲或 CameraHelper 相机菜单手动切入，
        # 不再把开关绘制到「视图着色方式」弹窗。
        try:
            bpy.types.VIEW3D_PT_shading_compositor.remove(_shading_panel_draw)
        except Exception:
            pass
        for _handlers, _cb in ((bpy.app.handlers.render_pre, render_pre_cb),
                               (bpy.app.handlers.render_post, render_post_cb),
                               (bpy.app.handlers.save_pre, save_pre_cb),
                               (bpy.app.handlers.depsgraph_update_post, depsgraph_post_cb)):
            if _cb not in _handlers:
                _handlers.append(_cb)
        _setup_f12()
        if bpy.app.timers.is_registered(tick) is False:
            bpy.app.timers.register(tick, persistent=True)
    except Exception:
        traceback.print_exc()


def unregister():
    try:
        # 卸载前先让位并清理代理对象，避免 scene.camera 卡在代理 / 残留进 .blend
        scn = bpy.context.scene
        if scn is not None:
            if state['active']:
                release(scn)
            cleanup_proxy_objects()
        if bpy.app.timers.is_registered(tick):
            bpy.app.timers.unregister(tick)
        _restore_f12()
        if render_pre_cb in bpy.app.handlers.render_pre:
            bpy.app.handlers.render_pre.remove(render_pre_cb)
        if render_post_cb in bpy.app.handlers.render_post:
            bpy.app.handlers.render_post.remove(render_post_cb)
        if save_pre_cb in bpy.app.handlers.save_pre:
            bpy.app.handlers.save_pre.remove(save_pre_cb)
        if depsgraph_post_cb in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(depsgraph_post_cb)
        try:
            bpy.types.VIEW3D_PT_shading_compositor.remove(_shading_panel_draw)
        except Exception:
            pass
        if hasattr(bpy.types.Scene, 'proxy_camera_enabled'):
            del bpy.types.Scene.proxy_camera_enabled
        for cls in reversed(_classes):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
    except Exception:
        traceback.print_exc()
