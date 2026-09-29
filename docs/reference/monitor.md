# Monitor

## `monitor`

Run [`esp-idf-monitor`](https://github.com/espressif/esp-idf-monitor) on the
device picked by `-p`, `-m`, or the [picker](../guide/choosing-a-device.md).

```bash
idftool monitor
idftool -m 9c:13:9e:1b:d4:6c monitor build/app.elf
idftool --no-reset monitor
```

Everything after `monitor` goes to `esp-idf-monitor`; `idftool monitor -h` shows
its options. A `-p` given there is used as-is, without the picker.

`--no-reset` is passed through, so combined with the picker (which doesn't
reset boards) you can attach to a running device without rebooting it.

`idftool`'s `-b` is the flashing baud rate and isn't passed on. Give the
monitor's own after `monitor`: `idftool monitor -b 460800`.

Quit with ++ctrl+bracket-right++.
