# EFF Blender v0.49.2

配套：[EFF v1.10.2](https://github.com/ssice-a/EFF/releases/tag/v1.10.2)、[AnimeStudio v1.8.1](https://github.com/ssice-a/AnimeStudio/releases/tag/v1.8.1)。

- 物理碰撞体作为 Rig 下独立对象创建和管理，不再创建后自动关联当前物理组；需要碰撞时再从物理组显式引用。
- 导入源物理时递归查找解包目录中的组件证据和 `.physics` 作者包。
- 保留 Chen 软飘带物理预设，通过紧凑下拉框应用；预设不包含角色专属碰撞体。
- 与 EFF v1.10.2 的命名按键切换、资源地址增量替换和静态多部件 Mesh 装配校验配套；原生基础包由 DLL Release 工作流构建并固定大小与 SHA256。

- 移动后的 Blender 工程会按当前游戏安装查找 AnimeStudio 登记的完整索引，不用把全量索引复制到每个项目。
- 来源清单里的旧绝对索引路径失效时，自动尝试共享索引，并保留来源清单和摘要不变。
- 显式设置的 `EFF_SOURCE_INDEX` 路径失效时给出明确错误，不会悄悄改用其他索引。

- 新增对象级 `.efffx` 编辑：根 Empty 管理挂载和导出；发射器按来源层级组织，所有参数以内联类型控件编辑，并可添加、复制和删除发射器。
- 提供 Emission、Shape、Initial、Velocity、Force、Size、Rotation、Color、Texture Sheet、Renderer 等模块块；保留未知源字段，模块可停用后再恢复。
- 速度、大小、旋转等曲线使用 Blender 原生 Graph Editor 一起编辑；颜色渐变使用 Blender 原生 Color Ramp。
- 支持 Billboard 序列帧、来源 Mesh 或自选 Mesh、材质和贴图引用；离线预览发射形状、粒子数量、运动、生命周期、渐隐、相机朝向和序列帧。
- 特效可挂载角色骨骼或作为独立 Mod 导出；统一导出支持仅选特效、多个特效和角色加特效，并可让特效覆盖所有 LOD。
- 创建款式切换组时直接录制快捷键；分资源导出仍复用当前 Mesh 快照，减少不必要的重复处理。
- 使用由配套 EFF v1.10.1 构建并固定大小/SHA256 的原生基础包，包含 effectAttachment、standaloneEffect、作者核心和来源索引工具。
- 修复完整安装包漏带 compiled block 读取模块的问题，并在发布契约测试中验证该模块。

Windows x64 / Blender 5.0 / Python 3.11，已在 Blender 5.0.1 验证。预览是离线近似，不覆盖游戏的全部 Shader、碰撞和渲染行为；游戏画面仍需实机验收。更新前保存工程并关闭 Blender；包内工具只供作者离线导出，不安装到游戏目录。

## English

Fixes the complete add-on package missing its compiled-block reader, with release contract coverage for the shipped module. Adds independent Rig-owned collision authoring, recursive source-physics discovery, and a compact reusable physics-preset selector. The paired EFF v1.10.1 release fixes static multi-part Mesh provenance validation and retains named switch groups with resource-only incremental updates. The SHA256-pinned native base is built by the EFF release workflow. Existing effect authoring and offline preview remain supported; Blender preview remains an approximation of game rendering and collision.
