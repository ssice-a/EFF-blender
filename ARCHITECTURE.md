# Blender 侧架构

EIEM Blender 是三端管线的作者端：AnimeStudio 生成 EIEM 源包，Blender 编辑并生成 Mod，EIEM DLL 在游戏中加载 Mod。

## 模块

| 模块 | 责任 | 不应包含 |
|---|---|---|
| `eiem_format.py` | Mesh、材质、贴图、骨骼和 INI 的格式读写 | `bpy`、场景对象和 UI |
| `eiem_lod.py` | LOD 身份发现与导出计划 | 文件写入和 Blender UI |
| `eiem_blender_controls.py` | 款式切换、形态键控制和导出计划 | 二进制 Mesh 序列化 |
| `eiem_blender_addon.py` | 场景导入、资源导出、面板和操作符 | 新的独立格式实现 |
| `eiem_physics_*.py` | 可选物理数据、作者工具和预览 | 普通 Mesh 导出的必需依赖 |

## 约束

- 导出只处理用户选中的 EIEM 网格及其依赖，不隐式扩大到其他源资源。
- 同一源 Mesh 的多个部件共享形态键变量；合并 Mesh 保留形态键数据和顶点偏移。
- 骨骼路径和绑定矩阵来自资源身份，不依赖 Blender 显示名称。
- 导出窗口负责把包写入所选父目录的 `mod/` 子目录。
- 更新检查只读取 GitHub Release，不自动改写插件文件。

导出格式和资源解析集中在纯模块与后台测试中；需要场景状态的逻辑保留在主插件模块。
