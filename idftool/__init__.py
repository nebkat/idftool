"""idftool — a library and CLI for flashing and provisioning ESP-IDF devices.

Like esptool, the operations are exposed as plain functions that each take a
``State`` (which owns the serial connection and global options) plus the same
arguments as the corresponding CLI command. Import them to drive idftool from
your own code, reusing a single connection across operations::

    from idftool import State, write_image, factory, ota

The names are resolved lazily from the module that defines them on first
access, so importing this package has no side effects — and running
``python -m idftool`` does not import the command modules twice.
"""
import importlib
from typing import TYPE_CHECKING

#: Public name → the module that defines it. Doubles as a map of where each
#: command's logic lives; the CLI wrappers sit alongside it in the same module.
_SOURCES = {
    'idftool.state': ('State', 'Loaded', 'get_esp'),
    'idftool.cli': ('pass_state',),
    'idftool.flash': ('WRITE_FLASH_OPTIONS',),
    'idftool.commands.misc': ('list_devices', 'monitor', 'idf_py', 'run_esptool',
                              'run_espefuse', 'enter_bootloader'),
    'idftool.commands.partition_io': ('read_partition', 'write_partitions', 'erase_partition',
                                      'view_partition'),
    'idftool.commands.images': ('create_image', 'dump_image', 'write_image', 'print_image'),
    'idftool.commands.bundles': ('create_bundle', 'dump_bundle', 'write_bundle', 'print_bundle'),
    'idftool.commands.table': ('print_table', 'create_table', 'dump_table', 'write_table'),
    'idftool.commands.nvs': ('create_nvs', 'write_nvs', 'print_nvs', 'extract_nvs',
                             'read_nvs', 'get_nvs', 'set_nvs'),
    'idftool.commands.fs': ('create_fs', 'write_fs', 'read_fs', 'extract_fs', 'print_fs'),
    'idftool.commands.firmware': ('factory', 'ota', 'get_boot', 'set_boot', 'clear_boot'),
}

_EXPORTS = {name: module for module, names in _SOURCES.items() for name in names}

__all__ = sorted(_EXPORTS)

# What a type checker sees in place of the lazy lookup below, which it can't follow.
if TYPE_CHECKING:
    from idftool.state import State as State
    from idftool.state import Loaded as Loaded
    from idftool.state import get_esp as get_esp
    from idftool.cli import pass_state as pass_state
    from idftool.flash import WRITE_FLASH_OPTIONS as WRITE_FLASH_OPTIONS
    from idftool.commands.misc import list_devices as list_devices
    from idftool.commands.misc import monitor as monitor
    from idftool.commands.misc import idf_py as idf_py
    from idftool.commands.misc import run_esptool as run_esptool
    from idftool.commands.misc import run_espefuse as run_espefuse
    from idftool.commands.misc import enter_bootloader as enter_bootloader
    from idftool.commands.partition_io import read_partition as read_partition
    from idftool.commands.partition_io import write_partitions as write_partitions
    from idftool.commands.partition_io import erase_partition as erase_partition
    from idftool.commands.partition_io import view_partition as view_partition
    from idftool.commands.images import create_image as create_image
    from idftool.commands.images import dump_image as dump_image
    from idftool.commands.images import write_image as write_image
    from idftool.commands.images import print_image as print_image
    from idftool.commands.bundles import create_bundle as create_bundle
    from idftool.commands.bundles import dump_bundle as dump_bundle
    from idftool.commands.bundles import write_bundle as write_bundle
    from idftool.commands.bundles import print_bundle as print_bundle
    from idftool.commands.table import print_table as print_table
    from idftool.commands.table import create_table as create_table
    from idftool.commands.table import dump_table as dump_table
    from idftool.commands.table import write_table as write_table
    from idftool.commands.nvs import create_nvs as create_nvs
    from idftool.commands.nvs import write_nvs as write_nvs
    from idftool.commands.nvs import print_nvs as print_nvs
    from idftool.commands.nvs import extract_nvs as extract_nvs
    from idftool.commands.nvs import read_nvs as read_nvs
    from idftool.commands.nvs import get_nvs as get_nvs
    from idftool.commands.nvs import set_nvs as set_nvs
    from idftool.commands.fs import create_fs as create_fs
    from idftool.commands.fs import write_fs as write_fs
    from idftool.commands.fs import read_fs as read_fs
    from idftool.commands.fs import extract_fs as extract_fs
    from idftool.commands.fs import print_fs as print_fs
    from idftool.commands.firmware import factory as factory
    from idftool.commands.firmware import ota as ota
    from idftool.commands.firmware import get_boot as get_boot
    from idftool.commands.firmware import set_boot as set_boot
    from idftool.commands.firmware import clear_boot as clear_boot
else:
    def __getattr__(name: str) -> object:
        # PEP 562 lazy re-export: import the defining module only when the name is first
        # accessed. Importing them eagerly here would make `python -m idftool` import the
        # command modules twice (runpy warning), so defer it.
        module = _EXPORTS.get(name)
        if module is not None:
            return getattr(importlib.import_module(module), name)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
