# EFF Blender v0.40.0

配套：[EFF v1.3.0](https://github.com/ssice-a/EFF/releases/tag/v1.3.0)、[AnimeStudio v1.3.0](https://github.com/ssice-a/AnimeStudio/releases/tag/v1.3.0)。

- 完整与分资源导出统一通过原生作者核心和配套编译器，复用 Mesh 快照、静态来源描述及编译缓存。
- 每个 Mod 自动携带 source-inputs.bin，导出按完整 mods 集合准备替换规则；兼容已声明的编译器参数布局。
- 同一来源材质可作为独立作者副本，分别绑定原版或自定义贴图。
- 保留款式切换、默认状态、按键声明、形态键及滑块；全 LOD 映射保持准确骨骼槽来源。
- README 更新为 F10 热重载、Ins 选择按键目标、多角色流程及短贴图名称说明。
- ZIP 包含作者核心 DLL、v35 编译器、静态来源工具及 Pillow，无需从开发目录另行复制。

保存工程并关闭 Blender，替换旧插件后安装 EFF_Blender_v0.40.0.zip，再启动 Blender。Windows x64 / Blender 5.0.1（Python 3.11）已验证；其他 Python 布局需匹配的原生依赖。首次导出选择全部资源，后续可分资源更新；等待准备完成后在游戏按 F10。不会自动为低级 LOD 减面，Physics 尚未接通完整游戏模拟链。
