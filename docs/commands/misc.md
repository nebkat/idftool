# Misc

## `monitor`

Run [esp-idf-monitor](https://github.com/espressif/esp-idf-monitor) on the
device picked by `-p`, `-m`, or the [picker](../guide/choosing-a-device.md).

```bash
idftool monitor
idftool -m 9c:13:9e:1b:d4:6c monitor build/app.elf
idftool --no-reset monitor
```

Everything after `monitor` goes to esp-idf-monitor; `idftool monitor -h` shows
its options. A `-p` given there is used as-is, without the picker.

`--no-reset` is passed through, so combined with the picker (which doesn't
reset boards) you can attach to a running device without rebooting it.

idftool's `-b` is the flashing baud rate and isn't passed on. Give the
monitor's own after `monitor`: `idftool monitor -b 460800`.

Quit with ++ctrl+bracket-right++.

## `enter-bootloader`

Wait for a port to appear, reset the chip into the ROM bootloader (download
mode), and exit, leaving it parked for another tool. The port is polled every
50 ms, and errors from a device node that isn't ready yet are retried.

```bash
idftool -p /dev/cu.usbmodem1101 enter-bootloader
idftool -m 9c:13:9e:1b:d4:6c enter-bootloader
```

Needs `-p` or `-m`. With `-m`, it waits for a port whose USB serial number is
that MAC, so it only works for boards on their built-in USB-Serial/JTAG port.
