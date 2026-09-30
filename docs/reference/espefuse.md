# espefuse

## `espefuse`

Run [`espefuse`](https://docs.espressif.com/projects/esptool/en/latest/esp32/espefuse/)
using `idftool`'s [device selection](../guide/choosing-a-device.md).

```bash
idftool espefuse summary
idftool -m 9c:13:9e:1b:d4:6c espefuse summary --format json
```

Everything after `espefuse` goes to `espefuse`. A `-p` given there is used
as-is, without the picker. `-b` and `--no-reset`, if given, are passed on as
`--baud` and `--after no-reset`.

`-y` only skips the picker. `espefuse` still asks before burning; pass it
`--do-not-confirm` to skip that.
