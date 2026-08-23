"""Tests of bounding-box collision.

Pure arithmetic, so all of it runs without a window. The cases worth having are
the boundaries — boxes exactly touching, boxes separated on one axis only — and
the promise `colliding_pairs` makes about its output being order-independent,
which the renderer relies on to decide whether anything changed.
"""

from __future__ import annotations

import pytest

from pssim.domain.collision import (
    AABB,
    Body,
    aabb_around,
    bodies_collide,
    boxes_overlap,
    colliding_ids,
    colliding_pairs,
    parts_overlap,
)


def box(low: float, high: float, axis: int = 0) -> AABB:
    """A unit box stretched from `low` to `high` on one axis."""
    lows = [0.0, 0.0, 0.0]
    highs = [1.0, 1.0, 1.0]
    lows[axis] = low
    highs[axis] = high
    return AABB(low=(lows[0], lows[1], lows[2]), high=(highs[0], highs[1], highs[2]))


class TestAABB:
    def test_a_valid_box_is_accepted(self) -> None:
        made = AABB(low=(0.0, 0.0, 0.0), high=(1.0, 1.0, 1.0))

        assert made.high[0] == 1.0

    @pytest.mark.parametrize("axis", [0, 1, 2])
    def test_low_above_high_is_rejected_on_each_axis(self, axis: int) -> None:
        low = [0.0, 0.0, 0.0]
        high = [1.0, 1.0, 1.0]
        low[axis] = 2.0

        with pytest.raises(ValueError, match="exceeds high"):
            AABB(low=tuple(low), high=tuple(high))  # type: ignore[arg-type]


class TestBoxesOverlap:
    def test_two_boxes_sharing_volume_overlap(self) -> None:
        assert boxes_overlap(box(0.0, 1.0), box(0.5, 1.5))

    def test_a_box_inside_another_overlaps(self) -> None:
        inner = AABB(low=(0.2, 0.2, 0.2), high=(0.3, 0.3, 0.3))

        assert boxes_overlap(box(0.0, 1.0), inner)

    def test_boxes_exactly_touching_overlap(self) -> None:
        # Deliberate: a part resting exactly on another is worth flagging, and
        # `sensors.is_blocked` treats a tangent beam the same way.
        assert boxes_overlap(box(0.0, 1.0), box(1.0, 2.0))

    def test_a_gap_on_one_axis_is_enough_to_separate(self) -> None:
        assert not boxes_overlap(box(0.0, 1.0), box(1.001, 2.0))

    @pytest.mark.parametrize("axis", [0, 1, 2])
    def test_separation_is_detected_on_every_axis(self, axis: int) -> None:
        assert not boxes_overlap(box(0.0, 1.0, axis), box(2.0, 3.0, axis))

    def test_a_box_overlaps_itself(self) -> None:
        same = box(0.0, 1.0)

        assert boxes_overlap(same, same)


class TestCollidingPairs:
    def test_nothing_collides_in_an_empty_scene(self) -> None:
        assert colliding_pairs({}) == frozenset()

    def test_one_box_cannot_collide(self) -> None:
        assert colliding_pairs({"a": Body(box(0.0, 1.0))}) == frozenset()

    def test_two_overlapping_boxes_are_one_pair(self) -> None:
        pairs = colliding_pairs({"a": Body(box(0.0, 1.0)), "b": Body(box(0.5, 1.5))})

        assert pairs == frozenset({("a", "b")})

    def test_two_separated_boxes_are_no_pair(self) -> None:
        pairs = colliding_pairs({"a": Body(box(0.0, 1.0)), "b": Body(box(5.0, 6.0))})

        assert pairs == frozenset()

    def test_a_pair_is_reported_once(self) -> None:
        pairs = colliding_pairs({"b": Body(box(0.0, 1.0)), "a": Body(box(0.5, 1.5))})

        assert len(pairs) == 1

    def test_a_pair_is_ordered_by_id(self) -> None:
        # The renderer compares this frozenset against the previous frame's, so
        # ("a", "b") and ("b", "a") must not read as a change.
        pairs = colliding_pairs({"b": Body(box(0.0, 1.0)), "a": Body(box(0.5, 1.5))})

        assert pairs == frozenset({("a", "b")})

    def test_the_result_does_not_depend_on_iteration_order(self) -> None:
        forwards = colliding_pairs({"a": Body(box(0.0, 1.0)), "b": Body(box(0.5, 1.5))})
        backwards = colliding_pairs({"b": Body(box(0.5, 1.5)), "a": Body(box(0.0, 1.0))})

        assert forwards == backwards

    def test_three_boxes_in_a_row_give_two_pairs(self) -> None:
        pairs = colliding_pairs(
            {"a": Body(box(0.0, 1.0)), "b": Body(box(0.9, 2.0)), "c": Body(box(1.9, 3.0))}
        )

        assert pairs == frozenset({("a", "b"), ("b", "c")})

    def test_one_box_spanning_everything_collides_with_all(self) -> None:
        pairs = colliding_pairs(
            {"a": Body(box(0.0, 1.0)), "b": Body(box(5.0, 6.0)), "big": Body(box(-1.0, 10.0))}
        )

        assert pairs == frozenset({("a", "big"), ("b", "big")})

    def test_nothing_is_excluded_for_being_mounted_together(self) -> None:
        # The chosen rule: every overlapping pair counts. This function knows
        # nothing about joints, so there is nowhere for an exception to hide.
        pairs = colliding_pairs({"carriage": Body(box(0.0, 1.0)), "tool": Body(box(0.4, 0.6))})

        assert pairs == frozenset({("carriage", "tool")})


class TestCollidingIds:
    def test_nothing_collides_in_an_empty_set(self) -> None:
        assert colliding_ids(frozenset()) == frozenset()

    def test_both_members_of_a_pair_are_reported(self) -> None:
        assert colliding_ids(frozenset({("a", "b")})) == frozenset({"a", "b"})

    def test_an_id_in_two_pairs_is_reported_once(self) -> None:
        ids = colliding_ids(frozenset({("a", "b"), ("b", "c")}))

        assert ids == frozenset({"a", "b", "c"})


class TestAabbAround:
    def test_no_points_give_no_box(self) -> None:
        assert aabb_around(()) is None

    def test_one_point_gives_a_degenerate_box(self) -> None:
        box = aabb_around([(1.0, 2.0, 3.0)])

        assert box == AABB(low=(1.0, 2.0, 3.0), high=(1.0, 2.0, 3.0))

    def test_it_spans_every_point(self) -> None:
        box = aabb_around([(0.0, 0.0, 0.0), (1.0, -2.0, 3.0)])

        assert box == AABB(low=(0.0, -2.0, 0.0), high=(1.0, 0.0, 3.0))

    def test_each_axis_is_taken_independently(self) -> None:
        box = aabb_around([(5.0, 0.0, 0.0), (0.0, 5.0, 0.0), (0.0, 0.0, 5.0)])

        assert box == AABB(low=(0.0, 0.0, 0.0), high=(5.0, 5.0, 5.0))

    def test_the_eight_corners_of_a_box_give_that_box_back(self) -> None:
        # The identity case the renderer relies on: an unrotated model's corners
        # must reproduce its own box exactly, or every model would drift.
        original = AABB(low=(-1.0, 0.5, 2.0), high=(3.0, 4.0, 7.0))
        corners = [
            (x, y, z)
            for x in (original.low[0], original.high[0])
            for y in (original.low[1], original.high[1])
            for z in (original.low[2], original.high[2])
        ]

        assert aabb_around(corners) == original

    def test_it_accepts_a_generator(self) -> None:
        # The renderer passes one, so this must not be consumed twice internally.
        box = aabb_around((float(index), 0.0, 0.0) for index in range(3))

        assert box == AABB(low=(0.0, 0.0, 0.0), high=(2.0, 0.0, 0.0))


class TestPartsOverlap:
    """The narrow phase: does any part of one body meet any part of the other."""

    def test_nothing_overlaps_nothing(self) -> None:
        assert parts_overlap((), ()) is False

    def test_a_part_against_no_parts_does_not_overlap(self) -> None:
        assert parts_overlap((box(0.0, 1.0),), ()) is False

    def test_two_meeting_parts_overlap(self) -> None:
        assert parts_overlap((box(0.0, 1.0),), (box(0.5, 1.5),)) is True

    def test_two_separated_parts_do_not_overlap(self) -> None:
        assert parts_overlap((box(0.0, 1.0),), (box(5.0, 6.0),)) is False

    def test_it_finds_a_pair_buried_in_a_crowd(self) -> None:
        left = tuple(box(float(index), float(index) + 0.5) for index in range(20))
        right = (box(10.2, 10.4),)

        assert parts_overlap(left, right) is True

    def test_interleaved_parts_that_never_meet(self) -> None:
        # The case a whole-model box gets wrong: the two sets share the same
        # overall extent, yet no individual part touches another.
        left = tuple(box(float(index) * 2.0, float(index) * 2.0 + 0.5) for index in range(10))
        right = tuple(
            box(float(index) * 2.0 + 1.0, float(index) * 2.0 + 1.5) for index in range(10)
        )

        assert parts_overlap(left, right) is False

    def test_the_input_need_not_be_sorted(self) -> None:
        # The sweep sorts internally; a caller forgetting to would otherwise
        # silently miss collisions rather than fail.
        left = (box(9.0, 10.0), box(0.0, 1.0), box(4.0, 5.0))
        right = (box(4.5, 4.6),)

        assert parts_overlap(left, right) is True

    def test_separation_on_y_alone_is_enough(self) -> None:
        # The sweep runs along X, so a pair that overlaps in X must still be
        # rejected on the other axes.
        left = (AABB(low=(0.0, 0.0, 0.0), high=(1.0, 1.0, 1.0)),)
        right = (AABB(low=(0.0, 5.0, 0.0), high=(1.0, 6.0, 1.0)),)

        assert parts_overlap(left, right) is False

    def test_it_is_symmetric(self) -> None:
        left = (box(0.0, 1.0), box(4.0, 5.0))
        right = (box(4.5, 6.0),)

        assert parts_overlap(left, right) == parts_overlap(right, left)


class TestBodiesCollide:
    def test_bodies_whose_boxes_miss_do_not_collide(self) -> None:
        first = Body(box(0.0, 1.0), (box(0.0, 1.0),))
        second = Body(box(5.0, 6.0), (box(5.0, 6.0),))

        assert bodies_collide(first, second) is False

    def test_overlapping_boxes_with_meeting_parts_collide(self) -> None:
        first = Body(box(0.0, 2.0), (box(0.0, 1.0),))
        second = Body(box(0.5, 2.5), (box(0.5, 1.5),))

        assert bodies_collide(first, second) is True

    def test_overlapping_boxes_with_parts_apart_do_not_collide(self) -> None:
        # This is the reported bug: a carriage inside a frame overlaps the
        # frame's overall box wherever it stands, and the warning never cleared.
        first = Body(box(0.0, 10.0), (box(0.0, 1.0),))
        second = Body(box(0.0, 10.0), (box(9.0, 10.0),))

        assert bodies_collide(first, second) is False

    def test_a_body_with_no_parts_falls_back_to_its_box(self) -> None:
        first = Body(box(0.0, 1.0))
        second = Body(box(0.5, 1.5), (box(0.5, 1.5),))

        assert bodies_collide(first, second) is True

    def test_the_box_test_comes_first(self) -> None:
        # Bodies far apart must be rejected without looking at parts at all,
        # which is what keeps the cost near zero for a scene of many models.
        first = Body(box(0.0, 1.0), (box(0.0, 1.0),))
        second = Body(box(100.0, 101.0), (box(0.0, 1.0),))

        assert bodies_collide(first, second) is False


class TestCollidingPairsUsesParts:
    def test_a_pair_whose_parts_never_meet_is_not_reported(self) -> None:
        bodies = {
            "frame": Body(box(0.0, 10.0), (box(0.0, 1.0),)),
            "carriage": Body(box(0.0, 10.0), (box(9.0, 10.0),)),
        }

        assert colliding_pairs(bodies) == frozenset()

    def test_a_pair_whose_parts_meet_is_reported(self) -> None:
        bodies = {
            "frame": Body(box(0.0, 10.0), (box(0.0, 1.0),)),
            "carriage": Body(box(0.0, 10.0), (box(0.5, 1.5),)),
        }

        assert colliding_pairs(bodies) == frozenset({("carriage", "frame")})
