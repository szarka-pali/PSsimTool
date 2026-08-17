"""Panda3D vizualizácia.

Panda3D typy (`NodePath`, `LVector3`, `LQuaternion`, `Geom`) sa nesmú objaviť
v signatúrach mimo tohto balíka. Doména vracia `JointPose` (os + uhol / posun)
a `viz/` si to preloží.

Import `panda3d` je ťažký a vyžaduje grafický kontext — celý balík sa importuje
až v `cli.run`, nikdy na module level inde.
"""
