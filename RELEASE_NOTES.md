# EFF Blender v0.50.3

配套 EFF v2.0.6、AnimeStudio v1.9.1；Windows x64 / Blender 5.2。

- 按坐标约定往返 Mesh UV：游戏原生坐标保持原值，Blender 作者坐标仅在写回时转换一次。
- DDS 未修改时逐字节保留；PNG 压缩显式声明并应用 Unity 纹理行序。
- Format 3 包缺少 Skeleton 区段时，仍从 Mesh 骨骼路径恢复顶点组权重，并在导出时保留骨骼完整路径。
- 增加 Blender 5.2 的来源网格 UV 往返和 Format 3 权重导入测试。

## EFF Blender v0.50.2

配套 EFF v2.0.5、AnimeStudio v1.9.1；Windows x64 / Blender 5.2。

- 正确识别 `packages/` 与 `assets/` 下的游戏资源身份，支持 package 来源贴图和 Mesh 目标的 Format 3 导入、保留与导出。
- 将游戏资源身份与本地图片路径区分，避免把 package 贴图误判为需要重建的图片文件。
- 增加非法路径校验与 package 资源路径回归测试。

## EFF Blender v0.50.1

配套 EFF v2.0.4、AnimeStudio v1.9.1；Windows x64 / Blender 5.2。

- 导入 AnimeStudio `EFFSOURCE` 解包包中的网格、材质和贴图，同时保留 Format 3 Mod 导入。
- 保持 AnimeStudio 来源贴图的行方向；修改后的 DDS 从 Blender 当前像素重新编码，未修改 DDS 原样保留。
- 明确区分 Format 2、Format 3 和 `EFFSOURCE` 输入，不把解包包误判为 Format 2 Mod。
- 补充包类型、贴图回写和 Blender 5.2 插件注册回归测试。

## EFF Blender v0.50.0

配套 EFF v2.0.0、AnimeStudio v1.9.0；Windows x64 / Blender 5.2。

- 统一导出 Format 3 自描述目录，运行时不再需要旧编译缓存。
- 使用公共资源目录和经 SHA256 校验的共享作者工具；插件 ZIP 仅包含 Python 和格式模块。
- Mesh、材质、贴图、物理、特效和按键控制保持同一导出事务。
- 修复特效挂载的 Part 标识。
- 自动压缩导出后实际使用的骨骼调色板，连同 bindpose、hash、来源与索引一并重映射；不改动作者原权重。
- Mesh 属性面板新增“清理空顶点组”，对所选可编辑网格执行，可撤销；仅移除没有任何正权重的组，保留微小正权重并隔离共享网格数据。

预览为离线近似，游戏内材质、物理和特效仍需实机验证。旧工程可继续编辑，导出结果统一为 Format 3。
