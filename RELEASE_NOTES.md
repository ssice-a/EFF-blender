# EFF Blender v0.43.0

配套：[EFF v1.6.0](https://github.com/ssice-a/EFF/releases/tag/v1.6.0)、[AnimeStudio v1.5.0](https://github.com/ssice-a/AnimeStudio/releases/tag/v1.5.0)。

- 新增统一的 N → EFF → 形态动画组管理面板，无需选中网格即可查看全部组、成员及快捷键。
- 一个播放组支持多个网格，每个成员独立选择 Action、KEY slot 和频道；组内共用范围、循环、倍率、快捷键与播放时间。预览停止后恢复各成员原动画状态。
- 同一网格、同一频道可以加入多个播放组。游戏内启动新组会停止共享频道的冲突组；不同频道的组可同时播放。
- 删除形态键后自动清理失效控制记录，包括复制网格遗留的记录，保留有效控制设置。删除已发布的形态绑定后仍需选择“全部资源”导出。
- 配套编译器支持原 PFB 与 Mesh 骨骼列表数量不对应时的精确骨架路径绑定，修复这类资源导致的整包导出失败。

Windows x64 / Blender 5.0，内置依赖使用 Python 3.11，已在 Blender 5.0.1 验证。保存工程并关闭 Blender 后安装 `EFF_Blender_v0.43.0.zip`；形态动画导出需同时更新 EFF v1.6.0。多网格组导出需选齐成员，NLA/驱动器仍需先烘焙。

真实 Blender 的组管理、成员/slot/频道、预览恢复和删除形态键回归通过，含原失败脸部的完整包编译与候选 53 个骨骼引用回读通过。本次不包含骨骼动画播放或完整的游戏更新自动 PFB 适配。游戏内新动画画面和连续 F10 最终验收仍待完成。

## English

The unified Shape Animation Group Manager supports multi-mesh groups, per-member Actions/KEY slots/channels, shared playback settings and synchronized previews with state restoration. A mesh or channel may belong to several groups; the paired runtime replaces conflicting playback while allowing disjoint groups to run together.

Deleted Shape Keys no longer leave stale export controls. The bundled compiler resolves mismatched native bone palettes through exact hierarchy bindings. Removing published shape bindings still requires a full export.

Windows x64 / Blender 5.0 with Python 3.11, tested on 5.0.1. Save your work and close Blender before installation; pair with EFF v1.6.0. Export all group members together and bake NLA/drivers first. Automated Blender and isolated real-resource validation passed; final in-game visual checks remain pending.
