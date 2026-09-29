# Monitoring a device

`idftool monitor` opens [`esp-idf-monitor`](https://github.com/espressif/esp-idf-monitor),
the serial console `idf.py monitor` uses, on a device chosen the same way as
every other command.

```bash
idftool monitor
```

Pick the device, and its output starts scrolling. Quit with
++ctrl+bracket-right++; ++ctrl+t++ ++ctrl+h++ lists the monitor's other keys.

## Decode crashes

Pass the app's ELF file so backtraces and addresses are turned into function
names and line numbers:

```bash
idftool monitor build/my-app.elf
```

## Attach without rebooting

The monitor resets the chip when it opens. To look at a device that's already
running without disturbing it:

```bash
idftool --no-reset monitor
```

The device list doesn't reset boards either, so nothing reboots.

## Flash, then monitor

After picking a device, `idftool` prints the command to reach it again. Use the
`-m` form to flash and then watch the same board:

```bash
idftool -m 7c:2c:67:92:79:c0 ota build/my-app.bin
idftool -m 7c:2c:67:92:79:c0 monitor build/my-app.elf
```

## Other options

Everything after `monitor` goes to `esp-idf-monitor`, so its own options work:

```bash
idftool monitor -b 460800 --timestamps
idftool monitor -h
```

See the [`monitor` reference](../reference/monitor.md).
