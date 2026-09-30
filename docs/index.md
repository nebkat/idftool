# `idftool`

The ultimate CLI tool for interacting with Espressif devices. Built on
[`esptool`](https://docs.espressif.com/projects/esptool/en/latest/esp32/) and aware of ESP-IDF
partition tables.

<div class="cta-row" markdown>

<div class="cta-box" markdown>

**Install `idftool` with [pipx](https://pipx.pypa.io)**

```bash
pipx install idftool
```

[Installation :material-arrow-right:](installation.md){ .md-button .md-button--primary }

</div>

<div class="cta-box" markdown>

**Or use it in the browser**

[esp-web-toolkit](https://github.com/nebkat/esp-web-toolkit) is the
browser-based equivalent: the same tools with a UI, nothing to install.

[Open esp-web-toolkit :material-open-in-new:](https://nebkat.github.io/esp-web-toolkit/){ .md-button }

</div>

</div>

## Why

`esptool` flashes at arbitrary addresses. It knows nothing of partitions or OTA,
so simple jobs like flashing a new app, switching OTA slots, or pulling logs off
a device mean working out offsets by hand, for every partition table you deal
with. `idftool` does that for you.

<div class="grid cards" markdown>

-   :material-usb: **Device selection**

    ---

    Pick your board from a list of connected devices, or name it by MAC
    address. No more guessing which device is on which port.

    [:octicons-arrow-right-24: Choosing a device](guide/choosing-a-device.md)

-   :material-swap-horizontal: **Firmware flashing**

    ---

    Flash to the default (factory) or next available (OTA) app partition,
    with no offsets to memorise.

    [:octicons-arrow-right-24: Flashing firmware](guide/flashing-firmware.md)

-   :material-tag-text-outline: **Partition-name addressing**

    ---

    Read, write, erase, or hex-dump a partition by name (`nvs`, `ota_0`,
    `storage`), or a slice of one with `name[start:stop]`.

    [:octicons-arrow-right-24: Partition addressing](guide/partition-addressing.md)

-   :material-shield-check-outline: **Safety checks**

    ---

    Everything is checked before flash is touched: data has to fit its
    partition, apps have to be valid images built for the connected chip, and
    a file name given where a partition name belongs is caught straight away.

    [:octicons-arrow-right-24: Partitions or files](guide/partitions-or-files.md)

-   :material-package-variant-closed: **Bundles and images**

    ---

    Pack several partitions into one ZIP and flash them in one go, or dump
    and restore a whole flash image.

    [:octicons-arrow-right-24: Backups and releases](guide/backups-and-releases.md)

-   :material-folder-key-outline: **Filesystems and NVS**

    ---

    Flash a directory as a FAT, littlefs, or SPIFFS image (the type comes
    from the partition table), or pull one off the device. Generate NVS from CSV, or change a key on a live device.

    [:octicons-arrow-right-24: Provisioning devices](guide/provisioning.md)

</div>

## At a glance

Run any command without `-p`, and `idftool` asks which device to use:

```console
$ idftool monitor
? Select device (↑↓ move · ↵ select · p probe · r refresh · k kill · q quit)
 » /dev/cu.usbmodem11401         ESP USB-Serial/JTAG   7c:2c:67:92:79:c0
   /dev/cu.usbmodem2101          ESP USB-Serial/JTAG   b8:f8:62:49:ea:14
   /dev/cu.PL2303G-USBtoUART10   PL2303GT
   ✎ Enter a port manually…
   ↻ Refresh
   ✕ Quit
```

```console
$ idftool devices
╭─────────────────────────────┬─────────────────────┬───────────────────┬───────────┬──────────╮
│ Port                        │ Type                │ USB serial #      │ USB ID    │ Location │
├─────────────────────────────┼─────────────────────┼───────────────────┼───────────┼──────────┤
│ /dev/cu.usbmodem11401       │ ESP USB-Serial/JTAG │ 7C:2C:67:92:79:C0 │ 303A:1001 │ 1-1.4    │
│ /dev/cu.PL2303G-USBtoUART10 │ PL2303GT            │ DPEDf103Y23       │ 067B:23C3 │ 0-1      │
╰─────────────────────────────┴─────────────────────┴───────────────────┴───────────┴──────────╯

$ idftool list
╭──────────┬──────┬─────────┬──────────┬──────┬────────────────────────╮
│ Name     │ Type │ Subtype │   Offset │ Size │ App                    │
├──────────┼──────┼─────────┼──────────┼──────┼────────────────────────┤
│ nvs      │ data │ nvs     │   0x9000 │  16K │                        │
│ otadata  │ data │ ota (A) │   0xd000 │   8K │                        │
│ phy_init │ data │ phy     │   0xf000 │   4K │                        │
│ ota_0    │ app  │ ota_0   │  0x10000 │   1M │ my-app v1.0.0          │
│ ota_1    │ app  │ ota_1   │ 0x110000 │   1M │ my-app v1.1.0 ● active │
│ storage  │ data │ spiffs  │ 0x210000 │   1M │                        │
╰──────────┴──────┴─────────┴──────────┴──────┴────────────────────────╯

$ idftool ota build/my-app.bin
Writing 'my-app v1.2.0' to partition 'ota_0'...
Setting boot partition to 'ota_0'...

$ idftool write nvs my-nvs.bin
Writing file my-nvs.bin (size=0x4000) to partition nvs (offset=0x9000, size=0x4000)

$ idftool set-boot ota_1
Setting boot partition to 'ota_1'...
```

Without `-p`, `idftool` asks which device to use. See
[Choosing a device](guide/choosing-a-device.md).
