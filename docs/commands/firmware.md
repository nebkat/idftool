# Firmware

App images are checked against the connected chip before they're written, so a
build for the wrong target is refused rather than flashed.

## `factory`

Write an app to the factory partition and erase otadata, so the bootloader
falls back to factory on the next boot. Without a factory partition, the app
goes to `ota_0` instead.

```bash
idftool factory build/my-app.bin
```

`--skip-flashed` and `--diff` are on by default, so flashing the same build
again skips the write. See [Write options](../guide/write-options.md).

## `ota`

Write an app to the **next** OTA slot and make it the boot slot. idftool reads
otadata to find the next slot, writes the image, and bumps the OTA sequence
number: what an OTA update from the firmware would do, over USB.

```bash
idftool ota build/my-app.bin
```

`--skip-flashed` and `--diff` are on by default here too, so flashing the same
build again only switches the slot.

## `app-info`

Print the app description of a bare application binary, without a device:
project name, version, IDF version, compile time, ELF SHA-256, and target
chip. The same block [`print-image`](images.md#print-image) and
[`print-bundle`](bundles.md#print-bundle) print for each app partition.
Alias: `print-app`.

```bash
idftool app-info -f build/my-app.bin
```
