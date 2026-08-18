"""Panda3D visualisation.

Panda3D types (`NodePath`, `LVector3`, `LQuaternion`, `Geom`) must not appear in
signatures outside this package. The domain returns a `JointPose` (axis + angle /
translation) and `viz/` translates it.

Importing `panda3d` is heavy and requires a graphics context — the whole package is
imported in `cli.run`, never at module level anywhere else.
"""
