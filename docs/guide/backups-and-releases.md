# Backups and releases

`idftool` has two ways to capture a device as a file:

| | Bundle | Image |
|---|---|---|
| **What** | A ZIP with one file per partition, plus the partition table | One file holding all of flash |
| **Good for** | Releases, and backups you might want to pick apart | Exact copies, and production programmers |
| **Commands** | [Bundles](../reference/bundles.md) | [Images](../reference/images.md) |

## Back up a device

Copy every partition, and the partition table, into a bundle:

```bash
idftool dump-bundle
```

Without a filename it's named after the chip, MAC, and time, e.g.
`esp32-s3-7c2c679279c0-20260929-143000.zip`. You can open it like any ZIP and
take out a single partition.

For a byte-for-byte copy including the bootloader:

```bash
idftool dump-image
```

This reads the whole chip, which can take a long time on a large flash.

## Restore a device

```bash
idftool write-bundle esp32-s3-7c2c679279c0-20260929-143000.zip
```

This writes every partition, the bootloader and the partition table from the
bundle, skipping what's already there.

From an image:

```bash
idftool write-image esp32-s3-7c2c679279c0-20260929-143000.img
```

!!! warning
    `write-image` erases the whole chip first.

## Ship a release

Give testers or production a single file instead of a list of binaries and
offsets. Build the bundle from your project's outputs:

```bash
idftool --partition-table-file partitions.csv create-bundle \
  -o release.zip --flash-partition-table \
  ota_0 build/my-app.bin storage build/storage.bin
```

This runs without a device, so it fits in CI. Check what went in:

```bash
idftool print-bundle -f release.zip
```

Whoever receives it flashes it with one command:

```bash
idftool write-bundle release.zip
```

`--flash-partition-table` includes the partition table, so a device with an
older layout is updated to match.

For a field update, name the app `@ota` instead of a partition, so it goes to
whichever OTA slot is next. A [manifest](../reference/bundles.md#manifest) can
also set NVS keys, edit files, and control when the partition table may
change.

## Clone a device

Back up one board and restore the bundle onto another:

```bash
idftool -m 7c:2c:67:92:79:c0 dump-bundle golden.zip
idftool -m 9c:13:9e:1b:d4:6c write-bundle golden.zip
```

The copy includes NVS, so per-device settings like serial numbers come along
too. Fix those afterwards with [`set-nvs`](provisioning.md#change-one-value).
