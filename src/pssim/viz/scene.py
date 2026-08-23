"""Building the `NodePath` hierarchy from the cache.

Split out of `viz/app.py` because the scene also has to be built **without a machine
definition** — when simply opening a STEP file in the UI there is no `machines/*.yaml` and
there are no joints.

It needs neither a window nor a `ShowBase`: it returns a detached root that the caller
attaches wherever they want. That makes it testable headless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pssim.cad.model import CadAssembly
from pssim.domain.machine import Transform, Vec3
from pssim.observability import get_logger
from pssim.viz.mesh_loader import load_geom_node
from pssim.viz.transforms import Quaternion, rpy_to_quat

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BuiltScene:
    """The built scene and what needs to be known about it afterwards."""

    root: Any
    """The root `NodePath`. It is not attached to `render` — the caller does that."""

    node_paths: dict[str, Any] = field(default_factory=dict)
    """The mapping from a node's stable path to its `NodePath`."""

    base_transforms: dict[str, tuple[Vec3, Quaternion]] = field(default_factory=dict)
    """The node's placement from CAD. Joint movement is added on top of it, see R9."""

    missing_meshes: int = 0
    """How many nodes had geometry missing from the cache."""

    @property
    def is_empty(self) -> bool:
        return not self.node_paths


def build_scene(
    assembly: CadAssembly,
    cache_dir: Path,
    name: str = "model",
    flatten: frozenset[str] = frozenset(),
) -> BuiltScene:
    """Assemble the scene from the assembly and load the geometry from the cache.

    `flatten` holds the paths of the nodes that may be merged into a single `Geom` —
    typically the static parts. Moving nodes must not be flattened, they would lose their
    own transformation.
    """
    from panda3d.core import NodePath

    root = NodePath(name)
    node_paths: dict[str, Any] = {}
    base_transforms: dict[str, tuple[Vec3, Quaternion]] = {}
    missing_meshes = 0

    # The parent-before-child order is MANDATORY: the parent is looked up among the nodes
    # already created, and in the opposite order the tree would come out flat.
    for node in assembly.nodes_parents_first:
        parent = _parent_of(node.path, node_paths, root)
        node_path = parent.attachNewNode(node.name)
        _apply_transform(node_path, node.transform)
        node_path.setColor(*node.color)
        base_transforms[node.path] = (node.transform.xyz, rpy_to_quat(node.transform.rpy))

        if node.mesh is not None:
            geom_node = load_geom_node(cache_dir / node.mesh, node.path)
            if geom_node is None:
                missing_meshes += 1
            else:
                node_path.attachNewNode(geom_node)

        node_paths[node.path] = node_path

    if missing_meshes:
        logger.warning(
            "some geometry is missing - run `pssim import-step`",
            missing=missing_meshes,
            total=len(assembly.nodes),
        )

    for path in flatten:
        node_path = node_paths.get(path)
        if node_path is not None and not node_path.getChildren():
            node_path.flattenStrong()

    return BuiltScene(
        root=root,
        node_paths=node_paths,
        base_transforms=base_transforms,
        missing_meshes=missing_meshes,
    )


def _parent_of(path: str, node_paths: dict[str, Any], root: Any) -> Any:
    parent_path = path.rsplit("/", 1)[0] if "/" in path else None
    if parent_path is None:
        return root
    return node_paths.get(parent_path, root)


def _apply_transform(node_path: Any, transform: Transform) -> None:
    """Set the fixed transformation of a node.

    The rotation goes through a quaternion, not through HPR: converting rpy → HPR would
    mean guessing Panda3D's axis order convention, whereas `rpy_to_quat` is verified
    against a rotation matrix in `tests/unit/viz/test_transforms.py`.
    """
    from panda3d.core import LQuaternion

    node_path.setPos(*transform.xyz)
    node_path.setQuat(LQuaternion(*rpy_to_quat(transform.rpy)))
