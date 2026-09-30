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

Only the sectors that differ are written, and nothing at all if the partition
already holds this build. otadata is erased either way. See
[Write options](write-options.md).

## `ota`

Write an app to the **next** OTA slot and make it the boot slot. `idftool` reads
otadata to find the next slot, writes the image, and bumps the OTA sequence
number: what an OTA update from the firmware would do, over USB.

```bash
idftool ota build/my-app.bin
```

Only the sectors that differ are written, and nothing at all if the slot
already holds this build. The boot slot is switched either way.

## Boot selection

These read and write otadata, the partition the bootloader consults to pick an
[OTA](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/system/ota.html) slot. The app images themselves are never touched.

### `get-boot`

Show which OTA slot the bootloader will run on the next reset, with its
sequence number and OTA state.

```console
$ idftool get-boot
OTA slot 'ota_1' (seq=2, state=VALID)
```

### `set-boot`

Make the next boot run a specific OTA partition.

```bash
idftool set-boot ota_1
```

### `clear-boot`

Erase otadata. The bootloader then falls back to the factory partition if
there is one, and to `ota_0` otherwise.

```bash
idftool clear-boot
```

## `app-info`

Print the app description of a bare application binary, without a device:
project name, version, IDF version, compile time, ELF SHA-256, and target
chip. The same block [`print-image`](images.md#print-image) and
[`print-bundle`](bundles.md#print-bundle) print for each app partition.
Given a bootloader, it prints which chip it's for.
Alias: `print-app`.

```console
$ idftool app-info -f build/my-app.bin
App: build/my-app.bin (0x28090 bytes)
Project name:     my-app
Version:          1.2.0
IDF version:      v6.0.2
Secure version:   0
Compiled:         Sep 29 2026 14:30:00
ELF SHA256:       4fd8f31259d82483f0fc34d4422b1aefb6ead9da845395d5ea96a135ed32c78a
Chip:             ESP32S3 (rev 0 to 99)
```
