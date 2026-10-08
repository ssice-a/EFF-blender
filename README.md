# EFF Blender 插件

EFF Blender 用于把 AnimeStudio 导出的资源制作成游戏 Mod。你可以在 Blender 中编辑网格、材质、贴图、款式切换和形态键，然后导出可放入 EFF `plugin/mods` 的 Mod 文件夹。

本次发布 **v0.40.2**，配套 [EFF v1.3.3](https://github.com/ssice-a/EFF/releases/tag/v1.3.3) 与 [AnimeStudio v1.3.1](https://github.com/ssice-a/AnimeStudio/releases/tag/v1.3.1)。Windows x64 发布包包含原生导出核心、资源编译器、来源工具及 Pillow；已在 Blender 5.0.1 / Python 3.11 验证。其他 Python 布局需要对应的依赖构建。

带截图的[简易 Mod 制作教程](https://github.com/ssice-a/EFF/blob/main/Mod%20Tutorial.md)包含安装、解包、材质编辑和所选网格导出步骤。

## 功能

- 导入和编辑 EFF 网格、材质、贴图、骨骼、LOD 与物理资源。
- 支持多个部件、材质槽和形态键。
- 为形态键提供 Blender 滑块，并可按需录制游戏快捷键。
- 创建款式切换组，设置默认款式和游戏快捷键。
- 导出 format 2 完整 Mod，或更新已有 Mod 的 Mesh、材质和贴图；导出按键切换选项默认开启。
- 分资源导出复用当前会话 Mesh 快照；同一次导出复用共享骨架的路径、供体及绑定信息；仅贴图导出保留已有完整 Mod 的其他资源。
- 自动生成每个 Mod 的 `source-inputs.bin`，只编译当前导出的独立 Mod，生成 `compiled.bin`。
- 同一游戏材质可作为多个独立作者材质的模板，各自保留参数和贴图绑定。
- 在 EFF 面板中检查 GitHub Release 更新并忽略指定版本。

## 安装

从 [Releases](https://github.com/ssice-a/EFF-blender/releases) 下载 `EFF_Blender_v0.40.2.zip`，在 Blender 的插件设置中选择“从磁盘安装”，然后启用插件。更新前保存工程、关闭 Blender，再替换旧插件并重新启动，以释放原生 DLL 和旧 Python 模块。

## 制作 Mod

1. 在 AnimeStudio 中导出 EFF 源包。
2. 在 Blender 选择 **文件 → 导入 → EFF Mod 包**，打开源包中的 `mod.ini`。
3. 编辑模型、材质和贴图。需要款式切换时，在 **3D 视图 → N → EFF → 网格切换**中创建切换组并设置默认状态。
4. 需要形态键时，在网格数据属性的 **EFF 形态键控制**中点击“接管当前形态键”。这会把已有的 Blender 形态键登记为导出滑块；它不会修改顶点，也不会自动创建游戏快捷键。快捷键需要在同一面板中单独录制。
5. 选择要导出的 EFF 网格，在 EFF 面板或 **文件 → 导出** 中选择“导出所选 Mod”。首次选择“全部资源”；后续可以选择“仅 Mesh”“仅材质与贴图”或“仅贴图”更新已有完整 Mod。完整导出窗口中的“导出按键切换”默认勾选；取消后只导出默认款式，不导出款式和形态键快捷键，但仍保留形态键及其滑块。
6. 在 EFF 导出区域填写 Mod 文件夹名，然后在文件浏览器中选择父目录；插件创建或刷新同名文件夹，把 `mod.ini`、`meshes/`、`materials/` 和 `textures/` 写入其中，再在暂存目录中完成独立编译和校验后发布。需要直接在游戏中使用时，父目录选择游戏的 `plugin/mods/`。文件浏览器里的文件名不决定 Mod 名称。
7. 等待导出和离线准备完成，进入游戏后按 **F10** 热重载。按 **Ins** 独立勾选哪些 Mod 接收按键，默认全部启用，允许多个 Mod 同时响应。也可在各 `mod.ini` 的 `[Mod]` 中设置 `key_switch_enabled=0` 或 `1`。实际全局快捷键以游戏 `plugin/eff.ini` 为准。

导出需要游戏安装路径，请设置 `EFF_RELOAD_GAME` 环境变量指向当前游戏目录；如果输出目录本身位于 `plugin/mods/<Mod>` 下，插件会从目录结构推断游戏目录。插件不保存开发者本机的游戏路径，也不会把固定的 Mod 名称写入代码。

插件只调用自己安装目录内的原生作者核心、编译器和约 3 MiB 的 `eff_source_prepare.exe`，不从游戏或开发目录借用文件。Mod 包含独立作者资源、来源描述和压缩 `compiled.bin`；不复制完整游戏原包。实测 LZY 输出从 131.17 MiB 增至约 142.42 MiB。

导出只处理当前选择的资源。请保留 AnimeStudio 源包和 `.blend` 工程，避免覆盖源资源。

多个作者包可以在 Blender 中共存，同名材质和贴图保持精确来源归属。每个导出 Mod 的 INI 是作者规则入口；F10 统一加载整个 mods 目录，包括多角色的增删与资源修改。全 LOD 导出会把当前作者几何映射到各目标 LOD，不会自动减面。

保留源包的 `source/manifest.json` 和 `source/serialized/` 供精确来源和字段模板读取；普通 Blender 导出不读取 `source/streams/` 的原始载荷快照。新解包贴图使用短名称，旧包的哈希名称按自己的 INI/manifest 继续使用，不能只改文件名而不更新引用。游戏 Mod 无需携带原包快照或全局索引。

Physics 参数和预览仍可在作者工具中编辑，新增 Physics 尚未接通完整游戏模拟链。当前测试不代表所有角色或二三十个 Mod 的性能已经验证。

## 更新

在 Blender 的 **3D 视图 → N → EFF → EFF 更新**面板点击“检查更新”。发现新版本后可以打开 Release 或忽略该版本；插件不会自动替换文件。

## TODO

- 继续完善多角色制作流程和物理效果作者工具。

## 鸣谢与免责声明

感谢 [AnimeStudio](https://github.com/Escartem/AnimeStudio) 及其贡献者。第三方依赖的许可见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES)。插件不包含游戏资源；游戏素材版权归鹰角网络所有。使用前请阅读 [EFF 用户协议与免责声明](https://github.com/ssice-a/EFF#用户协议与免责声明)，并自行承担使用风险。
