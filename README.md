# EIEM Blender 插件

EIEM Blender 用于把 AnimeStudio 导出的资源制作成游戏 Mod。你可以在 Blender 中编辑网格、材质、贴图、款式切换和形态键，然后导出可放入 EIEM `plugin/mods` 的 Mod 文件夹。

## 功能

- 导入和编辑 EIEM 网格、材质、贴图、骨骼、LOD 与物理资源。
- 支持多个部件、材质槽、合并网格和形态键。
- 为形态键提供 Blender 滑块，并可按需录制游戏快捷键。
- 创建款式切换组，设置默认款式和游戏快捷键。
- 导出完整 Mod 或仅网格包；导出按键切换选项默认开启。
- 在 EIEM 面板中检查 GitHub Release 更新并忽略指定版本。

## 安装

从 [Releases](https://github.com/ssice-a/EIEM-blender/releases) 下载 `EIEM_Blender_v*.zip`，在 Blender 的插件设置中选择“从磁盘安装”，然后启用插件。更新前先移除旧版并保存当前 `.blend` 工程。

## 制作 Mod

1. 在 AnimeStudio 中导出 EIEM 源包。
2. 在 Blender 选择 **文件 → 导入 → EIEM Mod 包**，打开源包中的 `mod.ini`。
3. 编辑模型、材质和贴图。需要款式切换时，在 **3D 视图 → N → EIEM → 网格切换**中创建切换组并设置默认状态。
4. 需要形态键时，在网格数据属性的 **EIEM 形态键控制**中点击“接管当前形态键”。这会把已有的 Blender 形态键登记为导出滑块；它不会修改顶点，也不会自动创建游戏快捷键。快捷键需要在同一面板中单独录制。
5. 选择要导出的 EIEM 网格，使用 **文件 → 导出 → EIEM Mod 包**。导出窗口中的“导出按键切换”默认勾选；取消后只导出默认款式，不导出款式和形态键快捷键，但仍保留形态键及其滑块。
6. 选择一个输出父目录。插件会自动创建 `mod/`，并把 `mod.ini`、`meshes/`、`materials/`、`textures/`、`skeletons/` 和 `physics/` 放在其中。将这个 `mod` 文件夹复制到游戏的 `plugin/mods/`。
7. 进入游戏后按 F10 刷新 Mod。

导出只处理当前选择的资源。请保留 AnimeStudio 源包和 `.blend` 工程，避免覆盖源资源。

## 更新

在 Blender 的 **3D 视图 → N → EIEM → EIEM 更新**面板点击“检查更新”。发现新版本后可以打开 Release 或忽略该版本；插件不会自动替换文件。

## TODO

- 继续完善多角色制作流程和物理效果作者工具。

## 鸣谢与免责声明

感谢 [AnimeStudio](https://github.com/Escartem/AnimeStudio) 及其贡献者。第三方依赖的许可见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES)。插件不包含游戏资源；游戏素材版权归鹰角网络所有。使用前请阅读 [EIEM 用户协议与免责声明](https://github.com/ssice-a/EIEM#用户协议与免责声明)，并自行承担使用风险。
