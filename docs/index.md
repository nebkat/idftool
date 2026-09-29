# idftool

A CLI built on [esptool](https://github.com/espressif/esptool) that understands
the ESP-IDF partition table.

esptool flashes arbitrary addresses. It knows nothing of partitions or OTA, so
simple jobs like flashing a new app, switching OTA slots, or pulling logs off a
device mean working out offsets by hand, for every partition table you deal
with. idftool does that for you.

<div class="grid cards" markdown>

-   :material-tag-text-outline: **Partition-name addressing**

    ---

    Read, write, erase, or hex-dump a partition by name (`nvs`, `ota_0`,
    `storage`), or a slice of one with `name[start:stop]`.

    [:octicons-arrow-right-24: Partition addressing](guide/partition-addressing.md)

-   :material-swap-horizontal: **OTA slot management**

    ---

    See the active slot, switch slots, or fall back to factory without
    computing otadata offsets.

    [:octicons-arrow-right-24: Boot selection](commands/boot.md)

-   :material-shield-check-outline: **Safety checks**

    ---

    Writes can't overflow their partition, and app binaries are checked
    against the connected chip before flashing.

    [:octicons-arrow-right-24: Firmware](commands/firmware.md)

-   :material-package-variant-closed: **Bundles and images**

    ---

    Pack several partitions into one ZIP and flash them in one go, or dump
    and restore a whole flash image.

    [:octicons-arrow-right-24: Bundles](commands/bundles.md)

-   :material-folder-outline: **Filesystems, both ways**

    ---

    Build FAT, littlefs, or SPIFFS images from a directory and flash them, or
    pull one off the device and extract it.

    [:octicons-arrow-right-24: Filesystems](commands/filesystems.md)

-   :material-key-outline: **NVS editing**

    ---

    Generate NVS images from CSV, read single keys into a shell, or change a
    key on a live device without rebuilding the partition.

    [:octicons-arrow-right-24: NVS](commands/nvs.md)

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
