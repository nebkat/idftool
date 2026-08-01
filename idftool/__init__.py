"""idftool — a library and CLI for flashing and provisioning ESP-IDF devices.

Like esptool, the operations are exposed as plain functions that each take a
``State`` (which owns the serial connection and global options) plus the same
arguments as the corresponding CLI command. Import them to drive idftool from
your own code, reusing a single connection across operations::

    from idftool import State, write_image, factory, ota

The names are resolved lazily from :mod:`idftool.__main__` on first access, so
importing this package has no side effects — and running ``python -m idftool``
does not import ``__main__`` twice.
"""

__all__ = [
    # Core types and connection helpers
    'State',
    'Loaded',
    'get_esp',
    'pass_state',
    # Discovery
    'list_devices',
    # Partition I/O
    'read_partition',
    'write_partitions',
    'erase_partition',
    'view_partition',
    # Firmware
    'ota',
    'factory',
    # Boot selection
    'get_boot',
    'set_boot',
    'clear_boot',
    # Images
    'create_image',
    'dump_image',
    'write_image',
    'print_image',
    # Bundles
    'create_bundle',
    'dump_bundle',
    'write_bundle',
    'print_bundle',
    # Partition table
    'print_table',
    'convert_table',
    'dump_table',
    'write_table',
    # NVS
    'create_nvs',
    'write_nvs',
    # Misc
    'enter_bootloader',
]

_EXPORTS = frozenset(__all__)


def __getattr__(name):
    # PEP 562 lazy re-export: pull the operation functions and core types out of
    # idftool.__main__ only when first accessed. Importing __main__ eagerly here
    # would make `python -m idftool` import it twice (runpy warning), so defer it.
    if name in _EXPORTS:
        import idftool.__main__ as _main
        return getattr(_main, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | _EXPORTS)
