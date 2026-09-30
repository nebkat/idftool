# Plugins

A plugin names devices your own way, for example from your eFuse fields or a
config partition. It's an installed Python package that registers an
`identify` function:

```toml title="pyproject.toml"
[project.entry-points."idftool.identify"]
chip-description = "idftool_chip_description:identify"
```

```python title="idftool_chip_description.py"
def identify(esp):
    return esp.get_chip_description()
```

This is the full example in
[`examples/identify-plugin`](https://github.com/nebkat/idftool/tree/main/examples/identify-plugin).
Install it next to `idftool` and probe:

```console
$ pip install ./examples/identify-plugin
$ idftool devices --probe
Port                    Device                            Chip      MAC
/dev/cu.usbmodem11201   ESP32-S3 (QFN56) (revision v0.2)  ESP32-S3  90:e5:b1:cd:47:bc
```

## When it runs

`identify` is called with a connected `ESPLoader` whenever a board is probed:
`--probe`, or ++p++ in the [picker](choosing-a-device.md#the-picker). It never
runs otherwise, so installing a plugin resets no extra boards.

The loader is esptool's ROM loader. It reads eFuses and registers, but on most
chips not flash. For that, start the stub first:

```python
def identify(esp):
    esp = esp if esp.IS_STUB else esp.run_stub()
    data = esp.read_flash(0x9000, 0x1000)
    ...
```

## What it returns

A string naming the device, shown in place of the chip name. `None` means
"not one of mine": the next plugin gets a turn, then the chip name is shown.

Plugins run in order of entry point name, and the first one that isn't `None`
wins. A plugin that fails to import or raises is shown as the device's error,
unless another plugin names it.

## Standalone binaries

Plugins load from the Python environment `idftool` is installed in, so they
don't reach the standalone binaries. Install `idftool` with `pip` or `pipx`
(`pipx inject idftool ./my-plugin`) to use them.
