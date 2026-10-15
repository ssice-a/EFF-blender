# EFF Blender v0.44.0

配套：[EFF v1.7.0](https://github.com/ssice-a/EFF/releases/tag/v1.7.0)、[AnimeStudio v1.6.0](https://github.com/ssice-a/AnimeStudio/releases/tag/v1.6.0)。

- 接受 AnimeStudio 在原解包目录更新的来源清单、原生模板与当前索引。旧 Blender 工程无需重新导入人物；作者修改的几何、UV、权重、材质参数、形态键和动画继续用于导出。
- 导出期间与解包器共用来源锁，发布前核对来源是否变化；避免混用新清单和旧快照，来源更新中会明确提示重试。
- 优先使用重新解包记录的当前索引，避免回到旧父目录索引；原生模板缓存按来源内容失效。
- 配套编译器支持本次更新的 BLC v5 目录，并依据当前完整索引更新变化/退休来源、覆盖当前命中的 PFB。编译器静态链接 C/C++ 运行实现。

使用：保存工程并关闭 Blender 后安装 `EFF_Blender_v0.44.0.zip`。游戏更新后，用 AnimeStudio 重新打开当前游戏与原工作区、更新原解包资源，再打开原工程正常导出；首次更新建议选择“全部资源”。此作者流程不需要 EFF 修复工具。

Windows x64 / Blender 5.0 / Python 3.11，已在 Blender 5.0.1 验证。真实庄方宜旧工程 body 网格使用新来源隔离导出成功，原工程文件摘要未变；来源变化、缓存、离线调用与发布回归通过。不将此样本解释为所有骨架、Shader 或游戏画面已验收。

## English

Existing Blender projects can export against sources refreshed in place by AnimeStudio. Export locks and manifest validation keep source snapshots consistent, and the refreshed source package selects its current asset index. Author geometry, UVs, weights, materials, Shape Keys and animation remain in the original project.

The paired compiler supports BLC v5 and current-index source relocation, retirement and Prefab consumer discovery. Save and close Blender before installing. Refresh sources in AnimeStudio, then export from the existing project; the separate Mod repair tool is optional. Windows x64 / Blender 5.0 / Python 3.11, tested on 5.0.1.
