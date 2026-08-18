"""Typed domain errors.

Every error the application raises deliberately inherits from `PSsimError`. That
makes it possible at the boundary (CLI, render loop) to tell "an expected error
with a usable message" from "a bug that should be reported".
"""


class PSsimError(Exception):
    """The base for every deliberate error of the application."""


class ConfigError(PSsimError):
    """An invalid machine definition or configuration.

    Raised at load time, never at run time. The message must say which file and
    which field is wrong.
    """


class CadImportError(PSsimError):
    """Importing a CAD file failed, or the file is unusable."""


class CacheError(PSsimError):
    """The cache is damaged, incomplete, or from an incompatible importer version."""


class DataSourceError(PSsimError):
    """The data source cannot be opened, or its configuration is invalid.

    Note: **losing the connection at run time is not an error** — it is a normal
    state the source handles by reconnecting. This error is for the cases where it
    cannot even start.
    """
