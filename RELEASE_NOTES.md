# EFF Blender v0.46.0

配套：[EFF v1.8.1](https://github.com/ssice-a/EFF/releases/tag/v1.8.1)、[AnimeStudio v1.7.0](https://github.com/ssice-a/AnimeStudio/releases/tag/v1.7.0)。

- 材质按表面透明、PBR／法线、Ramp、清漆、边缘光、自发光、角色 VFX、视差和描边分组；功能开关联动 Shader 关键字，不再手填字符串。
- 所有已绑定贴图集中顶部；开启功能时自动显示其已声明空槽，关闭功能保留设置并隐藏参数。保留完整原始参数入口与未知 Shader 原始编辑。
- 透明模式下拉框同步裁剪／预乘混合、实际队列和标签，保留作者当前深度写入、双面及贴图选择；呼吸联动自发光与角色 VFX。
- 新增仓库更新：main 推送通过自动测试后生成完整更新包，插件按提交 SHA 检查，无需作者每次手动发布 Release 或升版本号。仍可切换为仅检查正式 Release。
- 用户点击“下载并准备更新”后先校验大小、SHA256、包内文件及来源身份；保存工程并关闭全部 Blender 后自动安装，保留完整旧插件备份。不会自动关闭／保存工程、改游戏 DLL 或 Mod；开发用源码链接不自动覆盖。
- Python／UI 更新复用已验证的 v0.45.0 原生基础包，完整包含编译器、索引刷新器、作者核心和依赖。未改变离线包协议或 DLL；原生组件变化时需维护者另行构建配套基础包。

旧版用户需先安装本次完整 ZIP，才能使用新更新器。Windows x64 / Blender 5.0 / Python 3.11，已在 Blender 5.0.1 验证。角色效果组合仍受游戏 Shader 变体限制，编辑器设置与实际游戏视觉验收不同。

## English

Adds grouped material controls with paired Shader keywords, centralized texture paths and automatic declared empty slots. Adds opt-in installation from automatically tested main-branch packages, identified by source commit rather than version bumps. Downloads and validates a complete package, waits until all Blender processes close, then replaces the add-on with a full rollback backup. Stable Release checks remain available. Existing users must install this archive once to obtain the updater. Native tools are reused from an explicitly pinned, verified full base package; no game DLL or Mod changes are included.
