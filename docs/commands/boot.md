# Boot selection

These read and write otadata, the partition the bootloader consults to pick an
OTA slot. The app images themselves are never touched.

## `get-boot`

Show which OTA slot the bootloader will run on the next reset, with its
sequence number and OTA state.

```console
$ idftool get-boot
OTA slot 'ota_1' (seq=2, state=VALID)
```

## `set-boot`

Make the next boot run a specific OTA partition.

```bash
idftool set-boot ota_1
```

## `clear-boot`

Erase otadata. The bootloader then falls back to the factory partition if
there is one, and to `ota_0` otherwise.

```bash
idftool clear-boot
```
