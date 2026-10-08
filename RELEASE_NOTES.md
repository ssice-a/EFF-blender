# EFF Blender v0.40.2

配套：[EFF v1.3.3](https://github.com/ssice-a/EFF/releases/tag/v1.3.3)、[AnimeStudio v1.3.1](https://github.com/ssice-a/AnimeStudio/releases/tag/v1.3.1)。[简易制作教程](https://github.com/ssice-a/EFF/blob/v1.3.3/Mod%20Tutorial.md)。

- 单次导出复用共享骨架的路径、供体及绑定信息，避免每个网格反复扫描全部骨骼；保留原骨骼顺序、权重、bindpose 和新增骨骼校验。
- 13 个网格的快照处理对照从约 6.02 秒降至 2.46 秒，减少约 59%；该数值仅为快照阶段，完整导出还包含资源编码、编译和发布。
- 保留外部角色材质来源，支持同一游戏材质的多个独立作者副本及共享资源引用。
- 支持多 Mod 共用按键，并在 mod.ini 写出 key_switch_enabled；修正形态键面板中文显示，导出提示实际耗时。
- 插件内完整配套作者核心、编译器、来源工具及 Pillow，使用自己的安装目录。独立编译当前 Mod，生成 compiled.bin，不复制完整游戏原包。

安装 EFF_Blender_v0.40.2.zip，目标 Windows x64 / Blender 5.0.1（Python 3.11）。更新前保存工程并关闭 Blender，再替换插件。首次导出选择全部资源，后续可仅更新 Mesh、材质与贴图或贴图；Mod 需要随 compiled.bin 一起分发。

28 项导出核心、发布与 LOD 单元测试通过；权重、新增骨骼、空根、切线及接缝快照检查通过。在相同几何输入下，新旧路径的真实临时导出和编译输出共 31 个文件一致。全 LOD 输出不自动减面，Physics 尚未接通完整游戏模拟链。
