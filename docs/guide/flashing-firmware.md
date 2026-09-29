# Flashing firmware

After `idf.py build`, the app binary is `build/<project>.bin`. How you flash it
depends on the partition table.

## Factory or OTA?

Run `idftool list` and look at the app partitions:

- **`factory` only:** use [`idftool factory`](#flash-the-factory-app).
- **`ota_0`, `ota_1`:** use [`idftool ota`](#push-an-ota-update) for day-to-day
  flashing. It behaves like a real OTA update, so you're testing the same path
  your users get.
- **`factory` and OTA slots:** `factory` for the base image your devices ship
  with, `ota` for updates on top of it.

## Flash the factory app

```bash
idftool factory build/my-app.bin
```

This writes the app to the factory partition and erases otadata, so the
bootloader boots factory next, whatever OTA slot was running before. Without a
factory partition, it writes `ota_0`.

## Push an OTA update

```bash
idftool ota build/my-app.bin
```

This writes the app to the slot that isn't running, then makes that slot boot
next:

```text
Writing 'my-app v1.2.0' to partition 'ota_1'...
Setting boot partition to 'ota_1'...
```

The previous build stays in the other slot, so you can switch back to it.

## Reflashing is fast

Both commands compare the binary with what's already in flash and write only
the sectors that differ, or nothing if it's already there. Flashing a small
change to a large app takes seconds.

To write everything regardless, add `--no-skip-flashed --no-diff`. See
[Write options](../reference/write-options.md).

## Switch between builds

See which slot boots next:

```console
$ idftool get-boot
OTA slot 'ota_1' (seq=2, state=VALID)
```

Go back to the other one:

```bash
idftool set-boot ota_0
```

Or erase otadata and let the bootloader fall back to factory (or `ota_0`):

```bash
idftool clear-boot
```

None of these touch the apps themselves.

## Check a binary before flashing

```bash
idftool app-info -f build/my-app.bin
```

This prints the project name, version, IDF version, build time, and target chip.
`idftool` refuses to flash a binary built for a different chip; `--force`
overrides that.

## Flash more than the app

To write other partitions alongside the app, name each one:

```bash
idftool write ota_0 build/my-app.bin storage build/storage.bin
```

To flash a whole set of partitions from someone else's build, use a
[bundle](backups-and-releases.md#ship-a-release).
