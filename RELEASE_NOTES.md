# EFF Blender v0.40.1

配套：[EFF v1.3.1](https://github.com/ssice-a/EFF/releases/tag/v1.3.1)、[AnimeStudio v1.3.1](https://github.com/ssice-a/AnimeStudio/releases/tag/v1.3.1)。

- 在暂存目录编译当前一个 Mod，完整校验后发布作者资源和 compiled.bin；完整与分资源导出保持同一流程。
- 安装包自带作者核心、编译器、小型来源准备工具和 Pillow，全部从插件自己的目录读取。
- 源 Mesh 离线匹配全部 PFB/Renderer；不打包完整原游戏包，也不编译游戏中其他 Mod。
- 保留按键切换、默认款式、形态控制、材质模板和静态 Mesh 制作。

保存工程并关闭 Blender，再安装 EFF_Blender_v0.40.1.zip。目标 Windows x64 / Blender 5.0.1（Python 3.11）；其他 Python 布局需匹配依赖。首次导出选全部资源，导出到游戏 plugin/mods 或设置 EFF_RELOAD_GAME。实测 LZY 编译规则增加约 11.25 MiB。游戏 DLL 需配套更新，Mod 随 compiled.bin 一起分发。全 LOD 输出不会自动减面，Physics 尚未接通完整模拟链。
