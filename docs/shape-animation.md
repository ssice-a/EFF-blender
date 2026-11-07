# Unified Animation Groups

Shape Key and Bone animation are track types inside one animation group. A group owns one Clip ID, time range, integer sample rate, sampled timeline, playback speed, loop/autoplay state, and hotkey. Each Mesh or Armature keeps its own Action and enabled channels. Skeleton remains a separate resource.

1. Add Shape Keys to an EFF-bound Mesh and keyframe their `value`. The Action belongs to the Shape Keys data block; an Object transform Action is a different track type.
2. Keyframe Pose Bone transforms on an Armature. The first format exports bone location, rotation, and scale channels. Every animated bone must resolve to a path in the exported Skeleton.
3. Open N → EFF → Unified Animation Group. Select one or more animated Mesh and/or Armature objects, then create a group. Its initial time range is the union of member keyframe ranges. Add further Mesh or Armature members from the same panel.
4. Expand members under Shape Key Tracks or Bone Tracks to choose each Action, Action slot, and channels. The editors are separated by track type, while playback and sampling belong to the shared Clip. Use “Use Action Range” to recompute the group's time range. Export all members of the group together.
5. The advanced settings expose playback speed and integer sample rate, defaulting to 60 Hz. Preview maps the selected Shape Keys and Armature Actions onto one temporary timeline. Stopping restores the original Actions, slots, frame range, and Shape Key values.

Format 3 stores Shape scalar samples and Bone local TRS samples in the same Controls Clip. It does not declare a separate BoneAnimation clip. Shape targets use control-variable identities; Bone targets use a Skeleton ID and full hierarchy path.

Blender regression coverage includes mixed-group creation, shared settings, sampling, preview, and restoration. No Blender command-line executable was found in the current environment, so the live Blender integration test has not been run here. The DLL Catalog parses and validates mixed Clips and Skeleton paths, but runtime Bone Transform playback is not implemented yet; exported Bone tracks are not yet playable in game. Drivers and unbaked NLA must be baked to independent Actions first.
