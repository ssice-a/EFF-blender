# Blender 作者端架构

三端通过一条离线管线工作：AnimeStudio 保存游戏来源快照和可编辑资源，Blender 编辑作者数据并发布 format 2 Mod，原生编译器生成经过校验的候选代，DLL 在游戏原有资源生命周期内加载候选。旧 format 1 运行时 Mesh/材质替换导出器已经移除。

## 模块边界

| 模块 | 责任 |
|---|---|
| `eiem_format.py` | AnimeStudio 可编辑源包的 Mesh、材质、Skeleton 读取与作者侧往返 |
| `eiem_blender_controls.py` | 款式切换、Shape Key 控制、稳定身份及所选资源计划 |
| `eiem_lod.py` | 源资源 LOD 身份与导出计划 |
| `eiem_blender_addon.py` | Blender 导入、作者数据快照、UI、操作符和统一导出入口 |
| `eiem_native_export.py` | format 2 Mod 的资源装配、分材质部件和原子发布 |
| `eiem_offline_reload.py` | 编译器字段结构校验、目标目录解析、静态来源准备及完整集合编译调用 |
| `eiem_physics_*.py` | 可选 Physics 作者文件、参数编辑和预览 |

`tools/nativepack` 是原生资源 Python 格式与编码的唯一源码。源码 checkout 优先导入这个目录；发布包的 `nativepack/` 是打包时从它生成的运行副本，不另行维护实现。`bundle_blender_native_export.py` 包含完整的模块依赖（包括 `hashing.py`），并使用与作者导出相同的编译器协议检查。

## 协议与导出

- 普通 UI 和脚本都调用 `export_package`，再转到 format 2 原生导出器；没有选择旧运行时替换格式的入口。
- 导出只处理选中的 EFF Mesh 及其材质、贴图和作者控制。一次导出使用一个明确的来源基线；多来源导出需要提供能够唯一解析全部来源的合并基线。
- 同一作者网格按材质槽拆成原生部件，各 LOD 可以复用网格数据；原生编译器负责 Renderer、骨骼、形态和依赖展开。
- 新增 Physics 原生包导出仍是未接通能力。Physics 编辑和作者文件导出保留在独立工具中，不伪装为游戏已支持。
- `EFF_OFFLINE_PACKAGE_PROTOCOL` 声明实际字段结构、清单编码与集合能力；标签不作为兼容门槛，协议查询必须成功。
- Mod 自动带有 `source-inputs.bin`。首次或来源身份变化时，静态工具复用已有 AnimeStudio 索引确定相关包与范围；来源未变时直接复用描述。DLL 不查询运行时清单，也不扫描场景或整个游戏取得替换目标。
- Mod 文件夹名由导出 UI 提供。游戏目录通过 `EFF_RELOAD_GAME` 或
  `plugin/mods/<Mod>` 目录关系确定；发布包和离线缓存不保存开发者本机的
  游戏路径。`.blend` 内的作者来源字段仍是本地工程路径，移动工程后可在
  导出窗口重新指定来源基线。
- Material 导入按来源包和 section 归属隔离；同名不同包不会覆写既有 Blender 材质。Texture 的导入 section 和 Material 的导出 ID 会消解跨包名称冲突。
- 游戏从单一 `plugin/resource-reload-cache/current.tsv` 读取完整作者集合候选。Blender 调用共享编译器的 `--mods` 合并各 Mod 静态来源并一次构建共享 CAB/PFB；F10 才提交增删改代际。

## 缓存

Mesh 快照缓存只在当前 Blender 会话中存在，键包含对象、网格、骨架、Shape Key 的修订与内容指纹。UI 修改先刷新依赖图；导出内部的三角化与切线计算更新在缓存修订统计关闭时消费，避免下一次未修改导出误失效。

一次导出的资源解码缓存在验证阶段复用已经读取的内容。候选的文件内容由共享 SHA-256 实现验证；原生编译器在作者目录外缓存几何、外观、命名替换和已发布代。缓存键包含输入内容、绑定和编译协议，未命中的资源继续走完整校验流程。

部分导出会更新已有完整 Mod 的 Mesh、材质或贴图。文件写入完成后才发布 INI，原生候选完整校验后才更新当前代指针。失败保留之前的完整候选。
