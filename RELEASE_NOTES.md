# EFF Blender v0.41.0

配套：[EFF v1.4.0](https://github.com/ssice-a/EFF/releases/tag/v1.4.0)、[AnimeStudio v1.4.0](https://github.com/ssice-a/AnimeStudio/releases/tag/v1.4.0)。[中文教程](https://github.com/ssice-a/EFF/blob/v1.4.0/Mod%20Tutorial.md) · [English tutorial](https://github.com/ssice-a/EFF/blob/v1.4.0/Mod%20Tutorial_EN.md)。

- 材质参数按功能分组，提供开关、数字、RGBA 和渲染队列控件，名称左对齐；关闭功能收起细节，已有贴图保留显示，空槽按需添加。
- 仅修改材质参数时增量更新原生输入，不转换未修改贴图，不重新导出 Mesh；暂存验证后发布，失败保留旧输出。
- 离线生成输入视图、依赖、装配和控制索引，减少 DLL 加载准备。插件只调用自己安装目录的核心、编译器和依赖。
- 统一物理编辑器支持曲线、批量参数、预设、新链删除和独立碰撞引用；新增 BoneCloth 链及球/胶囊碰撞体接入编译。
- 导入 AnimeStudio `.eanim` 独立骨骼表情库，支持筛选、重复动作复用和参考姿势验证，不自动保存工程。

安装 `EFF_Blender_v0.41.0.zip`。当前发行包为 Windows x64 / Blender 5.0，内置 Pillow 使用 Python 3.11 ABI，已在 Blender 5.0.1 验证。更新插件需关闭 Blender 释放原生 DLL。**旧 Mod 请重新导出以生成新的 compiled.bin。**

验证：100 项 Python 单元回归、Blender 注册/卸载、材质 UI、按键、法线/切线/蒙皮、来源物理导入及动画导入通过。隔离的 Liino 材质参数导出约 1.6–2.1 秒，0 Mesh、0 贴图转换，63 项原生输入与完整编译一致；不是所有导出的速度承诺。新增物理完整游戏模拟与退出/F10 尚待实机验收。

## English

A grouped material editor provides typed controls, left-aligned names, optional empty texture slots and conditional detail visibility. Material-only edits reuse unchanged Meshes and textures and incrementally update native inputs before transactional publication. Offline compilation now emits runtime input views and indices. All compiler, core and dependency files belong to the add-on itself.

The unified physics editor supports curves, batch parameters, presets and deleting new chains without copying collision references. New BoneCloth chains and sphere/capsule colliders compile offline. Independent `.eanim` facial libraries can be imported without saving the project.

Windows x64 / Blender 5.0 with Python 3.11; tested on Blender 5.0.1. Close Blender before updating and re-export old Mods for the new compiled structure. Automated regressions passed; a measured isolated material edit took 1.6–2.1 seconds with no Mesh or texture conversion. Complete in-game physics lifecycle acceptance remains outstanding.
