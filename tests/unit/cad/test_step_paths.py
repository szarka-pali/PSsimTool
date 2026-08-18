"""Tests of composing stable node paths.

The paths must be deterministic between imports — `machines/*.yaml` refers to them.
This is the one part of `step_import.py` that is pure and testable without
OpenCASCADE.
"""

from __future__ import annotations

import pytest

from pssim.cad.step_import import RawNode, build_paths, scale_transform
from pssim.domain.machine import Transform


def raw(
    name: str, *children: RawNode, geometry: bool = True, mesh_key: str | None = None
) -> RawNode:
    """A node for the tests. Leaves have geometry, nodes with children are organisational.

    `mesh_key` can be given explicitly so that sharing a mesh between instances of the
    same part can be tested.
    """
    is_leaf = geometry and not children
    return RawNode(
        name=name,
        transform=Transform(),
        children=children,
        mesh_key=(mesh_key or f"def:{name}") if is_leaf else None,
        triangle_count=12 if is_leaf else 0,
    )


class TestPaths:
    def test_a_single_root(self) -> None:
        assembly = build_paths((raw("base"),))

        assert assembly.roots == ("base",)

    def test_nesting_is_joined_with_a_slash(self) -> None:
        assembly = build_paths((raw("base", raw("portal", raw("carriage"))),))

        assert {node.path for node in assembly.nodes} == {
            "base",
            "base/portal",
            "base/portal/carriage",
        }

    def test_a_unique_name_is_not_indexed(self) -> None:
        assembly = build_paths((raw("base", raw("portal")),))

        assert assembly.node("base/portal") is not None

    def test_siblings_of_one_name_are_indexed_from_one(self) -> None:
        # Ten `Part1`s in one assembly is common in practice.
        assembly = build_paths((raw("base", raw("Part1"), raw("Part1"), raw("Part1")),))

        paths = {node.path for node in assembly.nodes}

        assert {"base/Part1[1]", "base/Part1[2]", "base/Part1[3]"} <= paths

    def test_indexing_is_local_to_a_level(self) -> None:
        assembly = build_paths(
            (raw("base", raw("A", raw("X"), raw("X")), raw("B", raw("X"), raw("X"))),)
        )

        paths = {node.path for node in assembly.nodes}

        assert {"base/A/X[1]", "base/A/X[2]", "base/B/X[1]", "base/B/X[2]"} <= paths

    def test_the_order_is_deterministic(self) -> None:
        tree = (raw("base", raw("P"), raw("P")),)

        first = [node.path for node in build_paths(tree).nodes]
        second = [node.path for node in build_paths(tree).nodes]

        assert first == second

    def test_children_are_recorded_on_the_parent(self) -> None:
        assembly = build_paths((raw("base", raw("a"), raw("b")),))
        base = assembly.node("base")

        assert base is not None
        assert base.children == ("base/a", "base/b")


class TestNodeOrder:
    def test_nodes_is_bottom_up(self) -> None:
        # The recursive walk appends a node after its children. Anyone building a
        # hierarchy must use nodes_parents_first, or the tree comes out flat.
        assembly = build_paths((raw("base", raw("diel")),))

        paths = [node.path for node in assembly.nodes]

        assert paths.index("base/diel") < paths.index("base")

    def test_nodes_parents_first_puts_the_parent_first(self) -> None:
        assembly = build_paths((raw("base", raw("portal", raw("carriage"))),))

        paths = [node.path for node in assembly.nodes_parents_first]

        assert (
            paths.index("base") < paths.index("base/portal") < paths.index("base/portal/carriage")
        )

    def test_nodes_parents_first_contains_every_node(self) -> None:
        assembly = build_paths((raw("base", raw("a"), raw("b", raw("c"))),))

        assert len(assembly.nodes_parents_first) == len(assembly.nodes)

    def test_nodes_parents_first_is_deterministic(self) -> None:
        tree = (raw("base", raw("P"), raw("P"), raw("Q")),)

        first = [node.path for node in build_paths(tree).nodes_parents_first]
        second = [node.path for node in build_paths(tree).nodes_parents_first]

        assert first == second


class TestMeshSharing:
    def test_instances_of_one_part_share_a_file(self) -> None:
        # An assembly with a thousand screws should have one screw in the cache, not a thousand.
        assembly = build_paths(
            (raw("base", raw("Bolt", mesh_key="def:bolt"), raw("Bolt", mesh_key="def:bolt")),)
        )

        first = assembly.node("base/Bolt[1]")
        second = assembly.node("base/Bolt[2]")

        assert first is not None
        assert second is not None
        assert first.mesh == second.mesh

    def test_different_parts_have_different_files(self) -> None:
        assembly = build_paths((raw("base", raw("a"), raw("b")),))

        first = assembly.node("base/a")
        second = assembly.node("base/b")

        assert first is not None
        assert second is not None
        assert first.mesh != second.mesh

    def test_same_named_but_different_parts_do_not_merge(self) -> None:
        # Two different parts may share a name in STEP — the mesh_key tells them apart.
        assembly = build_paths(
            (raw("base", raw("Part", mesh_key="def:1"), raw("Part", mesh_key="def:2")),)
        )

        first = assembly.node("base/Part[1]")
        second = assembly.node("base/Part[2]")

        assert first is not None
        assert second is not None
        assert first.mesh != second.mesh


class TestGeometry:
    def test_a_node_with_geometry_has_a_mesh(self) -> None:
        assembly = build_paths((raw("diel"),))
        node = assembly.node("diel")

        assert node is not None
        assert node.has_geometry is True

    def test_an_organisational_node_has_no_mesh(self) -> None:
        assembly = build_paths((raw("base", raw("diel")),))
        base = assembly.node("base")

        assert base is not None
        assert base.mesh is None

    def test_the_triangle_counts_add_up(self) -> None:
        assembly = build_paths((raw("base", raw("a"), raw("b")),))

        assert assembly.triangle_count == 24


class TestScaling:
    def test_translation_is_scaled(self) -> None:
        # If the vertices shrink 1000x and the offsets do not, the model falls apart.
        scaled = scale_transform(Transform(xyz=(1000.0, 2000.0, 0.0)), 1e-3)

        assert scaled.xyz == pytest.approx((1.0, 2.0, 0.0))

    def test_rotation_is_not_scaled(self) -> None:
        scaled = scale_transform(Transform(rpy=(0.5, 0.0, 0.0)), 1e-3)

        assert scaled.rpy == pytest.approx((0.5, 0.0, 0.0))


class TestSimilarPaths:
    def test_najde_podobnu_cestu_pre_chybovu_spravu(self) -> None:
        # Without this, "node not found" is a useless error when there are a thousand nodes.
        assembly = build_paths((raw("base", raw("portal"), raw("cover")),))

        assert "base/portal" in assembly.similar_paths("portal")

    def test_an_unknown_path_returns_nothing_or_candidates(self) -> None:
        assembly = build_paths((raw("base"),))

        assert isinstance(assembly.similar_paths("zzz_neexistuje"), tuple)
