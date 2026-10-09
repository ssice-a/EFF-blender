# EFF Blender v0.40.3

配套：[EFF v1.3.4](https://github.com/ssice-a/EFF/releases/tag/v1.3.4)、[AnimeStudio v1.3.1](https://github.com/ssice-a/AnimeStudio/releases/tag/v1.3.1)。简易教程：[中文](https://github.com/ssice-a/EFF/blob/v1.3.4/Mod%20Tutorial.md) · [English](https://github.com/ssice-a/EFF/blob/v1.3.4/Mod%20Tutorial_EN.md)。

- 优化所选 Mesh 导出的离线编译：缓存有序 Renderer 关系、复用资源图，合并来源解析，并行处理独立来源并保持输出顺序确定。
- 相同固定输入、无编译缓存的对照：ZFY 编译 21.40 → 9.60 秒；Typhoea 15.33 → 8.35 秒；LZY 44.11 → 16.92 秒。各次编译产物逐字节一致，体积未增大。这些是当前机器的样本，未清空系统文件缓存。
- 当前选中 7 个 Mesh 的实际暂存导出 25.01 → 12.64 秒，包含快照、编译和发布；输入与输出校验一致。
- 同一游戏材质或贴图可用于多个独立作者变体，同时保留原生资源引用；支持局部导出。
- 重新打开工程时重置快照缓存，持续跟踪 Mesh 修改，保留骨骼、权重与引用校验。

安装 `EFF_Blender_v0.40.3.zip`；Windows x64 / Blender 5.0.1（Python 3.11）。保存工程并关闭 Blender 后更新插件。插件自带独立作者核心、编译器、来源工具及 Pillow。Mod 仍携带 `compiled.bin`，不保存完整游戏原包。

## English

- Faster offline compilation through ordered Renderer traversal, shared graph reuse, combined source parsing and deterministic parallel source processing.
- With fixed inputs and no compiled cache, measured compilation times fell from 21.40 to 9.60 seconds (ZFY), 15.33 to 8.35 seconds (Typhoea), and 44.11 to 16.92 seconds (LZY). Every compiled artifact was byte-identical. These are samples from the current machine; the OS file cache was not cleared.
- A real staged export of seven selected Meshes fell from 25.01 to 12.64 seconds, including snapshotting, compilation and publication.
- Independent material/texture variants can share original game templates while retaining native references. Snapshot tracking remains active across project loads.

Save your project and close Blender before updating. The archive includes only the add-on's own author tools and dependencies. Mod output size and the resource protocol are unchanged by the performance optimization.
