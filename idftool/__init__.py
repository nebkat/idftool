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

#: Public name → the module that defines it. Doubles as a map of where each
#: command's logic lives; the CLI wrappers sit alongside it in the same module.
_SOURCES = {
    'idftool.state': ('State', 'Loaded', 'get_esp'),
    'idftool.cli': ('pass_state',),
    'idftool.commands.misc': ('list_devices', 'enter_bootloader'),
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


def __getattr__(name):
    # PEP 562 lazy re-export: import the defining module only when the name is first
    # accessed. Importing them eagerly here would make `python -m idftool` import the
    # command modules twice (runpy warning), so defer it.
    module = _EXPORTS.get(name)
    if module is not None:
        return getattr(importlib.import_module(module), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))
