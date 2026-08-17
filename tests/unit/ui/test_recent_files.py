"""Tests for recent-project ordering.

Only the pure part is here — no Qt. The persistence layer is thin enough that
the ordering rules are where the mistakes would be.
"""

from __future__ import annotations

import pytest

from pssim.ui.recent_files import MAX_RECENT, promote, shorten


class TestPromote:
    def test_new_entry_goes_first(self) -> None:
        assert promote(("b", "c"), "a") == ("a", "b", "c")

    def test_first_entry_of_an_empty_list(self) -> None:
        assert promote((), "a") == ("a",)

    def test_reopening_moves_to_the_front(self) -> None:
        assert promote(("a", "b", "c"), "c") == ("c", "a", "b")

    def test_no_duplicates(self) -> None:
        assert promote(("a", "b"), "a") == ("a", "b")

    def test_reopening_the_front_entry_changes_nothing(self) -> None:
        assert promote(("a", "b"), "a") == ("a", "b")

    def test_list_is_capped(self) -> None:
        existing = tuple(str(index) for index in range(MAX_RECENT))

        assert len(promote(existing, "new")) == MAX_RECENT

    def test_capping_drops_the_oldest(self) -> None:
        existing = tuple(str(index) for index in range(MAX_RECENT))

        assert str(MAX_RECENT - 1) not in promote(existing, "new")

    def test_custom_limit(self) -> None:
        assert promote(("a", "b", "c"), "d", limit=2) == ("d", "a")

    def test_case_differences_count_as_the_same_file(self) -> None:
        # Windows paths differ only in case all the time; two entries for one
        # file would be a confusing menu.
        assert promote(("C:/Models/Line.pssim",), "c:/models/line.pssim") == (
            "c:/models/line.pssim",
        )

    def test_original_tuple_is_untouched(self) -> None:
        existing = ("a", "b")

        promote(existing, "c")

        assert existing == ("a", "b")


class TestShorten:
    def test_short_path_is_unchanged(self) -> None:
        assert shorten("C:/a/line.pssim") == "C:/a/line.pssim"

    def test_long_path_is_trimmed(self) -> None:
        assert len(shorten("C:/" + "x" * 200 + "/line.pssim", max_length=40)) == 40

    def test_trimming_keeps_the_end(self) -> None:
        # The file name identifies the project; the long prefix says nothing.
        assert shorten("C:/" + "x" * 200 + "/line.pssim", max_length=40).endswith("line.pssim")

    def test_trimmed_path_is_marked(self) -> None:
        assert shorten("C:/" + "x" * 200 + "/line.pssim", max_length=40).startswith("…")

    @pytest.mark.parametrize("length", [10, 25, 60, 120])
    def test_never_exceeds_the_limit(self, length: int) -> None:
        assert len(shorten("C:/" + "y" * 300 + "/project.pssim", max_length=length)) <= length
