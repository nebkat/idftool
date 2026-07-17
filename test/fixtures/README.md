# Device test fixtures

Per-chip binary fixtures for the device tests live here in `<chip>/` subdirectories (e.g.
`esp32s3/`). They are generated from the ESP-IDF app in `../project/` — see that project's README —
and are committed so the device tests only need a board, not a full ESP-IDF install.

Regenerate with:

```sh
. $IDF_PATH/export.sh
../project/build_fixtures.sh esp32s3
```
