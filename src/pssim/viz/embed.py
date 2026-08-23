"""Panda3D rendering into somebody else's window.

Panda3D can draw into a window created by someone else
(`WindowProperties.setParentWindow`), which is what makes embedding it in a
`QWidget` possible. See docs/architecture.md R3.

Why this lives in `viz/` and not in `ui/`: `ui/` must not import Panda3D. This
class is the boundary — Panda3D on the inside, only numbers and `CadAssembly` on
the outside. `ui/viewport.py` merely holds it and forwards Qt events.

The render loop **does not belong to Panda3D**: `base.run()` would take over and
the host GUI would freeze. The caller ticks and calls `step()`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Final

from pssim.cad.model import CadAssembly
from pssim.domain.collision import AABB, Body, aabb_around, colliding_ids, colliding_pairs
from pssim.domain.machine import Rgba, Transform, Vec3
from pssim.domain.model_joints import (
    Anchor,
    ModelJoint,
    anchor_pose,
    direction_of,
    effective_limits,
    joint_value_pose,
    rotation_onto,
)
from pssim.domain.placement import IDENTITY_PLACEMENT
from pssim.domain.sensors import (
    DISTANCE_KINDS,
    ENCODER_KINDS,
    Sensor,
    SensorReading,
    read_sensor,
)
from pssim.observability import get_logger
from pssim.viz.axes import (
    HIGHLIGHT_COLOR,
    HIGHLIGHT_THICKNESS_PX,
    axis_length_for,
    box_corners,
    make_axes_node,
    make_box_outline,
)
from pssim.viz.camera import scene_radius, setup_lights
from pssim.viz.collision_markers import COLLISION_COLOR, COLLISION_THICKNESS_PX
from pssim.viz.floor import FloorState, floor_half_extent_for, make_floor_node
from pssim.viz.joint_markers import (
    JOINT_MARKER_COLOR,
    make_joint_label,
    make_joint_marker,
)
from pssim.viz.orbit import OrbitCamera
from pssim.viz.orbit_control import OrbitController
from pssim.viz.picking import PointPicker
from pssim.viz.scene import build_scene
from pssim.viz.sensor_markers import aabb_of, make_sensor_marker
from pssim.viz.transforms import axis_angle_to_quat, rpy_to_quat

logger = get_logger(__name__)

BACKGROUND: Final = (0.12, 0.13, 0.15, 1.0)

#: The height of every piece of 3D text: the X/Y/Z glyphs on a coordinate cross
#: and a joint's name label alike.
#:
#: **One size for the whole scene**, deliberately. Each of these used to be
#: derived from something different — a label from its joint's span, a glyph from
#: its cross's arm length — and derived sizes cannot agree: the origin cross ended
#: up with 100 mm letters next to a joint cross with 3.1 mm ones. Stating the size
#: is the only way to make them match.
DEFAULT_TEXT_SIZE_M: Final = 0.05

#: The arm length of every coordinate cross: the origin one, a selected model's,
#: and a joint's initial frame. Same reasoning as the text size — these were
#: derived from the scene radius, the model's own radius and the joint's span
#: respectively, which is why they ranged over 100x.
#:
#: The cost of stating it: a scene at a very different scale wants this changed
#: once, where the old rule guessed. That is the trade the uniformity buys.
DEFAULT_CROSS_SIZE_M: Final = 0.2

#: The arm length of the **origin** cross, which has its own setting and its own
#: switch. It is the scene's reference rather than an annotation on one item, so
#: it is the one cross worth sizing and hiding on its own — the model and joint
#: crosses stay on the shared size, which is what makes them match each other.
DEFAULT_ORIGIN_CROSS_SIZE_M: Final = 0.2


def _marker_joint_at_origin(joint: ModelJoint) -> ModelJoint:
    """The same joint with its geometry expressed from zero.

    The marker hangs off the base node, which already sits at `joint.origin`;
    drawing the joint's absolute coordinates there would place it at twice the
    offset. Only the marker needs this — the pose maths never touches `origin`.
    """
    ox, oy, oz = joint.origin
    tx, ty, tz = joint.target
    return replace(joint, origin=(0.0, 0.0, 0.0), target=(tx - ox, ty - oy, tz - oz))


def sensor_is_active_reading(sensor: Sensor, reading: SensorReading) -> bool:
    """Whether a reading counts as "seeing something", for the marker's colour.

    Derived from the reading already taken rather than re-running the maths: the
    colour and the number must never disagree, and `domain.sensors.is_active`
    would compute the whole thing a second time to answer the same question.
    """
    if sensor.kind in ENCODER_KINDS:
        return False
    if sensor.kind in DISTANCE_KINDS:
        return reading.is_valid
    return reading.value != 0.0


def _part_bounds(built: Any) -> tuple[AABB, ...]:
    """Every part's box in the model root's coordinates, measured once.

    Each part's **own geometry** is measured, not its subtree. That distinction
    is the whole point: a STEP assembly's interior nodes are subassemblies, so
    measuring subtrees produced one box spanning nearly the entire model
    (measured: 1 of 1052 boxes covered more than half of it). That box overlapped
    any neighbouring model, so the "per part" check reported a collision wherever
    the real parts were — indistinguishable from comparing the outlines.

    Measuring the `GeomNode` each part carries instead gives 955 boxes for the
    same assembly, none of them spanning more than half the model.

    Costs ~690 ms for that assembly, on top of an import that already takes
    seconds — paid once at load so the per-check work is only corner transforms.
    A node with no geometry of its own contributes nothing.
    """
    boxes: list[AABB] = []
    for node_path in built.node_paths.values():
        for child in node_path.getChildren():
            if not child.node().isGeomNode():
                continue
            box = aabb_of(child, built.root)
            if box is not None:
                boxes.append(box)
    return tuple(boxes)


def offscreen_showbase(size: tuple[int, int]) -> Any:
    """Return a `ShowBase` for rendering without a window.

    Panda3D allows **one `ShowBase` per process** — a second attempt raises. That
    is harmless for a single render from the CLI, but tests render several times
    in a row, so an existing instance is reused.
    """
    import builtins

    from direct.showbase.ShowBase import ShowBase
    from panda3d.core import loadPrcFileData

    existing = getattr(builtins, "base", None)
    if existing is not None:
        return existing

    loadPrcFileData("", f"window-type offscreen\nwin-size {size[0]} {size[1]}")
    return ShowBase()


class EmbeddedRenderer:
    """Panda3D drawing into a window that belongs to somebody else.

    `ShowBase` may exist **only once** per process, which makes this renderer
    effectively a singleton — a second instance would fail.
    """

    def __init__(
        self,
        parent_handle: int,
        width: int,
        height: int,
        background: tuple[float, float, float, float] = BACKGROUND,
    ) -> None:
        from direct.showbase.ShowBase import ShowBase
        from panda3d.core import WindowProperties, loadPrcFileData

        # `window-type none` defers creating the window: it has to be opened with
        # a reference to the parent, or Panda3D would open its own separate one.
        loadPrcFileData("", "window-type none")
        self._base: Any = ShowBase()

        properties = WindowProperties()
        properties.setParentWindow(parent_handle)
        properties.setOrigin(0, 0)
        properties.setSize(max(width, 1), max(height, 1))
        self._base.openDefaultWindow(props=properties)
        self._base.setBackgroundColor(*background)

        self._models: dict[str, Any] = {}
        """Model id -> its root `NodePath`. Insertion order matters for the tree."""
        self._placements: dict[str, Transform] = {}
        self._axes_root: Any = None
        self._highlight_root: Any = None
        self._highlighted_id: str | None = None
        self._joint_highlight_root: Any = None
        """The cross on the selected joint's initial coordinate system."""
        self._joint_highlighted_id: str | None = None
        self._floor_state = FloorState()
        self._floor_root: Any = None
        self._sensors: dict[str, Sensor] = {}
        self._sensor_active: dict[str, bool] = {}
        self._sensor_roots: dict[str, Any] = {}
        self._sensor_mounts: dict[str, str] = {}
        """Sensor id -> the model or joint id carrying it. A sensor's own point
        and direction are in that thing's frame, so one bolted to a carriage
        rides it."""
        self._sensor_readings: dict[str, SensorReading] = {}
        self._show_axes: dict[str, bool] = {}
        """Item id -> whether its selection cross is drawn. Models and joints
        share one dict: the ids come from different registries and never collide,
        and the flag means the same thing for both."""
        self._model_local_bounds: dict[str, AABB] = {}
        """Each model's box in **its own** coordinates, measured once when it is
        added. Geometry never changes after that; only the transform above it
        does — see `_world_box`."""
        self._model_part_bounds: dict[str, tuple[AABB, ...]] = {}
        """The same, per individual part. What makes a collision answer mean
        something: one box round a whole assembly overlaps its neighbour forever."""
        self._text_size_m = DEFAULT_TEXT_SIZE_M
        """One height for every piece of 3D text — see `DEFAULT_TEXT_SIZE_M`."""
        self._cross_size_m = DEFAULT_CROSS_SIZE_M
        """One arm length for the model and joint crosses — see
        `DEFAULT_CROSS_SIZE_M`."""
        self._origin_cross_size_m = DEFAULT_ORIGIN_CROSS_SIZE_M
        self._origin_cross_visible = True
        """The origin cross has its own size and switch — see
        `DEFAULT_ORIGIN_CROSS_SIZE_M`."""
        self._scene_radius_m = 1.0
        """How big the loaded models are, refreshed with the origin cross. An axis
        marker is sized from this rather than from the whole render tree, which
        would include the markers themselves and feed back on itself."""
        self._collision_pairs: frozenset[tuple[str, str]] = frozenset()
        """What the last **explicit** check found. Not recomputed per frame: the
        check is a button now."""
        self._collision_matrices: dict[str, Any] = {}
        """Where each model stood when that check ran. Compared every frame so a
        result that no longer describes the scene is dropped rather than left
        showing red at a model that has since moved away."""
        self._outlines: dict[str, Any] = {}
        """Model id -> its outline group. One system for selection and collision:
        what differs is the colour and whether a cross comes with it."""
        self._highlight_colors: dict[str, Rgba] = {}
        """Model id -> its selection colour. Absent means `HIGHLIGHT_COLOR`."""
        self._joints: dict[str, ModelJoint] = {}
        self._joint_parent: dict[str, str | None] = {}
        """Joint id -> the joint carrying it, or `None` when it sits in the
        scene. Joints, not models, are what form the hierarchy."""
        self._joint_values: dict[str, float] = {}
        self._joint_bases: dict[str, Any] = {}
        """Joint id -> the `NodePath` at `joint.origin`. Never moves."""
        self._joint_moves: dict[str, Any] = {}
        """Joint id -> the `NodePath` carrying the live value. Everything riding
        the joint hangs off this one."""
        self._joint_markers: dict[str, Any] = {}
        """Joint id -> its marker, a child of the base — it shows where the
        joint is, which does not change when its value does."""
        self._joint_labels: dict[str, Any] = {}
        """Joint id -> its name label, beside the marker on the base."""
        self._joint_colors: dict[str, Rgba] = {}
        """Joint id -> its colour override. Absent means the default marker
        colour; the marker is rebuilt to change it, since a `LineSegs` colour is
        baked in when the geometry is created."""
        self._show_names: dict[str, bool] = {}
        """Joint id -> whether its own name label is wanted."""
        self._names_visible = True
        """The scene-wide switch. A label is drawn when this **and** the joint's
        own flag are on, so turning them all off does not lose which individual
        ones the user had already silenced."""
        self._joint_tangents: dict[str, Any] = {}
        """Joint id -> the node turning local `+Z` onto the joint's direction."""
        self._joint_frames: dict[str, Any] = {}
        """Joint id -> its **initial coordinate system**: the tangential frame
        plus `joint.alignment`. What a bound model aligns to."""
        self._anchors: dict[str, Anchor] = {}
        """Model id -> the point and direction it couples to a joint by."""
        self._anchor_nodes: dict[str, Any] = {}
        """Model id -> the node seating its anchor on its joint. Exists only
        while the model is bound."""
        self._bound_to: dict[str, str] = {}
        """Model id -> the joint it is bound to, if any."""
        self._joint_preview_root: Any = None
        """A throwaway marker for a joint still being edited in a dialog — not
        yet in `self._joints`, so it needs none of that bookkeeping."""
        self._controller = OrbitController(self._base)
        self._controller.enable()
        self._picker = PointPicker(self._base)
        self._picker.enable()
        self._refresh_floor()
        logger.info("embedded renderer ready", size=(width, height))

    # -- lifecycle ----------------------------------------------------------

    def step(self) -> None:
        """Draw one frame."""
        self._evaluate_sensors()
        self._drop_stale_collisions()
        self._base.taskMgr.step()

    def resize(self, width: int, height: int) -> None:
        """Follow the host widget to a new size.

        The origin is **deliberately left alone**: on a resize, Windows
        recalculates it for an embedded window against the screen rather than
        against the parent, and the window would wander outside the widget
        (measured: the origin jumped to -640).
        """
        from panda3d.core import WindowProperties

        if self._base.win is None:
            return
        properties = WindowProperties()
        properties.setOrigin(0, 0)
        properties.setSize(max(width, 1), max(height, 1))
        self._base.win.requestProperties(properties)

    def shutdown(self) -> None:
        """Release camera control. Idempotent."""
        self._controller.disable()
        self._picker.disable()

    # -- scene contents -----------------------------------------------------

    @property
    def controller(self) -> OrbitController:
        return self._controller

    def add_model(self, model_id: str, assembly: CadAssembly, cache_dir: Path) -> int:
        """Add a model to the scene under its own root and return the number of
        nodes whose mesh was missing from the cache.

        Adding does **not** move the camera. With several models loaded, jumping
        the view on every insert would fight the user; `fit_view()` is explicit.
        Only the very first model gets framed, so the window is not left staring
        at empty space.
        """
        self.remove_model(model_id)

        built = build_scene(assembly, cache_dir, name=model_id)
        built.root.reparentTo(self._base.render)
        # Lights hang on the model root, not on `render`: on `render` they would
        # accumulate with every model added and wash the picture out.
        setup_lights(built.root)
        self._models[model_id] = built.root
        local = aabb_of(built.root, built.root)
        if local is not None:
            self._model_local_bounds[model_id] = local
        self._model_part_bounds[model_id] = _part_bounds(built)
        self._placements.setdefault(model_id, IDENTITY_PLACEMENT)
        self._apply_placement(model_id)

        if len(self._models) == 1:
            self._controller.frame(built.root)
        self._refresh_axes()
        self._refresh_floor()

        logger.info(
            "model added",
            model=model_id,
            nodes=len(built.node_paths),
            triangles=assembly.triangle_count,
            missing_meshes=built.missing_meshes,
        )
        return built.missing_meshes

    def remove_model(self, model_id: str) -> bool:
        """Remove one model. Returns `True` if it was there.

        No cascade to joints any more: a model owns none. It is a leaf, so the
        only tidying needed is releasing it from whatever joint carried it —
        the joint itself, and anything else riding it, is untouched.
        """
        root = self._models.get(model_id)
        if root is None:
            return False

        self.bind_model(model_id, None)

        # A pick armed against this model would otherwise be left pointing at a
        # NodePath about to be destroyed.
        self._picker.cancel()
        self.clear_joint_preview()
        self.clear_collisions()

        del self._models[model_id]
        self._placements.pop(model_id, None)
        self._anchors.pop(model_id, None)
        self._show_axes.pop(model_id, None)
        self._model_local_bounds.pop(model_id, None)
        self._model_part_bounds.pop(model_id, None)

        if self._highlighted_id == model_id:
            self.set_highlight(None)
        root.removeNode()
        self._refresh_axes()
        self._refresh_floor()
        logger.info("model removed", model=model_id)
        return True

    def clear(self) -> None:
        """Remove every model, joint and sensor, the highlight and the axes. The
        floor stays — it is scene furniture, not modelled content — but its
        extent resets to the empty-scene default."""
        self._picker.cancel()
        self.clear_joint_preview()
        self.clear_collisions()
        for root in self._models.values():
            root.removeNode()
        self._models.clear()
        self._placements.clear()
        self._outlines.clear()
        self._show_axes.clear()
        self._model_local_bounds.clear()
        self._model_part_bounds.clear()
        self.set_highlight(None)
        self._remove_axes()
        self._refresh_floor()

        # Joint nodes now hang off `render`, not off a model root, so removing
        # the models does **not** destroy them — they have to go explicitly.
        # (Removing a base destroys its move node and marker with it.)
        for base in self._joint_bases.values():
            base.removeNode()
        self._joints.clear()
        self._joint_parent.clear()
        self._joint_values.clear()
        self._joint_bases.clear()
        self._joint_moves.clear()
        self._joint_markers.clear()
        self._joint_labels.clear()
        self._joint_colors.clear()
        self._show_names.clear()
        self._joint_tangents.clear()
        self._joint_frames.clear()
        self._joint_highlight_root = None
        self._joint_highlighted_id = None
        self._anchors.clear()
        self._anchor_nodes.clear()
        self._bound_to.clear()

        for root in self._sensor_roots.values():
            root.removeNode()
        self._sensors.clear()
        self._sensor_active.clear()
        self._sensor_mounts.clear()
        self._sensor_readings.clear()
        self._sensor_roots.clear()

    # -- camera -------------------------------------------------------------

    def set_view(self, name: str) -> None:
        """Switch to a standard view (`front`, `top`, …).

        Zoom and point of interest stay; only the angle changes.
        """
        self._controller.set_camera(self._controller.camera.with_view(name))
        logger.info("view switched", view=name)

    @property
    def camera_state(self) -> OrbitCamera:
        """The current orbit camera, for saving into a project."""
        return self._controller.camera

    def set_camera_state(self, camera: OrbitCamera) -> None:
        """Restore a saved camera."""
        self._controller.set_camera(camera)

    def fit_view(self, model_id: str | None = None) -> None:
        """Frame one model, or everything when `model_id` is `None`."""
        if model_id is not None:
            root = self._models.get(model_id)
            if root is not None:
                self._controller.frame(root)
            return

        if self._models:
            self._controller.frame(self._base.render)

    # -- placement ----------------------------------------------------------

    def placement(self, model_id: str) -> Transform:
        """Where a model sits relative to the scene origin."""
        return self._placements.get(model_id, IDENTITY_PLACEMENT)

    def set_placement(self, model_id: str, placement: Transform) -> None:
        """Move and rotate one model.

        The origin cross does **not** move — it is the reference the models are
        placed against. The camera stays too; `fit_view()` re-aims it.
        """
        self._placements[model_id] = placement
        self._apply_placement(model_id)
        self._refresh_axes()
        self._refresh_floor()

    def _apply_placement(self, model_id: str) -> None:
        """Push a placement onto a model root.

        Rotation happens about the **model origin**, not its centre of mass —
        that is what people expect from "rotate 90° about Z".
        """
        from panda3d.core import LQuaternion

        root = self._models.get(model_id)
        if root is None:
            return
        placement = self._placements.get(model_id, IDENTITY_PLACEMENT)
        root.setPos(*placement.xyz)
        root.setQuat(LQuaternion(*rpy_to_quat(placement.rpy)))

    # -- selection ----------------------------------------------------------

    def set_highlight(self, model_id: str | None) -> None:
        """Mark one model as selected, or clear the selection with `None`."""
        self._highlighted_id = model_id
        self._refresh_outlines()

    def set_highlight_color(self, model_id: str, color: Rgba | None) -> None:
        """Set the colour this model is outlined in when selected, or go back to
        the default with `None`."""
        if color is None:
            self._highlight_colors.pop(model_id, None)
        else:
            self._highlight_colors[model_id] = color
        self._refresh_outlines()

    def _refresh_outlines(self) -> None:
        """Rebuild every model outline from scratch.

        **One system for selection and collision.** A model gets a wireframe box
        when it is selected or when it is colliding, and the two states differ
        only in colour and thickness: red and thick when it collides, its own
        highlight colour otherwise. That is what makes a collision read as "these
        two" — both models are outlined, whether or not either is selected.

        A box rather than tinting the model: models carry their own colours from
        the STEP file, so tinting would be invisible on some and misleading on
        others. The coordinate cross rides along on the **selected** model only,
        colliding or not — it answers where that model's zero is, which has
        nothing to do with whether it is touching something.

        Everything hangs off one throwaway group per model, so cleanup is one
        `removeNode` however many decorations get added here.
        """
        for outline in self._outlines.values():
            outline.removeNode()
        self._outlines.clear()

        colliding = colliding_ids(self._collision_pairs)
        for model_id, root in self._models.items():
            is_selected = model_id == self._highlighted_id
            if not is_selected and model_id not in colliding:
                continue

            group = root.attachNewNode(f"outline-{model_id}")
            self._outlines[model_id] = group

            local = self._model_local_bounds.get(model_id)
            if local is not None:
                color, thickness = self._outline_style(model_id, colliding)
                make_box_outline(local.low, local.high, color, thickness).reparentTo(group)

            if is_selected and self._show_axes.get(model_id, True):
                # One size for every cross in the scene, the model's own radius
                # included: three sources gave three different sizes, which is the
                # whole complaint. Labels off — this marks an origin, and X/Y/Z
                # glyphs on every selected part is clutter.
                cross = make_axes_node(self._cross_size_m, self._text_size_m, with_labels=False)
                cross.reparentTo(group)

    def _outline_style(self, model_id: str, colliding: frozenset[str]) -> tuple[Rgba, float]:
        """The colour and thickness for one model's outline. Collision wins over
        selection: a warning must not be hidden by a colour the user chose."""
        if model_id in colliding:
            return (COLLISION_COLOR, COLLISION_THICKNESS_PX)
        return (
            self._highlight_colors.get(model_id, HIGHLIGHT_COLOR),
            HIGHLIGHT_THICKNESS_PX,
        )

    def set_joint_highlight(self, joint_id: str | None) -> None:
        """Mark the selected joint's **initial coordinate system** with a
        labelled cross, or clear it with `None`.

        Attached to the frame node rather than the base, so what it shows is
        exactly the orientation a bound model aligns to — the clearest answer to
        "where will the model end up". Labels on here: unlike a model's origin
        marker, the orientation is the whole point.
        """
        if self._joint_highlight_root is not None:
            self._joint_highlight_root.removeNode()
            self._joint_highlight_root = None
        self._joint_highlighted_id = None

        if joint_id is None:
            return
        frame = self._joint_frames.get(joint_id)
        joint = self._joints.get(joint_id)
        if frame is None or joint is None:
            return
        if not self._show_axes.get(joint_id, True):
            # Still recorded as highlighted: turning the cross back on has to
            # redraw it without the user having to reselect the joint.
            self._joint_highlighted_id = joint_id
            return

        cross = make_axes_node(self._cross_size_m, self._text_size_m, with_labels=True)
        cross.reparentTo(frame)
        self._joint_highlight_root = cross
        self._joint_highlighted_id = joint_id

    def set_model_visible(self, model_id: str, is_visible: bool) -> bool:
        """Show or hide one model. Returns `True` if it was there.

        Hiding takes its outlines with it — both the selection box and any
        collision warning hang off the model root. That is deliberate: a marker
        floating where an invisible model is would be worse than nothing.
        """
        root = self._models.get(model_id)
        if root is None:
            return False
        if is_visible:
            root.show()
        else:
            root.hide()
        return True

    def set_axes_visible(self, item_id: str, show_axes: bool) -> None:
        """Turn the selection cross for one model or joint on or off.

        Takes effect immediately by re-running whichever highlight is currently
        showing, so the toggle does not wait for the next selection change. Only
        the matching one is redrawn — toggling a model must not disturb the
        cross on the selected joint.
        """
        self._show_axes[item_id] = show_axes
        if self._highlighted_id == item_id:
            self.set_highlight(item_id)
        if self._joint_highlighted_id == item_id:
            self.set_joint_highlight(item_id)

    # -- axes ---------------------------------------------------------------

    def _refresh_axes(self) -> None:
        """Redraw the origin cross sized to everything currently loaded.

        Recomputed on every change because the cross scales with the scene: a
        second, much larger model would otherwise leave it invisibly small.
        """
        self._remove_axes()
        if not self._models:
            return
        self._scene_radius_m = scene_radius(self._base.render)[1]
        if not self._origin_cross_visible:
            return
        node = make_axes_node(self._origin_cross_size_m, self._text_size_m)
        node.reparentTo(self._base.render)
        self._axes_root = node

    def _remove_axes(self) -> None:
        if self._axes_root is not None:
            self._axes_root.removeNode()
            self._axes_root = None

    # -- floor ----------------------------------------------------------------

    @property
    def floor_visible(self) -> bool:
        return self._floor_state.visible

    @property
    def floor_z_m(self) -> float:
        return self._floor_state.z_m

    def set_floor_visible(self, visible: bool) -> None:
        """Show or hide the floor without disturbing its height."""
        self._floor_state = replace(self._floor_state, visible=visible)
        self._refresh_floor()

    def set_floor_z(self, z_m: float) -> None:
        """Move the floor without disturbing its visibility."""
        self._floor_state = replace(self._floor_state, z_m=z_m)
        self._refresh_floor()

    def _refresh_floor(self) -> None:
        """Rebuild the grid sized to everything currently loaded, and apply its
        height and visibility.

        Unlike the axes cross, the floor does **not** disappear on an empty
        scene — it is set-dressing, not a per-model aid, and `scene_radius`
        already has a sensible fallback for that case.
        """
        if self._floor_root is not None:
            self._floor_root.removeNode()
        extent = floor_half_extent_for(scene_radius(self._base.render)[1])
        self._floor_root = make_floor_node(extent)
        self._floor_root.reparentTo(self._base.render)
        self._floor_root.setZ(self._floor_state.z_m)
        if self._floor_state.visible:
            self._floor_root.show()
        else:
            self._floor_root.hide()

    # -- sensors --------------------------------------------------------------

    def add_sensor(self, sensor_id: str, sensor: Sensor, mounted_on: str | None = None) -> None:
        """Add a sensor and draw its marker, starting clear — it is re-evaluated
        on the very next frame, so a stale colour never lingers.

        `mounted_on` is a model id or a joint id. The marker is parented under
        that node, which is what makes the sensor's point and direction local to
        it: a sensor on a carriage moves with the carriage for free, rather than
        needing its coordinates rewritten every time the carriage moves.
        """
        self.remove_sensor(sensor_id)
        self._sensors[sensor_id] = sensor
        self._sensor_active[sensor_id] = False
        if mounted_on is not None:
            self._sensor_mounts[sensor_id] = mounted_on
        node = make_sensor_marker(sensor, is_active=False)
        node.reparentTo(self._mount_node(sensor_id))
        self._sensor_roots[sensor_id] = node

    def _mount_node(self, sensor_id: str) -> Any:
        """The node a sensor hangs off: its mount, or the scene root.

        A joint's **move** node rather than its base, so a sensor on a rotating
        axis turns with it — the base deliberately never moves (see `add_joint`).
        A mount that has since been removed falls back to the scene root, which
        leaves the sensor visible where it was rather than making it disappear.
        """
        mount = self._sensor_mounts.get(sensor_id)
        if mount is None:
            return self._base.render
        node = self._models.get(mount) or self._joint_moves.get(mount)
        if node is None:
            logger.warning("sensor mount not found", sensor=sensor_id, mount=mount)
            return self._base.render
        return node

    def set_sensor_mount(self, sensor_id: str, mounted_on: str | None) -> None:
        """Move a sensor onto another model or joint, or off onto the scene."""
        sensor = self._sensors.get(sensor_id)
        if sensor is None:
            return
        if mounted_on is None:
            self._sensor_mounts.pop(sensor_id, None)
        else:
            self._sensor_mounts[sensor_id] = mounted_on
        root = self._sensor_roots.get(sensor_id)
        if root is not None:
            root.reparentTo(self._mount_node(sensor_id))

    def sensor_reading(self, sensor_id: str) -> SensorReading | None:
        """What the sensor last read, or `None` if it has not been evaluated."""
        return self._sensor_readings.get(sensor_id)

    def remove_sensor(self, sensor_id: str) -> bool:
        """Remove one sensor. Returns `True` if it was there."""
        root = self._sensor_roots.pop(sensor_id, None)
        self._sensors.pop(sensor_id, None)
        self._sensor_active.pop(sensor_id, None)
        self._sensor_mounts.pop(sensor_id, None)
        self._sensor_readings.pop(sensor_id, None)
        if root is None:
            return False
        root.removeNode()
        return True

    def update_sensor(self, sensor_id: str, sensor: Sensor) -> None:
        """Replace a sensor's geometry after an edit.

        Keeps whatever active state it already had (redrawn in the matching
        colour) rather than flashing clear for one frame until the next
        evaluation.
        """
        was_active = self._sensor_active.get(sensor_id, False)
        self.add_sensor(sensor_id, sensor)
        self.set_sensor_active(sensor_id, was_active)

    def set_sensor_active(self, sensor_id: str, is_active: bool) -> None:
        """Show a sensor as clear or active. No-op when the state has not
        changed — the only place that decides whether a marker gets rebuilt."""
        if self._sensor_active.get(sensor_id) == is_active:
            return
        sensor = self._sensors.get(sensor_id)
        root = self._sensor_roots.get(sensor_id)
        if sensor is None or root is None:
            return

        self._sensor_active[sensor_id] = is_active
        root.removeNode()
        node = make_sensor_marker(sensor, is_active)
        node.reparentTo(self._base.render)
        self._sensor_roots[sensor_id] = node

    def _sensor_targets(self, sensor_id: str) -> tuple[AABB, ...]:
        """The part boxes a sensor can see, in **its own** frame.

        Per-part rather than one box per model, and the same boxes the collision
        check uses: one notion of where the parts are, so a sensor cannot
        disagree with the warning outline about what is touching what.

        **The model a sensor is mounted on is excluded.** A sensor bolted to a
        bracket would otherwise read blocked by its own bracket forever. A
        genuine self-occlusion is a mounting error, not something worth
        simulating.

        The boxes are converted into the mount's frame because the sensor's own
        point and direction live there. Converting the geometry once per sensor
        is cheaper than the alternative — transforming the sensor into world
        space would need its direction rotated, and the boxes re-boxed anyway.
        """
        from panda3d.core import LPoint3

        mount = self._sensor_mounts.get(sensor_id)
        node = self._mount_node(sensor_id)
        into_mount = self._base.render.getMat(node)

        boxes: list[AABB] = []
        for model_id in self._models:
            if model_id == mount:
                continue
            for world in self._world_part_boxes(model_id):
                corners = box_corners(world.low, world.high)
                local = aabb_around(
                    tuple(into_mount.xformPoint(LPoint3(*corner))) for corner in corners
                )
                if local is not None:
                    boxes.append(local)
        return tuple(boxes)

    def _mount_angle(self, sensor_id: str) -> float:
        """The value of the joint a sensor is mounted on, for an encoder.

        Zero for a sensor on a model or on nothing: an encoder needs an axis to
        read, and one bolted to something that does not turn reads no motion.
        """
        mount = self._sensor_mounts.get(sensor_id)
        if mount is None:
            return 0.0
        return self._joint_values.get(mount, 0.0)

    def _evaluate_sensors(self) -> None:
        """Re-read every sensor against the current scene, once per frame.

        Any model's placement can affect any sensor, so there is no cheap
        "which sensors care about this model" index worth building — recomputing
        everything every frame is simpler to wire than hooking every place a
        model can move, and no more expensive: the real cost, rebuilding a
        marker, is already gated by `set_sensor_active`'s no-op-on-no-change.

        An encoder skips the geometry entirely — it reads the angle of the joint
        it is bolted to, so gathering boxes for it would be work with no answer
        in it.
        """
        if not self._sensors:
            return
        for sensor_id, sensor in self._sensors.items():
            if sensor.kind in ENCODER_KINDS:
                reading = read_sensor(sensor, (), self._mount_angle(sensor_id))
            else:
                reading = read_sensor(sensor, self._sensor_targets(sensor_id))
            self._sensor_readings[sensor_id] = reading
            self.set_sensor_active(sensor_id, sensor_is_active_reading(sensor, reading))

    # -- collisions ---------------------------------------------------------

    def check_collisions(self) -> frozenset[tuple[str, str]]:
        """Check now which models touch, and outline whatever does. Returns the
        pairs found.

        Run when asked rather than on a timer: the answer is only interesting at
        a moment the user chose, and a check that runs by itself has to be cheap
        enough to run always, which caps how good it can be. On demand it can
        afford the per-part comparison — measured at ~30 ms for two 1052-part
        assemblies.

        **Visibility is deliberately not consulted.** Hiding a model is a visual
        choice, so a hidden model still collides. Its own outline goes invisible
        along with it — the outline is parented to the model root — while the
        model it hit still shows one, which is the honest picture.
        """
        bodies = {
            model_id: body
            for model_id in self._models
            if (body := self._world_body(model_id)) is not None
        }
        self._collision_pairs = colliding_pairs(bodies)
        self._collision_matrices = {
            model_id: root.getMat(self._base.render) for model_id, root in self._models.items()
        }
        self._refresh_outlines()
        logger.info("collisions checked", models=len(bodies), pairs=len(self._collision_pairs))
        return self._collision_pairs

    def _drop_stale_collisions(self) -> None:
        """Forget the last result as soon as any model has moved.

        Compared against the matrices recorded at check time rather than flagged
        by every method that can move something: a flag has to be remembered in
        every such place, and the one that gets forgotten leaves a red outline on
        a model sitting somewhere it no longer collides. A matrix walk per model
        per frame is cheap enough that correctness is free here.
        """
        if not self._collision_matrices:
            return

        for model_id, matrix in self._collision_matrices.items():
            root = self._models.get(model_id)
            if root is None or root.getMat(self._base.render) != matrix:
                self.clear_collisions()
                return
        if len(self._models) != len(self._collision_matrices):
            self.clear_collisions()

    def clear_collisions(self) -> None:
        """Drop the last result and its outlines."""
        if not self._collision_pairs and not self._collision_matrices:
            return
        self._collision_pairs = frozenset()
        self._collision_matrices.clear()
        self._refresh_outlines()

    @property
    def collisions(self) -> frozenset[tuple[str, str]]:
        """Which pairs of models the last check found, each pair ordered by id."""
        return self._collision_pairs

    def _world_body(self, model_id: str) -> Body | None:
        """The model as the collision test wants it: its overall world box plus
        each part's world box.

        The parts are what make the answer useful, and they are why this runs on
        a period rather than every frame — 1052 parts is ~14 ms of corner
        transforms per model, which is nothing once a second and too much sixty
        times a second.
        """
        box = self._world_box(model_id)
        if box is None:
            return None
        return Body(box=box, parts=self._world_part_boxes(model_id))

    def _world_part_boxes(self, model_id: str) -> tuple[AABB, ...]:
        """Each part's box in world coordinates, from the local ones measured at
        `add_model` and the model's current world matrix."""
        locals_ = self._model_part_bounds.get(model_id)
        root = self._models.get(model_id)
        if not locals_ or root is None:
            return ()

        matrix = root.getMat(self._base.render)
        boxes = (self._transform_box(matrix, local) for local in locals_)
        return tuple(box for box in boxes if box is not None)

    @staticmethod
    def _transform_box(matrix: Any, local: AABB) -> AABB | None:
        from panda3d.core import LPoint3

        corners = box_corners(local.low, local.high)
        return aabb_around(tuple(matrix.xformPoint(LPoint3(*corner))) for corner in corners)

    def _world_box(self, model_id: str) -> AABB | None:
        """The model's bounding box in world coordinates.

        Built from the box measured once at `add_model`, pushed through the
        model's current world matrix — **not** by asking Panda3D for world
        bounds. `getTightBounds(render)` walks the whole subtree, and on a real
        STEP assembly that is not a micro-optimisation: measured at ~154 ms per
        model per frame for a 1052-node assembly, which took the window from
        29 fps to 0.5 and looked exactly like "nothing is being drawn".

        Reading the matrix instead is eight point transforms. It needs no dirty
        flag and no knowledge of where a model can move from: the matrix already
        accounts for the placement, for the joint chain a bound model hangs off,
        and for a re-parenting.
        """
        local = self._model_local_bounds.get(model_id)
        root = self._models.get(model_id)
        if local is None or root is None:
            return None

        return self._transform_box(root.getMat(self._base.render), local)

    # -- joints -----------------------------------------------------------------

    def add_joint(
        self, joint_id: str, joint: ModelJoint, parent_joint_id: str | None = None
    ) -> None:
        """Add a joint to the scene, optionally carried by another joint.

        Two `NodePath`s per joint, because they answer two different questions:
        the **base** sits at `joint.origin` and never moves, and the **move**
        node underneath carries the live value. Anything riding this joint —
        bound models, child joints — hangs off the move node and is therefore
        carried by it for free.

        The base deliberately holds **no rotation**. An earlier design aligned
        it with the joint's direction, but then a child joint inherited that
        rotation and its typed coordinates stopped meaning what they looked
        like (on a rail along +X, a child origin of `(0,0,1)` moved it along
        X). The motion is expressed along/about the real direction instead.

        Below the move node, on the **model branch only**, sit two more: a
        *tangent* node turning local `+Z` onto the joint's direction, and the
        *frame* node holding `joint.alignment` — together the initial
        coordinate system a bound model aligns to. Child joints hang off the
        move node instead, so they keep the intuitive axis-aligned coordinates
        the paragraph above is about.
        """
        parent = self._joint_moves.get(parent_joint_id) if parent_joint_id else None
        if parent_joint_id is not None and parent is None:
            logger.warning("parent joint not found", joint=joint_id, parent=parent_joint_id)
            return

        self.remove_joint(joint_id)
        self._joints[joint_id] = joint
        self._joint_parent[joint_id] = parent_joint_id

        anchor_root = parent if parent is not None else self._base.render
        base = anchor_root.attachNewNode(f"joint-base-{joint.name}")
        base.setPos(*joint.origin)
        self._joint_bases[joint_id] = base
        move = base.attachNewNode(f"joint-move-{joint.name}")
        self._joint_moves[joint_id] = move
        self._build_joint_frame(joint_id, joint)

        self._rebuild_joint_marker(joint_id, joint)

        low, high = effective_limits(joint)
        self.set_joint_value(joint_id, min(max(0.0, low), high))

    def _rebuild_joint_marker(self, joint_id: str, joint: ModelJoint) -> None:
        """(Re)build a joint's marker and its name label on the base node.

        Both hang off the **base**, so they show where the joint *is* and stay
        put while the value moves whatever rides the joint.

        One helper because `add_joint`, `update_joint`, a colour change and a
        name toggle all need exactly this: a `LineSegs` colour and a `TextNode`
        string are fixed when the geometry is created, so changing either means
        building it again.
        """
        base = self._joint_bases.get(joint_id)
        if base is None:
            return

        for held in (self._joint_markers, self._joint_labels):
            existing = held.pop(joint_id, None)
            if existing is not None:
                existing.removeNode()

        color = self._joint_colors.get(joint_id, JOINT_MARKER_COLOR)
        marker = make_joint_marker(
            _marker_joint_at_origin(joint), axis_length_for(self._scene_radius_m), color
        )
        marker.reparentTo(base)
        self._joint_markers[joint_id] = marker

        if not (self._names_visible and self._show_names.get(joint_id, True)):
            return
        label = make_joint_label(joint.name, self._text_size_m, color)
        label.reparentTo(base)
        self._joint_labels[joint_id] = label

    def set_text_size(self, size_m: float) -> None:
        """Set the height of every piece of 3D text in the scene."""
        if size_m <= 0.0 or size_m == self._text_size_m:
            return
        self._text_size_m = size_m
        self._refresh_sizes()

    def set_cross_size(self, size_m: float) -> None:
        """Set the arm length of the model and joint crosses."""
        if size_m <= 0.0 or size_m == self._cross_size_m:
            return
        self._cross_size_m = size_m
        self._refresh_sizes()

    def set_origin_cross_size(self, size_m: float) -> None:
        """Set the arm length of the origin cross alone."""
        if size_m <= 0.0 or size_m == self._origin_cross_size_m:
            return
        self._origin_cross_size_m = size_m
        self._refresh_axes()

    def set_origin_cross_visible(self, visible: bool) -> None:
        """Show or hide the origin cross, leaving every other cross alone."""
        if visible == self._origin_cross_visible:
            return
        self._origin_cross_visible = visible
        self._refresh_axes()

    @property
    def origin_cross_size_m(self) -> float:
        return self._origin_cross_size_m

    @property
    def origin_cross_visible(self) -> bool:
        return self._origin_cross_visible

    @property
    def text_size_m(self) -> float:
        return self._text_size_m

    @property
    def cross_size_m(self) -> float:
        return self._cross_size_m

    def _refresh_sizes(self) -> None:
        """Rebuild everything a size change can affect.

        Crosses and text are baked into their geometry when it is created — a
        `LineSegs` length and a `TextNode` scale are both fixed at build time —
        so changing a size means building them again. All four places are listed
        here rather than each setter knowing which ones it touches: the two
        sizes overlap (a cross carries text), and splitting them would be two
        lists to keep in step.
        """
        self._refresh_axes()
        self._refresh_outlines()
        for joint_id, joint in self._joints.items():
            self._rebuild_joint_marker(joint_id, joint)
        if self._joint_highlighted_id is not None:
            self.set_joint_highlight(self._joint_highlighted_id)

    def set_joint_color(self, joint_id: str, color: Rgba | None) -> None:
        """Recolour one joint's marker and label, or return it to the default
        with `None`."""
        if color is None:
            self._joint_colors.pop(joint_id, None)
        else:
            self._joint_colors[joint_id] = color
        joint = self._joints.get(joint_id)
        if joint is not None:
            self._rebuild_joint_marker(joint_id, joint)

    def set_joint_name_visible(self, joint_id: str, show_name: bool) -> None:
        """Show or hide one joint's name label."""
        self._show_names[joint_id] = show_name
        joint = self._joints.get(joint_id)
        if joint is not None:
            self._rebuild_joint_marker(joint_id, joint)

    def set_names_visible(self, show_names: bool) -> None:
        """The scene-wide name switch. Each joint's own flag still applies, so
        turning everything off and on again does not lose the individual ones
        that were already silenced."""
        if self._names_visible == show_names:
            return
        self._names_visible = show_names
        for joint_id, joint in self._joints.items():
            self._rebuild_joint_marker(joint_id, joint)

    @property
    def names_visible(self) -> bool:
        return self._names_visible

    def set_model_color(self, model_id: str, color: Rgba | None) -> bool:
        """Override a model's colour, or restore the CAD colours with `None`.

        `setColor` on the root wins over the per-part colours `viz.scene` sets
        from the STEP file, and `clearColor` hands them back — which is why the
        override is stored as "absent, or a colour" rather than as a colour with
        a default: there is nowhere else to recover the CAD colours from.
        """
        root = self._models.get(model_id)
        if root is None:
            return False
        if color is None:
            root.clearColor()
        else:
            root.setColor(*color)
        return True

    def _build_joint_frame(self, joint_id: str, joint: ModelJoint) -> None:
        """(Re)build the tangent + alignment pair that forms a joint's initial
        coordinate system. Split into two nodes so neither needs any transform
        composition: the tangent is a pure rotation from `rotation_onto`, and the
        frame is `setPos`/`setQuat` from a `Transform`, exactly as
        `_apply_placement` does for a model.
        """
        from panda3d.core import LQuaternion

        move = self._joint_moves.get(joint_id)
        if move is None:
            return

        # Anything bound to this joint hangs off the frame about to be
        # destroyed, and `removeNode()` on an ancestor **orphans** a descendant
        # rather than erroring — the model's `NodePath` handle stays valid but
        # leaves the scene, so it would silently vanish. Measured; hence the
        # rescue: park the models on the scene root, then let `_apply_anchor`
        # re-seat them on the new frame.
        rescued = [model_id for model_id, bound in self._bound_to.items() if bound == joint_id]
        for model_id in rescued:
            model = self._models.get(model_id)
            if model is not None:
                model.reparentTo(self._base.render)
            stale_anchor = self._anchor_nodes.pop(model_id, None)
            if stale_anchor is not None:
                stale_anchor.removeNode()

        for stale in (
            self._joint_tangents.pop(joint_id, None),
            self._joint_frames.pop(joint_id, None),
        ):
            if stale is not None:
                stale.removeNode()

        tangent = move.attachNewNode(f"joint-tangent-{joint.name}")
        axis, angle = rotation_onto((0.0, 0.0, 1.0), direction_of(joint))
        tangent.setQuat(LQuaternion(*axis_angle_to_quat(axis, angle)))
        self._joint_tangents[joint_id] = tangent

        frame = tangent.attachNewNode(f"joint-frame-{joint.name}")
        frame.setPos(*joint.alignment.xyz)
        frame.setQuat(LQuaternion(*rpy_to_quat(joint.alignment.rpy)))
        self._joint_frames[joint_id] = frame

        for model_id in rescued:
            self._apply_anchor(model_id)
        if self._joint_highlighted_id == joint_id:
            # The cross hung off the frame just replaced.
            self.set_joint_highlight(joint_id)

    def joint_frame(self, joint_id: str) -> Any:
        """The joint's initial-coordinate-system node, or `None`. What a bound
        model hangs off and what the selection cross is attached to."""
        return self._joint_frames.get(joint_id)

    def set_joint_parent(self, joint_id: str, parent_joint_id: str | None) -> None:
        """Re-hang a joint under another joint, or under the scene root.

        Does not check for cycles — that needs the joint registry, so the caller
        checks first (see `ui.joint_registry.would_cycle`).
        """
        base = self._joint_bases.get(joint_id)
        if base is None:
            return
        parent = self._joint_moves.get(parent_joint_id) if parent_joint_id else None
        if parent_joint_id is not None and parent is None:
            return

        base.reparentTo(parent if parent is not None else self._base.render)
        self._joint_parent[joint_id] = parent_joint_id
        self._refresh_axes()
        self._refresh_floor()

    def remove_joint(self, joint_id: str) -> bool:
        """Remove one joint. Returns `True` if it was there.

        Everything riding it is rescued rather than destroyed with it: bound
        models are unbound back to the scene root, and child joints are
        re-hung on *this* joint's own parent, so removing a rail keeps the head
        that sat on it instead of silently deleting a whole subtree.
        """
        if joint_id not in self._joints:
            return False

        for model_id, bound_joint_id in list(self._bound_to.items()):
            if bound_joint_id == joint_id:
                self.bind_model(model_id, None)

        grandparent = self._joint_parent.get(joint_id)
        for child_id, parent_id in list(self._joint_parent.items()):
            if parent_id == joint_id:
                self.set_joint_parent(child_id, grandparent)

        self._joint_bases.pop(joint_id).removeNode()
        self._joint_moves.pop(joint_id, None)
        self._joint_markers.pop(joint_id, None)
        self._joint_tangents.pop(joint_id, None)
        self._joint_frames.pop(joint_id, None)
        self._show_axes.pop(joint_id, None)
        self._joint_labels.pop(joint_id, None)
        self._joint_colors.pop(joint_id, None)
        self._show_names.pop(joint_id, None)
        if self._joint_highlighted_id == joint_id:
            # The cross hung off the frame just destroyed.
            self._joint_highlight_root = None
            self._joint_highlighted_id = None
        self._joint_values.pop(joint_id, None)
        self._joint_parent.pop(joint_id, None)
        self._joints.pop(joint_id, None)
        return True

    def update_joint(self, joint_id: str, joint: ModelJoint) -> None:
        """Replace a joint's geometry after an edit, keeping its current value
        (reclamped against the edited joint's own limits)."""
        base = self._joint_bases.get(joint_id)
        if base is None:
            return
        value = self._joint_values.get(joint_id, 0.0)

        self._joints[joint_id] = joint
        base.setPos(*joint.origin)
        # The direction and the alignment may both have changed, so the
        # tangential frame and the ICS on top of it are rebuilt from scratch.
        self._build_joint_frame(joint_id, joint)

        self._rebuild_joint_marker(joint_id, joint)

        self.set_joint_value(joint_id, value)
        # The direction may have changed, so every anchor seated on this joint
        # has to be turned again.
        for model_id, bound_joint_id in self._bound_to.items():
            if bound_joint_id == joint_id:
                self._apply_anchor(model_id)
        self._refresh_axes()
        self._refresh_floor()

    def set_joint_value(self, joint_id: str, value: float) -> None:
        """Push a live value onto a joint's move node.

        No no-op-on-no-change guard the way `set_sensor_active` has — a value
        actively driven from a dialog is expected to change on nearly every
        call, so the guard would rarely trigger and is not worth the state.
        """
        joint = self._joints.get(joint_id)
        move = self._joint_moves.get(joint_id)
        if joint is None or move is None:
            return

        from panda3d.core import LQuaternion

        self._joint_values[joint_id] = value
        pose = joint_value_pose(joint, value)
        move.setPos(*pose.translation)
        move.setQuat(LQuaternion(*axis_angle_to_quat(pose.rotation_axis, pose.rotation_angle_rad)))

    # -- binding a model onto a joint --------------------------------------------

    def anchor(self, model_id: str) -> Anchor:
        """The model's contact point and direction. The default sits at its own
        origin pointing +Z."""
        return self._anchors.get(model_id, Anchor())

    def set_anchor(self, model_id: str, anchor: Anchor) -> None:
        """Set which point of the model couples to a joint, and which way it
        faces. Takes effect immediately if the model is already bound."""
        self._anchors[model_id] = anchor
        if model_id in self._bound_to:
            self._apply_anchor(model_id)
            self._refresh_axes()
            self._refresh_floor()

    def bind_model(self, model_id: str, joint_id: str | None) -> None:
        """Bind a model onto a joint so the joint's motion carries it, or
        release it back to the scene root with `None`.

        The model's own placement is re-applied afterward and is **not**
        consumed by this: binding only changes what the placement is relative
        to. Between the joint and the model sits one more node holding the
        anchor, which is what lets `_apply_placement` stay exactly as it is —
        no transform composition in `viz` at all.
        """
        model = self._models.get(model_id)
        if model is None:
            return

        if joint_id is None:
            model.reparentTo(self._base.render)
            self._bound_to.pop(model_id, None)
            node = self._anchor_nodes.pop(model_id, None)
            if node is not None:
                node.removeNode()
        else:
            move = self._joint_moves.get(joint_id)
            if move is None:
                return
            self._bound_to[model_id] = joint_id
            self._apply_anchor(model_id)

        self._apply_placement(model_id)
        self._refresh_axes()
        self._refresh_floor()

    def _apply_anchor(self, model_id: str) -> None:
        """(Re)build the node that seats a bound model's anchor on its joint."""
        from panda3d.core import LQuaternion

        joint_id = self._bound_to.get(model_id)
        model = self._models.get(model_id)
        if joint_id is None or model is None:
            return
        joint = self._joints.get(joint_id)
        move = self._joint_moves.get(joint_id)
        if joint is None or move is None:
            return

        frame = self._joint_frames.get(joint_id)
        if frame is None:
            return

        node = self._anchor_nodes.get(model_id)
        if node is None:
            node = frame.attachNewNode(f"anchor-{model_id}")
            self._anchor_nodes[model_id] = node
        elif node.getParent() != frame:
            node.reparentTo(frame)

        # Onto the frame's own `+Z`: the tangent node above already points that
        # way, so the anchor lines up with the frame rather than re-deriving the
        # joint's direction.
        pose = anchor_pose(self.anchor(model_id))
        node.setPos(*pose.translation)
        node.setQuat(LQuaternion(*axis_angle_to_quat(pose.rotation_axis, pose.rotation_angle_rad)))
        model.reparentTo(node)

    def preview_joint(self, joint: ModelJoint, parent_joint_id: str | None = None) -> None:
        """Show a temporary marker for a joint still being edited in a dialog,
        before it exists in the registry. Replaces any earlier preview."""
        self.clear_joint_preview()
        parent = self._joint_moves.get(parent_joint_id) if parent_joint_id else None
        marker = make_joint_marker(joint)
        marker.reparentTo(parent if parent is not None else self._base.render)
        self._joint_preview_root = marker

    def clear_joint_preview(self) -> None:
        """Remove the temporary preview marker, if any. Safe to call when there
        is none — the dialog calls this unconditionally on close."""
        if self._joint_preview_root is not None:
            self._joint_preview_root.removeNode()
            self._joint_preview_root = None

    # -- picking ------------------------------------------------------------

    def begin_pick_in_joint_frame(
        self,
        model_id: str,
        parent_joint_id: str | None,
        on_point_picked: Callable[[Vec3], None],
    ) -> None:
        """Arm picking on one model, reporting the point in a **joint's** frame.

        A pick lands in the clicked model's own coordinates, but a joint stores
        its geometry relative to whatever carries it (the scene, for a top-level
        one). Converting here keeps that frame maths where the scene graph is —
        `ui/` only ever sees numbers already in the frame it will store them in.
        """
        model = self._models.get(model_id)
        if model is None:
            logger.warning("model not found, picking not armed", model=model_id)
            return
        reference = self._joint_moves.get(parent_joint_id) if parent_joint_id else None
        if reference is None:
            reference = self._base.render

        def report_in_frame(point: Vec3) -> None:
            from panda3d.core import Point3

            converted = reference.getRelativePoint(model, Point3(*point))
            on_point_picked((converted[0], converted[1], converted[2]))

        self._picker.begin(model, report_in_frame)

    def begin_pick(self, model_id: str, on_point_picked: Callable[[Vec3], None]) -> None:
        """Arm picking for one model. The next plain click on it resolves a
        point in the model's own local frame — see `viz.picking.PointPicker`."""
        root = self._models.get(model_id)
        if root is None:
            logger.warning("model not found, picking not armed", model=model_id)
            return
        self._picker.begin(root, on_point_picked)

    def cancel_pick(self) -> None:
        """Disarm picking without resolving a point."""
        self._picker.cancel()
