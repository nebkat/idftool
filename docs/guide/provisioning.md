# Provisioning devices

Firmware is the same on every unit; settings and files often aren't. This page
covers putting per-device data on a board and reading it back.

## Settings in NVS

ESP-IDF apps usually keep settings in an NVS partition. Describe them in a CSV,
in the format ESP-IDF's `nvs_partition_gen.py` uses. This one makes a namespace
`config` holding two settings, a string and a 32-bit number:

```csv title="example.csv"
key,type,encoding,value
config,namespace,,
wifi_ssid,data,string,Workshop
sample_rate,data,u32,100
```

The [NVS reference](../reference/nvs.md) explains the columns. Build an NVS
image from the CSV and flash it in one step:

```bash
idftool write-nvs nvs example.csv
```

This replaces the whole partition, so anything the device had saved there is
gone.

### Change one value

To change a key without touching the rest, edit it in place:

```bash
idftool set-nvs nvs config:wifi_ssid=Office
```

Add a key that doesn't exist yet by giving its type:

```bash
idftool set-nvs nvs config:serial:string=SN-0042
```

Set several keys from a CSV, keeping everything else:

```bash
idftool set-nvs nvs --csv config.csv
```

### Read values back

```console
$ idftool print-nvs nvs
╭───────────┬─────────────┬────────┬─────────╮
│ Namespace │ Key         │ Type   │ Value   │
├───────────┼─────────────┼────────┼─────────┤
│ config    │ sample_rate │ u32    │ 100     │
│ config    │ serial      │ string │ SN-0042 │
│ config    │ wifi_ssid   │ string │ Office  │
╰───────────┴─────────────┴────────┴─────────╯
3 entries in 1 namespace, 19 bytes of data

$ idftool get-nvs nvs config:serial
SN-0042
```

`get-nvs` prints only the value, so it can go straight into a script:

```bash
serial=$(idftool get-nvs nvs config:serial)
```

See the [NVS reference](../reference/nvs.md) for blobs, deleting keys, and
working on image files.

## Files in a filesystem partition

Web pages, certificates, and other assets usually live in a FAT, littlefs, or
SPIFFS partition. Flash a directory to it:

```bash
idftool write-fs storage assets/
```

`idftool` picks the filesystem (FAT, littlefs, or SPIFFS) from the partition's
subtype, and builds an image that fills the partition.

## Pull files off a device

Copy a filesystem partition into a local directory, for example to collect
logs:

```bash
idftool read-fs storage ./device-files
```

Or just list what's there:

```bash
idftool print-fs storage
```

See the [filesystems reference](../reference/filesystems.md) for matching the
device's sdkconfig settings.

## Many devices

Address each board by MAC address, so a script can't flash the wrong one:

```bash
idftool -m 7c:2c:67:92:79:c0 set-nvs nvs config:serial:string=SN-0042
idftool -m 9c:13:9e:1b:d4:6c set-nvs nvs config:serial:string=SN-0043
```

See [Scripting](scripting.md).
