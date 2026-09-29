# Python library

Like esptool, every command is also a plain function. Each takes a `State`,
which holds the serial connection and the global options, plus the same
arguments as the CLI command. Reuse one `State` to run several operations over
a single connection.

```python
from idftool import State, ota, get_boot, set_nvs

state = State(
    port="/dev/cu.usbmodem1101", baud=115200, no_reset=False,
    partition_table_file=None, partition_table_offset=0x8000, partition_table_size=0x1000,
    primary_bootloader_offset=None, recovery_bootloader_offset=None,
)

ota(state, "build/app.bin")
get_boot(state)

state.esp.hard_reset()  # the CLI does this after each command; here it's up to you
```

`State` also takes `mac=` and `probe=`, which behave like
[`-m` and `--probe`](guide/choosing-a-device.md).

Names are imported lazily, so `import idftool` has no side effects.

## Write options

The [write options](guide/write-options.md) are keyword arguments, with the
flag's name in snake case:

```python
from idftool import write_image, write_nvs

write_image(state, "flash.img", erase=False, diff=True)
write_nvs(state, "nvs", "provision.csv", no_progress=True)
```

esptool options that take data rather than a yes/no are available here only:
`diff_with`, `no_diff_verify`, `encrypt_files`, `erase_all`.

An unknown option raises `TypeError` rather than being ignored. esptool reads
its options with `kwargs.get`, so a misspelled one would otherwise be a write
that quietly did something else.

## Choosing a device

`idftool.ports.select_device()` shows the [picker](guide/choosing-a-device.md)
and returns the chosen device as a dict with `port`, `chip`, `mac`, and
`adapter`. It returns `None` when there's no terminal to ask on.

Pass `identify` to name devices your own way. It's called with a connected
`ESPLoader` for each port, and whatever it returns is shown in place of the
chip name:

```python
from idftool.ports import select_device

def identify(esp):
    return read_board_name(esp)  # e.g. from a read-only NVS partition

found = select_device(115200, identify=identify, message="Select a controller")
state.port = found["port"]
```

`identify` needs a connection, so every port is probed (and reset) when it's
given. If it raises, the device is still listed and usable, with the error
next to it.

## Available functions

| Group | Functions |
|--------|-----------|
| Discovery | `list_devices`, `monitor`, `enter_bootloader` |
| Partition I/O | `read_partition`, `write_partitions`, `erase_partition`, `view_partition` |
| Firmware | `factory`, `ota`, `get_boot`, `set_boot`, `clear_boot` |
| Images | `create_image`, `dump_image`, `write_image`, `print_image` |
| Bundles | `create_bundle`, `dump_bundle`, `write_bundle`, `print_bundle` |
| Partition table | `print_table`, `create_table`, `dump_table`, `write_table` |
| NVS | `create_nvs`, `write_nvs`, `print_nvs`, `extract_nvs`, `read_nvs`, `get_nvs`, `set_nvs` |
| Filesystems | `create_fs`, `write_fs`, `read_fs`, `extract_fs`, `print_fs` |
