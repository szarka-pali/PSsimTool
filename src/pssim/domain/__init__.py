"""The domain layer — pure logic with no external dependencies.

This package imports **stdlib only**. No numpy, pydantic, panda3d, asyncua or OCP.
The reason is practical: kinematics and interpolation have to be testable without
opening a window and without a PLC. See CLAUDE.md and docs/architecture.md.
"""
