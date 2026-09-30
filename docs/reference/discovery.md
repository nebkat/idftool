# Discovery

## `devices`

List the serial ports the host can see, without opening any of them. Alias:
`ports`.

```console
$ idftool devices
╭─────────────────────────────┬─────────────────────┬───────────────────┬───────────┬──────────╮
│ Port                        │ Type                │ USB serial #      │ USB ID    │ Location │
├─────────────────────────────┼─────────────────────┼───────────────────┼───────────┼──────────┤
│ /dev/cu.usbmodem11401       │ ESP USB-Serial/JTAG │ 7C:2C:67:92:79:C0 │ 303A:1001 │ 1-1.4    │
│ /dev/cu.usbmodem2101        │ ESP USB-Serial/JTAG │ B8:F8:62:49:EA:14 │ 303A:1001 │ 2-1      │
│ /dev/cu.PL2303G-USBtoUART10 │ PL2303GT            │ DPEDf103Y23       │ 067B:23C3 │ 0-1      │
╰─────────────────────────────┴─────────────────────┴───────────────────┴───────────┴──────────╯
```

Type
:   What's on the other end of the USB cable, from its USB ID. The chip's
    built-in USB (`ESP USB-Serial/JTAG`) shows in cyan, USB-serial adapter
    chips (CP210x, CH340, CH9102, FTDI, PL2303) in yellow.

USB serial #
:   The USB serial number. On ESP USB-Serial/JTAG ports it's the chip's MAC,
    which [`-m`](../guide/choosing-a-device.md#by-mac-address) matches against.
    On an adapter it identifies the adapter, if it has one at all.

Location
:   The physical USB path: bus, then hub ports. On macOS this is what the
    `/dev/cu.usbmodem*` name is built from.

Piped, it prints plain space-separated columns instead of a box, for `awk` and
friends.

### `--probe`

Connect to every port to add each board's chip and MAC, including boards behind
a USB-serial adapter. This resets the boards. With a
[plugin](../guide/plugins.md) installed, a Device column shows what it names
each board.

```console
$ idftool devices --probe
Probing 2 port(s)…
╭─────────────────────────────┬─────────────┬───────────────────┬─────────────────────┬───────────────────┬───────────┬──────────╮
│ Port                        │ Chip        │ MAC               │ Type                │ USB serial #      │ USB ID    │ Location │
├─────────────────────────────┼─────────────┼───────────────────┼─────────────────────┼───────────────────┼───────────┼──────────┤
│ /dev/cu.usbmodem11401       │ ESP32-S3    │ 7c:2c:67:92:79:c0 │ ESP USB-Serial/JTAG │ 7C:2C:67:92:79:C0 │ 303A:1001 │ 1-1.4    │
│ /dev/cu.PL2303G-USBtoUART10 │ unavailable │                   │ PL2303GT            │ DPEDf103Y23       │ 067B:23C3 │ 0-1      │
╰─────────────────────────────┴─────────────┴───────────────────┴─────────────────────┴───────────────────┴───────────┴──────────╯

/dev/cu.PL2303G-USBtoUART10: no response (not an ESP, or not in download mode)
```
