# Camera Helper 修改版

为 Blender 快速创建视口相机、运动相机及相机切换的工具。在原 Camera Helper 基础上维护改进。

**[⬇ 下载可安装 ZIP](https://github.com/shuanghenyanshang/CameraHelper-Modified/releases/download/v1.4.2-motion-inherit-r2-20261114/CameraHelper-1.4.2-motion-inherit-R2.zip)** · [发布说明](https://github.com/shuanghenyanshang/CameraHelper-Modified/releases/tag/v1.4.2-motion-inherit-r2-20261114)

## 新增与改进

- **运动相机继承画面参数**：沿路径动态继承来源相机的焦距、镜头位移、景深等，默认开启；关闭后保留当前值，可手动调整。
- 匹配真实非等距及闭合路径；来源改参、关键帧和时间轴切换动态生效。离散类型在段中点切换。
- 已有运动路径自动兼容，保留原开关和偏移；不改写来源相机数据，不需要手工追加资源或接线。
- 兼容官方 Blender 的新旧修改器输入 API；修复开关置灰、注册时机及路径绘制 shader 名称。
- 同步此前的代理相机偏好开关（默认关闭）、创建前后视图对齐和普通相机保持输出分辨率等改进。

## 安装与使用

1. 下载上方 ZIP，在 Blender 偏好设置的扩展菜单中选择“从磁盘安装”，安装并启用 **Camera Helper**。
2. 按住 **Ctrl** 点击原创建相机按钮，按顺序选择来源相机，右键结束。
3. 选中 **Controller**，在 **MotionCamera → 调整 → 继承画面参数** 中启停继承。

普通点击创建当前视角相机；代理功能需先在插件偏好中启用，Shift 点击可创建代理相机。

## 兼容与验证

已实际验证官方 **Blender 5.0.0**、官方 **5.2.0 Alpha** 及 **NPR 5.2** 的创建、来源绑定、动态参数、保存重开与运动保持；官方 5.0 另有 Cycles 图像对照，当前工程的开关已获用户点击确认。官方完整 GUI 模态创建/GPU 绘制、悬停和任意窄栏未全部独立验收；其他宿主版本未作同等实测。

安装包保留已验证的产品文件；仓库 README 和 CHANGELOG 是公开项目说明，与包内技术文档有所不同，功能代码及资源一致。

## 来源与开源协议

本仓库是 **[AIGODLIKE/CameraHelper](https://github.com/AIGODLIKE/CameraHelper)** 的本地修改版。原作者：**ACGGIT、Atticus、小萌新**。

上游 [manifest 的实际许可声明](https://github.com/AIGODLIKE/CameraHelper/blob/35b2c3d9bff279f4bad9112a4481b30cddab6b48/blender_manifest.toml) 为 **GPL-3.0-or-later**，本仓库保持该许可及原作者声明，并补充标准 [LICENSE](LICENSE)。来源、文件身份与本地修改范围详见 [NOTICE](NOTICE.md)。
