# Getting started

This walks through connecting to a board and some common operations on it. It
assumes `idftool` is [installed](../installation.md) and you have an
ESP-IDF project that builds.

## 1. View your partition table

Plug the board in and run:

```bash
idftool list
```

With no `-p`, `idftool` lists the connected devices and asks which to use. Pick
yours with the arrow keys and ++enter++:

```text
? Select device (↑↓ move · ↵ select · p probe · r refresh · k kill · q quit)
 » /dev/cu.usbmodem11401   ESP USB-Serial/JTAG   7c:2c:67:92:79:c0
```

It then prints the partition table, with the app in each app partition and the
one that's running marked:

```text
╭──────────┬──────┬──────────┬──────────┬──────┬────────────────────────╮
│ Name     │ Type │ Subtype  │   Offset │ Size │ App                    │
├──────────┼──────┼──────────┼──────────┼──────┼────────────────────────┤
│ nvs      │ data │ nvs      │   0x9000 │  24K │                        │
│ otadata  │ data │ ota (A)  │   0xf000 │   8K │                        │
│ phy_init │ data │ phy      │  0x11000 │   4K │                        │
│ ota_0    │ app  │ ota_0    │  0x20000 │   1M │ my-app v1.0.0 ● active │
│ ota_1    │ app  │ ota_1    │ 0x120000 │   1M │ empty                  │
│ storage  │ data │ littlefs │ 0x220000 │   1M │                        │
╰──────────┴──────┴──────────┴──────────┴──────┴────────────────────────╯
```

Before the table, `idftool` prints how to skip the question next time:

```text
Re-run with: idftool -p /dev/cu.usbmodem11401 list
         or: idftool -m 7c:2c:67:92:79:c0 list
```

The `-m` form finds the board by MAC address, whichever USB socket it's in.

## 2. Flash a build

Build your project with `idf.py build`, then push the app to the next OTA slot:

```bash
idftool ota build/my-app.bin
```

`idftool` checks the binary was built for this chip, writes it to the slot that
isn't running, and makes that slot boot next. If your partition table has no
OTA slots, use `idftool factory` instead. See
[Flashing firmware](flashing-firmware.md).

## 3. Flash some data

To write any other partition, name the partition and the file to flash to it,
which must be small enough to fit. For example, if you had a partition called
`storage`:

```bash
idftool write storage build/storage.bin
```

To write several partitions at once, give more `PARTITION FILE` pairs:

```bash
idftool write storage build/storage.bin nvs build/nvs.bin
```

To build a filesystem image from a directory and flash it in one step, use
[`write-fs`](../reference/filesystems.md#write-fs):

```bash
idftool write-fs storage assets/
```

`write-fs` picks the filesystem (FAT, littlefs, or SPIFFS) from the partition's
subtype, so you don't need to say which.

## 4. Inspect your partitions

You can view the contents of NVS directly:

```console
$ idftool print-nvs nvs
Reading partition nvs (offset=0x9000, size=0x6000)
Partition 'nvs': NVS version 2, 0x6000 bytes
╭───────────┬─────────────┬────────┬──────────────╮
│ Namespace │ Key         │ Type   │ Value        │
├───────────┼─────────────┼────────┼──────────────┤
│ config    │ sample_rate │ u32    │ 100          │
│ config    │ serial      │ string │ SN-0042      │
│ config    │ wifi_ssid   │ string │ Workshop     │
│ phy       │ cal_mac     │ blob   │ 7c2c679279c0 │
│ phy       │ cal_version │ u32    │ 701          │
╰───────────┴─────────────┴────────┴──────────────╯
5 entries in 2 namespaces, 31 bytes of data
```

And list the files in a filesystem partition:

```console
$ idftool print-fs storage
Reading partition storage (offset=0x220000, size=0x100000)
Partition 'storage': littlefs, 0x100000 bytes
╭────────────────┬───────╮
│ Path           │  Size │
├────────────────┼───────┤
│ config.json    │   184 │
│ logs           │ <dir> │
│ logs/boot.log  │  2048 │
│ www            │ <dir> │
│ www/index.html │  1532 │
╰────────────────┴───────╯
3 files, 3764 bytes, 2 directories
```

Both print the partition table first, as above. `idftool` works out the
filesystem from the partition's subtype, so FAT, littlefs, and SPIFFS all work
the same way. To copy the files off the device, use
[`read-fs`](../reference/filesystems.md#read-fs).

## 5. Watch it run

```bash
idftool monitor build/my-app.elf
```

This opens [`esp-idf-monitor`](https://github.com/espressif/esp-idf-monitor) on the board, with the ELF file so crashes are
decoded. Quit with ++ctrl+bracket-right++. See
[Monitoring a device](monitoring.md).

## Next steps

- [Choosing a device](choosing-a-device.md): working with several boards at once.
- [Provisioning devices](provisioning.md): per-device settings and files.
- [Backups and releases](backups-and-releases.md): copying a device, or shipping
  a build to others.
