# EFF Blender v0.45.0

配套：[EFF v1.8.0](https://github.com/ssice-a/EFF/releases/tag/v1.8.0)、[AnimeStudio v1.7.0](https://github.com/ssice-a/AnimeStudio/releases/tag/v1.7.0)。

- 游戏小更新导致索引过期时，自动调用插件自带的离线索引工具：复用未变 Bundle，只解析变化/新增内容，再继续本次导出。相同基线后续不重复刷新，无需运行 AnimeStudio 或重新导入工程。
- 工具自带运行环境，完整索引仍在作者工作区，不随插件或 Mod 分发；游戏/工作区位置来自用户设置与解包清单，没有固定盘符。搬动工作区后，清单中旧绝对索引路径仍可能需要重新定位。
- 优化 Mesh 快照失效粒度、历史缓存占用、形态/面槽位读取、材质字段和磁盘图片解码；实际几何、权重、静态骨架、形态、材质槽和图片内容改变仍会更新。
- 修复游戏自带形态帧与频道身份不一致导致导出拒绝；导出窗口可选择并记住游戏目录，允许导出到任意制作目录。
- 明确区分来源被其他导出或解包更新占用。与解包器保持完整性锁，失败保留上一 Mod。

关闭 Blender 后安装完整 ZIP，再打开原工程。Windows x64 / Blender 5.0，已在 5.0.1 验证。固定十网格全部导出，单顶点编辑约 11.32 → 10.23 秒、不变重导约 5.76 → 5.08 秒，作者及编译产物逐文件一致；机器负载不同会影响耗时。几何变化仍完整离线编译。新增索引工具约 43 MiB，不包含 831 MB 工作区索引。

## English

Automatically refreshes a stale workspace index with the bundled offline updater and retries the same staged export once. Unchanged Bundle records are reused; new/changed content is parsed and retired content removed. Includes more precise Mesh snapshot invalidation, bounded history, material/bitmap reuse, native shape-frame identity fixes, and a persistent game-folder picker. Save and close Blender before installing the complete archive. No external AnimeStudio installation or .NET runtime is required for the updater.
