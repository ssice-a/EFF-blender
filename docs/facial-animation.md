# 表情动画导入

## 解包资源位置

AnimeStudio 打开终末地 VFS，并启用 Logical Asset Paths 后，在逻辑目录中浏览：

```text
assets/beyond/dynamicassets/gamedata/skeletalmorph/
  skeletalmorphcfg/     # 各角色的控制器到骨骼映射；还引用原始 Unity Avatar
  skeletalmorphanim/
    emotion/           # 情绪基础姿态及其配套行为配置
    pose/              # 口型、局部姿态和微表情
    morphanimation/    # 带时间曲线的表情动画
```

例如 Liino 的映射文件：

```text
skeletalmorphcfg/girl/data_facemorph_avatar_liino.asset
```

1. 勾选需要导出的动画 `.asset` 文件。
2. 同时勾选一个角色的 `data_facemorph_avatar_*.asset`；也可只选中这个 Avatar 文件。不要同时勾选多个 Avatar。
3. 选择 **Export → Format specific → EFF → Checked facial animations as EFF library...**，保存 `.eanim`。
4. 解包器只读取所选动画及 Avatar 引用的骨架，不需要 PostModel。不能匹配控制器的资源会列入文件中的 `skipped`，不会猜测替代控制器。

表情不是单一完整动画列表。Avatar 负责骨骼映射；每个 Emotion 还可配置 `_blinkAnim`、`_speyeAnim`、`_microExpressionPoses`、口型类型和随机眨眼间隔。此入口导出其确定的骨骼基础姿态，配套动态片段需要另外勾选。

## Blender

1. 先通过 EFF 模型导入得到来源骨架。
2. 选中骨架或仅绑定该骨架的网格，使用 **文件 → 导入 → EFF 动画（选中骨架）**。
3. 可按 `Emotion`、`Pose`、`Anim` 类型及名称包含文字筛选。
4. 在动作编辑器选择 `EFF_<avatar>_<kind>_<name>`。静态姿态在第 1 帧查看；动态片段按空格播放。

默认不切换当前 Action；勾选“预览首个动作”才会切换。全部动作启用 Fake User；请自行保存工程。重复导入相同数据及骨架绑定时复用现有动作。来源内容变化时创建新 Action，保留已有作者动作。

骨骼匹配使用完整来源路径、父子关系和参考姿态。骨架重命名不影响仍保留来源路径的骨骼。修改过参考姿态的骨架需要明确的重定向处理，当前导入器会报错，不静默套用。

面部动画采用局部 TRS；模型的身体参考姿态使用目标 EFF 骨架的来源值，因此 Unity Avatar 的身体默认姿态与 PostModel 存在微小差异时不会让脸整体偏移。动画所需、模型导入时省略的辅助 Transform 会补入骨架。

## 验证与范围

Blender 5.0.1 验证 Liino：119 个 Action（83 Emotion、31 Pose、5 Anim），重复导入复用全部动作，补入 `jawJoint` 和 `TongueMd04Joint`；眨眼中点的世界矩阵误差小于 0.000001。修改参考姿态时在创建动作前拒绝。

这不是身体 AnimationClip/ACL 的通用导出入口，也不导出 Shader 表情参数、游戏随机行为调度或游戏动画替换 Mod。普通 AnimationClip 的现有 FBX 导出入口保留。
