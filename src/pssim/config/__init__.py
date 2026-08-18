"""The configuration layer — the YAML machine definition schema and its translation into the domain model.

This is a system boundary: input is validated here, and here (and only here) units
from the PLC and from CAD are converted into internal metres and radians. Beyond
this boundary the domain assumes the data is valid.
"""
