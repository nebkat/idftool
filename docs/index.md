# idftool

A CLI built on [esptool](https://github.com/espressif/esptool) that understands
the ESP-IDF partition table.

esptool flashes arbitrary addresses. It knows nothing of partitions or OTA, so
simple jobs like flashing a new app, switching OTA slots, or pulling logs off a
device mean working out offsets by hand, for every partition table you deal
with. idftool does that for you.

<div class="grid cards" markdown>

-   :material-usb: **Device selection**

    ---

    Pick a board from a list of connected devices, or address it by MAC
    address. The list comes from USB, so no board is reset.

    [:octicons-arrow-right-24: Choosing a device](guide/choosing-a-device.md)

-   :material-swap-horizontal: **Firmware flashing**

    ---

    Flash factory or the next OTA slot and switch to it, skipping what's
    already there. See and change the boot slot without touching otadata by
    hand.

    [:octicons-arrow-right-24: Firmware](commands/firmware.md)

-   :material-tag-text-outline: **Partition-name addressing**

    ---

    Read, write, erase, or hex-dump a partition by name (`nvs`, `ota_0`,
    `storage`), or a slice of one with `name[start:stop]`.

    [:octicons-arrow-right-24: Partition addressing](guide/partition-addressing.md)

-   :material-shield-check-outline: **Safety checks**

    ---

    Writes can't overflow their partition, app binaries are checked against
    the chip, and a file passed as a partition is caught before connecting.

    [:octicons-arrow-right-24: Partitions or files](guide/partitions-or-files.md)

-   :material-package-variant-closed: **Bundles and images**

    ---

    Pack several partitions into one ZIP and flash them in one go, or dump
    and restore a whole flash image.

    [:octicons-arrow-right-24: Bundles](commands/bundles.md) ·
    [Images](commands/images.md)

-   :material-folder-key-outline: **Filesystems and NVS**

    ---

    Build FAT, littlefs, or SPIFFS images from a directory, or pull them off
    the device. Generate NVS from CSV, or change a key on a live device.

    [:octicons-arrow-right-24: Filesystems](commands/filesystems.md) ·
    [NVS](commands/nvs.md)

</div>

## At a glance

```console
$ idftool devices
PORT                         TYPE                 SERIAL             USB ID     LOCATION
/dev/cu.usbmodem11401        ESP USB-Serial/JTAG  7C:2C:67:92:79:C0  303A:1001  1-1.4
/dev/cu.PL2303G-USBtoUART10  PL2303GT             DPEDf103Y23        067B:23C3  0-1

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

Without `-p`, idftool asks which device to use. See
[Choosing a device](guide/choosing-a-device.md).
