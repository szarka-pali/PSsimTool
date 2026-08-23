"""Tests of the pure picking geometry (no Panda3D).

The click-vs-drag and Panda3D-collision mechanics are covered by
`tests/integration/test_viz_picking.py`.
"""

from __future__ import annotations

from pssim.viz.picking import MIN_HALF_THICKNESS_M, padded_box_bounds


class TestPaddedBoxBounds:
    def test_a_box_with_real_volume_is_left_alone(self) -> None:
        low, high = padded_box_bounds((0.0, 0.0, 0.0), (1.0, 2.0, 3.0))

        assert (low, high) == ((0.0, 0.0, 0.0), (1.0, 2.0, 3.0))

    def test_a_flat_axis_is_padded_symmetrically(self) -> None:
        low, high = padded_box_bounds((0.0, 0.0, 5.0), (1.0, 1.0, 5.0))

        assert low[2] == 5.0 - MIN_HALF_THICKNESS_M
        assert high[2] == 5.0 + MIN_HALF_THICKNESS_M

    def test_padding_keeps_the_flat_axis_centred(self) -> None:
        low, high = padded_box_bounds((0.0, 0.0, 5.0), (1.0, 1.0, 5.0))

        assert (low[2] + high[2]) / 2.0 == 5.0

    def test_a_slightly_thin_axis_is_still_widened_to_the_minimum(self) -> None:
        # Narrower than 2 * MIN_HALF_THICKNESS_M, but not exactly zero.
        thin = MIN_HALF_THICKNESS_M / 2.0
        low, high = padded_box_bounds((0.0, 0.0, 0.0), (1.0, 1.0, thin))

        assert high[2] - low[2] == 2.0 * MIN_HALF_THICKNESS_M

    def test_every_axis_can_be_flat_at_once(self) -> None:
        low, high = padded_box_bounds((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))

        assert all(hi - lo == 2.0 * MIN_HALF_THICKNESS_M for lo, hi in zip(low, high, strict=True))
