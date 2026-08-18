"""The command modules.

Importing this package imports every module below, and each one registers its commands
on the :data:`idftool.cli.cli` group as a side effect of being imported. ``__main__``
imports this package for exactly that reason; the order sets the order commands are
declared in, not the order they are shown (see ``COMMAND_GROUPS`` in :mod:`idftool.cli`).
"""
from idftool.commands import (  # noqa: F401  (imported for their registration side effect)
    misc,
    partition_io,
    images,
    bundles,
    table,
    nvs,
    fs,
    firmware,
)
