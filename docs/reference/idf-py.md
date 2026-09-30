# idf.py

## `idf.py`

ESP-IDF's [`idf.py`](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/tools/idf-py.html)
with device selection: actions like `flash` and `monitor` ask which device to
use, while `build` or `menuconfig` run straight away.

```bash
idftool idf.py build flash monitor
idftool -m 9c:13:9e:1b:d4:6c idf.py app-flash
```

`-b`, if given, is passed on as `idf.py -b`, the flashing baud rate.

Alias it to run every `idf.py` through `idftool`:

```bash
alias idf.py='idftool idf.py'
```
