# EFF Blender

EFF panels appear in the 3D View sidebar. Move the pointer over a 3D View, press **N**, then select the **EFF** tab. Panels in Material, Object, and Mesh Data Properties remain in their matching Properties contexts.

EFF Blender is the authoring add-on for AnimeStudio source packages. Install its release archive from [EFF Blender Releases](https://github.com/ssice-a/EFF-blender/releases).

The supported Blender version and current authoring workflow are documented in the [EFF main repository](https://github.com/ssice-a/EFF), including the [Mod tutorial](https://github.com/ssice-a/EFF/blob/main/Mod%20Tutorial.md) and [shared tools and index setup](https://github.com/ssice-a/EFF/blob/main/docs/shared-tools-and-indexes.md).

After installing the add-on, select the AnimeStudio unpack index location in **3D View > N > EFF > AnimeStudio 解包索引**. This is separate from add-on installation and does not require selecting the extracted-output directory. Blender targets 5.2; shared executable tools are downloaded from EFF Releases to the user-selected resource root.

Exports use Format 3 only and remove unused skin palette slots without editing the scene. To remove unused authoring groups explicitly, select Mesh objects in Object Mode and use **Mesh Data > EFF 网格 > 清理空顶点组**. This undoable operation removes only groups without positive weights, including zero-only groups. It preserves tiny positive weights and isolates shared Mesh datablocks before remapping group indices.

**Material Properties > EFF 材质 > 导入 EFF 材质** accepts an AnimeStudio EFFSOURCE `.mat` or a Format 3 Mod Material `.effres`. Keep the surrounding package and `mod.ini`: source materials bind their extracted images through Texture `path`, while Mod materials bind DDS through Texture `dds`. Only images referenced by the selected material are loaded. The material is assigned to the active slot without modifying the previous Material datablock. Mod material edits remain sparse; exporting them again requires the matching EFFSOURCE baseline, selected in the export dialog or inherited from the target Mesh's source package.
