# EFF Blender 插件

EFF Blender 用于把 AnimeStudio 导出的资源制作成游戏 Mod。你可以在 Blender 中编辑网格、材质、贴图、款式切换和形态键，然后导出可放入 EFF `plugin/mods` 的 Mod 文件夹。

## 功能

- 导入和编辑 EFF 网格、材质、贴图、骨骼、LOD 与物理资源。
- 支持多个部件、材质槽和形态键。
- 为形态键提供 Blender 滑块，并可按需录制游戏快捷键。
- 创建款式切换组，设置默认款式和游戏快捷键。
- 导出 format 2 完整 Mod，或更新已有 Mod 的 Mesh、材质和贴图；导出按键切换选项默认开启。
- 导出复用当前 Blender 会话内的 Mesh 快照及离线编译缓存。
- 在 EFF 面板中检查 GitHub Release 更新并忽略指定版本。

## 安装

从 [Releases](https://github.com/ssice-a/EFF-blender/releases) 下载 `EFF_Blender_v*.zip`，在 Blender 的插件设置中选择“从磁盘安装”，然后启用插件。更新前先移除旧版并保存当前 `.blend` 工程。

## 制作 Mod

1. 在 AnimeStudio 中导出 EFF 源包。
2. 在 Blender 选择 **文件 → 导入 → EFF Mod 包**，打开源包中的 `mod.ini`。
3. 编辑模型、材质和贴图。需要款式切换时，在 **3D 视图 → N → EFF → 网格切换**中创建切换组并设置默认状态。
4. 需要形态键时，在网格数据属性的 **EFF 形态键控制**中点击“接管当前形态键”。这会把已有的 Blender 形态键登记为导出滑块；它不会修改顶点，也不会自动创建游戏快捷键。快捷键需要在同一面板中单独录制。
5. 选择要导出的 EFF 网格，在 EFF 面板或 **文件 → 导出** 中选择“导出所选 Mod”。首次选择“全部资源”；后续可以选择“仅 Mesh”“仅材质与贴图”或“仅贴图”更新已有完整 Mod。完整导出窗口中的“导出按键切换”默认勾选；取消后只导出默认款式，不导出款式和形态键快捷键，但仍保留形态键及其滑块。
6. 在 EFF 导出区域填写 Mod 文件夹名，然后在文件浏览器中选择父目录；插件创建或刷新同名文件夹，把 `mod.ini`、`meshes/`、`materials/` 和 `textures/` 写入其中，再准备离线候选。需要直接在游戏中使用时，父目录选择游戏的 `plugin/mods/`。文件浏览器里的文件名不决定 Mod 名称。
7. 进入游戏后按小键盘减号（`NUMPADMINUS`）刷新 Mod。该按键写入游戏全局设置，不再占用 F10。

完整导出要生成游戏可加载的离线候选时，请设置 `EFF_RELOAD_GAME` 环境变量指向当前游戏目录；如果输出目录本身位于 `plugin/mods/<Mod>` 下，插件会从目录结构推断游戏目录。插件不保存开发者本机的游戏路径，也不会把固定的 Mod 名称写入代码。

导出只处理当前选择的资源。请保留 AnimeStudio 源包和 `.blend` 工程，避免覆盖源资源。

Physics 参数和预览仍可在作者工具中编辑；新增 Physics 的游戏原生导出尚未接通。多个作者包可以在 Blender 中共存，同名材质和贴图会保持来源归属；当前 DLL 的热重载事务仍只支持一个 Mod，不能据此认为多个 Mod 可以同时热重载。

## 更新

在 Blender 的 **3D 视图 → N → EFF → EFF 更新**面板点击“检查更新”。发现新版本后可以打开 Release 或忽略该版本；插件不会自动替换文件。

## TODO

- 继续完善多角色制作流程和物理效果作者工具。

## 鸣谢与免责声明

感谢 [AnimeStudio](https://github.com/Escartem/AnimeStudio) 及其贡献者。第三方依赖的许可见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES)。插件不包含游戏资源；游戏素材版权归鹰角网络所有。使用前请阅读 [EFF 用户协议与免责声明](https://github.com/ssice-a/EFF#用户协议与免责声明)，并自行承担使用风险。
