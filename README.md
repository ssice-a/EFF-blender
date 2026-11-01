# EFF Blender

EFF Blender is the authoring add-on for AnimeStudio source packages. Install its release archive from [EFF Blender Releases](https://github.com/ssice-a/EFF-blender/releases).

The supported Blender version and current authoring workflow are documented in the [EFF main repository](https://github.com/ssice-a/EFF), including the [Mod tutorial](https://github.com/ssice-a/EFF/blob/main/Mod%20Tutorial.md) and [shared tools and index setup](https://github.com/ssice-a/EFF/blob/main/docs/shared-tools-and-indexes.md).

After installing the add-on, select the AnimeStudio unpack index location in **3D View > N > EFF > AnimeStudio 解包索引**. This is separate from add-on installation and does not require selecting the extracted-output directory. Blender targets 5.2; shared executable tools are downloaded from EFF Releases to the user-selected resource root.

Exports use Format 3 only and remove unused skin palette slots without editing the scene. To remove unused authoring groups explicitly, select Mesh objects in Object Mode and use **Mesh Data > EFF 网格 > 清理空顶点组**. This undoable operation removes only groups without positive weights, including zero-only groups. It preserves tiny positive weights and isolates shared Mesh datablocks before remapping group indices.
