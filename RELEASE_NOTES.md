# EFF Blender v0.42.0

配套：[EFF v1.5.0](https://github.com/ssice-a/EFF/releases/tag/v1.5.0)、[AnimeStudio v1.5.0](https://github.com/ssice-a/AnimeStudio/releases/tag/v1.5.0)。

- 新增 N → EFF → 形态动画：给 Shape Keys value 插入关键帧，选择 Action 与 KEY slot、频道和帧范围，配置循环、自动播放与游戏快捷键。
- 动画随“全部资源”或“仅 Mesh”导出；仅材质/贴图更新保留动画。预览停止后恢复原 Action、帧、范围和权重。
- 游戏中快捷键播放/停止，Ins 支持暂停和实时调速；玩家倍率保存、调速不跳进度。
- 新增“恢复来源绑定”入口：副本 Mesh 数据块丢失绑定时，可按仍保留的精确 Renderer 来源恢复，错误提示列出未绑定物体。
- 优化离线编译的重复分析与装配编码；重新导出继承已提交的额外 PFB 命中，并保留发布事务校验。

Windows x64 / Blender 5.0，依赖使用 Python 3.11，已在 Blender 5.0.1 验证。关闭 Blender 后安装 `EFF_Blender_v0.42.0.zip`。形态动画需配套 EFF v1.5.0；同一频道只能属于一个启用组，NLA/驱动器需先烘焙。本功能不包含骨骼动画或自定义骨架运行控制。

作者导出、播放器、偏好和真实资源隔离编译已回归，游戏内动画显示及活跃动画成本仍待实测。离线提速没有减少目标 PFB 覆盖，也不代表所有 Mod 有相同收益；未来游戏更新的自动运行时 PFB 适配仍在开发。

## English

The separate Shape Animation panel exports selected Shape Keys Action/KEY-slot channels, frame ranges, loops, autoplay and shortcuts. Full/Mesh exports update clips; material and texture updates retain them. Preview restores author state. The paired runtime provides play/stop shortcuts, pause and persistent live speed controls.

Exact source-binding recovery and clearer unbound-Mesh errors help copied parts. Compiler improvements reduce repeated analysis and assembly encoding while retaining committed additional Prefab matches and transactional publication.

Windows x64 / Blender 5.0 with Python 3.11, tested on 5.0.1. Close Blender before installation. Each channel belongs to one enabled group; bake NLA/drivers first. Bone animation and custom-rig runtime control are not included. Automated and isolated real-resource checks passed; new in-game animation rendering and performance remain unverified.
