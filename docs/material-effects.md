# 材质透明、发光和动态发光

以下字段已在 Typhoea 的 CharacterNPR 材质中核对。其他 Shader 的同名字段不一定有相同作用，不应把这套设置照搬到全部游戏材质。

Shader 定义支持的功能和计算方式；材质资源保存参数值、贴图引用和关键字。实际渲染队列也是材质数据。无需修改 Shader 代码，可在 Blender 编辑这些材质数据并导出。

## 在 Blender 中修改

1. 选中 Mesh，在右侧“材质属性”选择要修改的材质槽。
2. 打开“EFF 材质”。已有贴图始终显示，可用文件夹按钮换图；空槽通过“添加空贴图槽”选择。仅能添加该来源材质已经声明的槽，不能凭空给 Shader 增加采样功能。清空作者路径表示恢复该槽的源材质设置。贴图缩放和偏移在“贴图变换”中。
3. 参数按表面、透明、自发光、描边、法线、特效和渲染状态分组，可以搜索。点击分组名称展开或收起，参数名称左对齐。布尔值使用勾选框，数值使用数字控件，颜色使用 RGBA 控件。发光或描边关闭后收起详细参数，已有的来源贴图仍然显示。若尚无对应 Shader 开关，在材质的“自定义属性”添加**字符串**属性，例如名称 `eiem_keyword._EMISSION`、值 `1`。`1` 启用，`0` 禁用，随后会出现在对应分组的勾选框中；插件不会猜测并添加 Shader 关键字。
4. 渲染队列在 EFF 材质参数中显示为“渲染队列”，对应 `eiem_render_queue`。`_characterRenderQueue` 是 Shader 属性，不能代替实际队列。
5. 修改已有材质可用“仅材质与贴图”导出；未变的贴图会复用已验证的 TEX，不重新生成 mip。只改参数、渲染队列、关键字或 UV 变换且绑定未变时，只重建对应原生输入。改贴图路径、透明开关或发光开关时，也应导出材质。新增材质、改 Mesh 材质槽使用“全部资源”。导出完成后按 F10。

Blender 的 Principled BSDF 发光、透明或关键帧不会自动变成游戏 Shader 设置。效果需要在游戏中验证；插件不会自动保存工程。

## Alpha 裁剪：适合 lian 链条镂空

以当前 cloth05 模板为例，采用游戏 cloth03 的裁剪设置：

| 字段 | 设置 |
| --- | --- |
| `_EnableAlphaTest` | `1` |
| `_AlphaClipThreshold` | `0.5` |
| `_ALPHATEST_ON（功能开关）` | `1` |
| `_ALPHABLEND_ON（功能开关）` | `0` |
| `_SurfaceType` | `0` |
| `_SrcBlend` / `_DstBlend` | `1` / `0` |
| `_ZWrite` / `_ZTest` | `1` / `3`，沿用该模板 |
| 实际渲染队列 | `2001`，参考原生 cloth03 |
| RenderType 标签 | `Opaque`，参考原生 cloth03 |

`_BaseMap` 必须使用带 Alpha 的图片。A 小于阈值的区域被丢弃，其余区域正常绘制；它不会产生半透明渐变。提高阈值会丢弃更多区域。这个 Shader 的原生裁剪材质仍将 `_AlphaClip` 设为 `0`，实际启用字段是 `_EnableAlphaTest` 和 `_ALPHATEST_ON`，不要仅凭类似的字段名称判断。

本次 lian 已在当前工程中启用这组裁剪设置，尚未保存工程或覆盖游戏 Mod。

## Alpha 混合：适合半透明薄纱

游戏 cloth07 与 cloth05 使用同一个 Shader。参考 cloth07 的组合：

| 字段 | 设置 |
| --- | --- |
| `_SurfaceType` | `1` |
| `_EnableAlphaTest` | `0` |
| `_ALPHATEST_ON（功能开关）` | `0` |
| `_ALPHABLEND_ON（功能开关）` | `1` |
| `_SrcBlend` / `_DstBlend` | `1` / `10` |
| `_ZTest` | `4` |
| `_ZWrite` | 原生 cloth07 为 `1` |
| 实际渲染队列 | `3000` |
| RenderType 标签 | `Transparent` |

标签可用字符串自定义属性 `eiem_tag.RenderType` 设置。Alpha 混合允许透明度渐变；透明重叠的绘制顺序与深度写入仍影响最终效果。若要让后绘制的透明层不被前层写入的深度遮挡，可另行试验 `_ZWrite=0`，但这与“开启透明”是两项设置，可能改变遮挡表现。

## 自发光

| 字段 | 起步设置 |
| --- | --- |
| `_UseEmission` | `1` |
| `_EMISSION（功能开关）` | `1` |
| `_EmissionMap` | 黑色区域不发光，有颜色的区域发光 |
| `_EmissionColor` | `1,1,1,1`，保留贴图颜色 |
| `_EmissionBrightness` | 可从 `2` 开始调亮；原生 cloth05 保存值为 `8` |

宝石可以使用独立发光贴图控制发光区域。整张有颜色的 BaseMap 同时用作 EmissionMap，会让整块宝石发光。亮面和画面光晕还受游戏曝光、Bloom 影响；自发光材质不会自动变成照亮周围物体的动态光源。

本次 baoshipink 已在当前工程启用 `_UseEmission` 和 `_EMISSION`，保留用户已有贴图、颜色及亮度。

## 呼吸灯

先配置自发光。2026-10-12 核对 Liino 来源 Shader 的编译程序后，确认呼吸计算位于 `_CHARACTER_VFX_SPECIAL` 分支；普通 `_EMISSION` 变体只计算固定发光，单独设置下列呼吸数值不会启用动态效果。

| 字段 | 起步设置 |
| --- | --- |
| `_EnableCharacterVFX` | `1` |
| `_CHARACTER_VFX_SPECIAL（功能开关）` | `1` |
| `_EmissionAlphaBrightBreath` | `1` |
| `_EmissionAlphaBrightBreathSpeed` | `1.6` |
| `_EmissionAlphaBrightBreathScaleMin` | `0.02` |
| `_EmissionAlphaBrightBreathScaleMax` | `1` |

这个编译分支先用游戏时间乘速度计算正弦，再映射到最小、最大亮度。发光图 A 通道是参与呼吸的遮罩：最终发光倍率为 `1 - A + A * 呼吸亮度`。A=1 完全参与，A=0 始终保持原亮度；它不是该材质的表面透明度。

仅使用呼吸时，将 `_VFXColor` 保持白色、`_VFXColorIntensity` 和 `_VFXColorAlpha` 保持 1，`_VFXAlphaAffectTotalMat`、`_UseVFXMainTexAsAlpha`、`_VFXFresnelAffectOpacity` 和 `_EnableVFXColorAdjustment` 设为 0，`_VFXFresnelColor` 设为 `0,0,0,0`，避免额外染色、边缘光和透明度修改。空的 VFX Main/Blend 纹理沿用 Shader 的白/黑默认纹理，无需打包新图。

还应检查组合对应的编译变体。当前 Liino 红色金属采用 `CHARACTER_VFX_SPECIAL、DIFF_RAMP_ON、EMISSION、METALLICSPECGLOSSMAP、NORMALMAP、SPEC_RAMP_ON`（各项带前导 `_`）；液体采用 `ALPHABLEND_ON、CHARACTER_VFX_SPECIAL、EMISSION、METALLICSPECGLOSSMAP、NORMALMAP`。已核对相应 D3D 编译程序包含呼吸计算，工程参数和导出数据另行验证；游戏中是否呈现预期亮度仍需 F10 后观察。其他 Shader 不应照搬这组关键字。Blender 亮度驱动仅用于预览，无需给游戏材质打关键帧。

## 流光

先配置自发光。当前 Liino 编译程序的发光 UV 滚动也位于上述角色 VFX 分支；需同时启用该分支。将 `_EmissionMap` 制作成“黑底、窄亮条、可平铺”的图片，然后试 `_EmissionMapUVSpeed=0.2,0,0,0`。前两个分量与游戏时间相乘后加到发光贴图 UV；方向、速度和实际外观仍需游戏内验证。图像纹理采用 Repeat，避免滚动后停在边缘。

先确认亮条确实移动，再调整速度、贴图缩放和亮度。全色发光贴图即使发生 UV 滚动也可能看不出移动；流光沿模型移动的路径还取决于 Mesh 的 UV 展开。

本次未自动给宝石增加呼吸或流光，避免替用户决定效果。

Unity 的 Shader 功能开关用于选择 Shader 变体，不能单靠修改数值属性代替，见 [Material.EnableKeyword](https://docs.unity.com/en-us/engine/6000.5/script-reference/unityengine/material/enablekeyword)。游戏专有字段以解包材质和编译程序为依据，实机效果的验收边界如上所述。
