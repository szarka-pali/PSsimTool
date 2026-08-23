"""The desktop shell in PySide6.

The topmost layer — it may import everything else, but nothing may import it. See
docs/architecture.md R3: the shell handles the window, the menus and the panels, while the
3D viewport itself is `viz/` embedded in a `QWidget`.

`PySide6` is an optional dependency (`uv sync --extra ui`), so it is imported inside
functions — neither `pssim --help` nor the unit tests need to drag it in.
"""
