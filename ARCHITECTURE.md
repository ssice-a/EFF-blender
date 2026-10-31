# Blender 作者端架构

## 格式边界

- Mod Manager 独占旧 Mod 到 Format 3 的迁移。迁移完成后，Mod 目录只保留 Format 3。
- 游戏 DLL 只解析和加载 Format 3；不探测、转换或兼容旧 Mod。
- Blender 只导入和导出 Format 3。旧 Mod 必须先由 Mod Manager 迁移。
- Format 3 的 `mod.ini` 是唯一资源关系与文件路径清单；资源文件和 DDS 路径可由作者直接编辑，不依赖额外索引。

## 作者工作流

- Blender 从 `EFFSOURCE` 基线读取被选 Mesh、Material 和 Texture 所需的原生模板；不扫描游戏 PFB，也不编译消费者清单。
- 完整导出在目标目录外生成候选文件，校验全部 Format 3 关系和资源，再事务式发布。失败时保留上一个完整 Mod。
- DDS 是可直接替换的纹理文件。已有 DDS 原样复制；PNG 按原 Texture2D 的线性/sRGB 语义压缩为 DDS。运行时尺寸、格式和 mip 信息以 DDS 头为准。
- Blender 回读使用 Format 3 的 Mesh、Material、Physics、Effect 作者数据及对应资源声明；材质导入只接受 Material `.effres`，并按 `mod.ini` 绑定 DDS。
- Mesh 快照缓存仅存在于当前 Blender 会话。导出计时分别记录作者数据准备、Format 3 写入校验和发布。

## 写入边界

- `author_format3.write_format3_directory` 直接生成最终 `mod.ini`、Material、Physics、Effect、Controls 和 DDS 文件，不创建 Format 2 资源对象或中间包。
- `eff_author_assets.dll` 只接收版本化的 Mesh、Texture2D、原生模板描述和作者快照记录；它直接写 Format 3 `.effres` 文件，并校验写入结果与已生成的 `mod.ini` 一致。
- 一个 Mesh Part 可以被多个 Replace 目标共享，但这些目标必须使用相同的 Unity Mesh TypeTree；类型不同就明确报错，不能生成含糊的共享资源。
- Unity PathID 按有符号 64 位值传入，文件和目录身份仍由 `mod.ini` 的逻辑路径与名称决定。
- 完整候选目录完成 Format 3 校验后才事务式发布。冷导出计时区分 Blender 作者数据准备、Format 3 写入校验和发布；Blender 5.2 实际项目的冷导出仍需实测。

`eiem_offline_reload.py`、消费者 PFB 清单生成、Format 2 内存包写入和旧的局部资源编译路径不属于 Blender 导出链路。特效与物理随完整 Format 3 导出统一调度。
