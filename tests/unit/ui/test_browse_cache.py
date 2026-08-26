"""What the address space said last time.

Pure — no Qt, no server — which is the point of the cache being its own thing:
"does refreshing a struct drop its fields" is a question about keys, and it is
answerable in microseconds.
"""

from __future__ import annotations

from pssim.io.opcua_browse_session import BrowseNode, BrowseResult, NodeKind
from pssim.ui.browse_cache import BrowseCache

STRUCT = "ns=2;s=Struct.AxisState"
FOLDER = "ns=2;i=1"


def result(*names: str) -> BrowseResult:
    return BrowseResult(
        nodes=tuple(
            BrowseNode(
                node_id=f"ns=2;s={name}",
                browse_name=name,
                display_name=name,
                kind=NodeKind.VARIABLE,
            )
            for name in names
        )
    )


class TestRememberingAnAnswer:
    def test_nothing_is_known_at_first(self) -> None:
        assert BrowseCache().get(FOLDER) is None

    def test_what_went_in_comes_out(self) -> None:
        cache = BrowseCache()
        cache.put(FOLDER, "", result("X", "Y"))

        answer = cache.get(FOLDER)
        assert answer is not None
        assert [node.browse_name for node in answer.nodes] == ["X", "Y"]

    def test_a_path_is_a_different_place(self) -> None:
        # A struct's every field shares one node id (R21).
        cache = BrowseCache()
        cache.put(STRUCT, "", result("Position", "Enabled"))
        cache.put(STRUCT, "Position", result("X", "Y", "Z"))

        assert len(cache) == 2

    def test_and_they_do_not_shadow_each_other(self) -> None:
        cache = BrowseCache()
        cache.put(STRUCT, "", result("Position"))
        cache.put(STRUCT, "Position", result("X"))

        inner = cache.get(STRUCT, "Position")
        assert inner is not None
        assert [node.browse_name for node in inner.nodes] == ["X"]

    def test_the_same_place_twice_replaces(self) -> None:
        cache = BrowseCache()
        cache.put(FOLDER, "", result("X"))
        cache.put(FOLDER, "", result("X", "Y"))

        answer = cache.get(FOLDER)
        assert answer is not None
        assert len(answer.nodes) == 2


class TestATruncatedAnswerIsNotKept:
    """It is a partial picture of that folder, and serving it again from here
    would make the truncation permanent for the session."""

    def test_it_is_dropped(self) -> None:
        cache = BrowseCache()
        cache.put(FOLDER, "", BrowseResult(nodes=result("X").nodes, is_truncated=True))

        assert cache.get(FOLDER) is None

    def test_a_complete_one_is_kept(self) -> None:
        cache = BrowseCache()
        cache.put(FOLDER, "", result("X"))

        assert cache.get(FOLDER) is not None


class TestForgettingOnePlace:
    def test_it_goes(self) -> None:
        cache = BrowseCache()
        cache.put(FOLDER, "", result("X"))

        cache.forget(FOLDER)

        assert cache.get(FOLDER) is None

    def test_a_neighbour_stays(self) -> None:
        cache = BrowseCache()
        cache.put(FOLDER, "", result("X"))
        cache.put("ns=2;i=2", "", result("Y"))

        cache.forget(FOLDER)

        assert cache.get("ns=2;i=2") is not None

    def test_a_struct_s_fields_go_with_it(self) -> None:
        # They are `(same node id, longer path)`, so they are stale the moment
        # the struct is re-read.
        cache = BrowseCache()
        cache.put(STRUCT, "", result("Position"))
        cache.put(STRUCT, "Position", result("X"))
        cache.put(STRUCT, "Position.X", result())

        cache.forget(STRUCT)

        assert len(cache) == 0

    def test_forgetting_a_field_leaves_the_struct(self) -> None:
        cache = BrowseCache()
        cache.put(STRUCT, "", result("Position"))
        cache.put(STRUCT, "Position", result("X"))

        cache.forget(STRUCT, "Position")

        assert cache.get(STRUCT, "") is not None
        assert cache.get(STRUCT, "Position") is None

    def test_a_sibling_field_stays(self) -> None:
        cache = BrowseCache()
        cache.put(STRUCT, "Position", result("X"))
        cache.put(STRUCT, "Limits", result("[0]"))

        cache.forget(STRUCT, "Position")

        assert cache.get(STRUCT, "Limits") is not None

    def test_a_field_whose_name_merely_starts_the_same_stays(self) -> None:
        # `Position` must not take `PositionRaw` with it. Compared as parsed
        # steps rather than as text, which is what makes that so.
        cache = BrowseCache()
        cache.put(STRUCT, "Position", result("X"))
        cache.put(STRUCT, "PositionRaw", result("Y"))

        cache.forget(STRUCT, "Position")

        assert cache.get(STRUCT, "PositionRaw") is not None

    def test_forgetting_what_was_never_there_is_quiet(self) -> None:
        cache = BrowseCache()

        cache.forget(FOLDER)

        assert len(cache) == 0


class TestForgettingTheServer:
    def test_everything_goes(self) -> None:
        cache = BrowseCache()
        cache.put(FOLDER, "", result("X"))
        cache.put(STRUCT, "Position", result("Y"))

        cache.clear()

        assert len(cache) == 0


class TestTheBound:
    def test_it_stops_growing(self) -> None:
        cache = BrowseCache(limit=3)

        for index in range(10):
            cache.put(f"ns=2;i={index}", "", result("X"))

        assert len(cache) == 3

    def test_the_newest_survives(self) -> None:
        cache = BrowseCache(limit=2)

        for index in range(5):
            cache.put(f"ns=2;i={index}", "", result("X"))

        assert cache.get("ns=2;i=4") is not None

    def test_replacing_an_entry_does_not_evict(self) -> None:
        cache = BrowseCache(limit=2)
        cache.put("a", "", result("X"))
        cache.put("b", "", result("Y"))

        cache.put("a", "", result("Z"))

        assert len(cache) == 2
        assert cache.get("b") is not None
