# idftool test fixture project

A deliberately trivial ESP-IDF app whose only purpose is to generate the binary fixtures the
idftool **device** tests flash and inspect. The app does nothing meaningful — the tests never
check what it does, only that idftool can flash / read / dump / inspect it.

It is built **twice**, stamped with different app-descriptor versions (`1.0.0` and `2.0.0`), so the
OTA tests have two distinguishable images to switch between.

## Regenerating the fixtures

Requires **ESP-IDF 6.0.x** (pinned in `main/idf_component.yml`) and `esptool` on PATH.

```sh
. $IDF_PATH/export.sh
cd test/project
./build_fixtures.sh esp32s3        # or esp32, esp32c3, ...
```

This writes into `test/fixtures/<chip>/`:

| File                  | Made from                     | Used by |
|-----------------------|-------------------------------|---------|
| `app-v1.bin`          | build with `PROJECT_VER=1.0.0`| factory / ota |
| `app-v2.bin`          | build with `PROJECT_VER=2.0.0`| ota (switch versions) |
| `bootloader.bin`      | build                         | (reference) |
| `partition-table.bin` | build                         | (reference) |
| `partitions.csv`      | `project/partitions.csv`      | write-table / print-table |
| `nvs.csv`             | `project/nvs.csv`             | create-nvs / write-nvs |
| `flash-image.bin`     | esptool merge-bin of the above| write-image provisioning + image round-trip |

The layout (`partitions.csv`) has `nvs`, `otadata`, `phy_init`, `factory`, `ota_0`, `ota_1`, and a
`storage` data partition — enough to exercise every idftool command. It fits a 4 MB flash.

Built fixtures are committed so the tests only need a board (not ESP-IDF) to run.
