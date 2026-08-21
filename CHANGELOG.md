# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- **Breaking.** Commands that can read either a partition or a file now take
  the partition as their first positional argument and the file behind
  `-f`/`--file`, matching `read`/`write`/`erase`/`view`. Commands whose subject
  can only be a file take `-f` as well, so there is no per-command exception to
  remember. Files that are *payloads* (`write-table`, `factory`, `ota`) or
  outputs (`-o`) are unaffected.

  | Was | Now |
  |-----|-----|
  | `print-fs image.bin` | `print-fs -f image.bin` |
  | `print-fs --partition storage` | `print-fs storage` |
  | `extract-fs image.bin DEST` | `extract-fs -f image.bin DEST` |
  | `print-image flash.bin` | `print-image -f flash.bin` |
  | `print-bundle release.zip` | `print-bundle -f release.zip` |
  | `print-table partitions.csv` | `print-table -f partitions.csv` |
  | `print-nvs`/`get-nvs`/`set-nvs` | `PARTITION`, or `-f FILE` |

  Passing a file where a partition now belongs is caught before connecting, so
  it fails with a usage error naming the `-f` form rather than a serial
  timeout.
- **Breaking.** `convert-table` is now `create-table`, matching the `create-X`
  builder in every other family (`create-image`, `create-bundle`, `create-nvs`,
  `create-fs`). `convert-table` remains as a command alias; the library function
  is `idftool.create_table`. Its `--format` option lost its `-f` short form,
  which now means `--file` everywhere else.

### Added
- NVS partitions can now be read and edited, not just generated. ESP-IDF's
  `nvs_partition_gen` can only build an image from scratch, so idftool has its
  own NVS parser and entry encoder in `idftool.nvs`; the encoder is pinned to
  the generator's output byte for byte by the test suite, blob chunking
  included.
  - `print-nvs` (alias `list-nvs`) lists the key/value pairs in a partition on
    the device, or in an image file with `-f`. `--pages` also shows the page map.
  - `extract-nvs -f image.bin out.csv` dumps the contents back to an
    `nvs_partition_gen` CSV, which feeds straight back into `create-nvs`;
    `read-nvs PARTITION out.csv` does the same from the device.
  - `get-nvs` prints the value of one or more keys, bare and one per line, for
    use in scripts. `--raw` writes a blob's bytes to stdout.
  - `set-nvs` sets or deletes keys in a partition or image that already exists.
    The type of
    an existing key is taken from the entry being replaced, so only a new key
    needs one spelled out. Changes are appended the way the firmware writes
    them — the replaced entry is marked erased rather than overwritten — so the
    rest of the partition is untouched and a device write only re-flashes the
    4 KiB pages that changed. With no room left to append, the image is
    compacted instead, which `--rewrite` also asks for directly.

  Encrypted NVS partitions are not supported yet.
- Every command that writes to flash — `write`, `write-image`, `write-nvs`,
  `write-fs`, `write-bundle`, `factory`, `ota`, `set-nvs` — now passes esptool's
  `write_flash` options through, as flags (`--skip-flashed`,
  `--compress`/`--no-compress`, `--encrypt`, `--force`,
  `--ignore-flash-enc-efuse`, `--no-progress`) and as keyword arguments on the
  matching library function, which also reach the ones that take images rather
  than a yes/no (`diff_with`, `no_diff_verify`, `encrypt_files`, `erase_all`).

  `--skip-flashed` is the interesting one: esptool compares the MD5 of what is
  already in flash against the data about to be written and skips the writes
  that would change nothing, so re-flashing an up-to-date partition costs a
  checksum instead of a write. The check is per file, not per sector, so it is
  all-or-nothing for each one.

  An option esptool does not know is rejected rather than ignored. It reads its
  keyword arguments with `kwargs.get`, so a misspelled one would otherwise be a
  write that quietly did something else.
- `dump-image` learned `--size`, which reads only the first N bytes instead of the
  whole chip. Most of a large flash is erased — everything past the last
  partition — and pulling it all back as one serial read is slow and gives a
  long transfer more chance to time out.
- `write-image` learned `--no-erase`, which writes only what the image contains
  instead of erasing the whole chip first. The erase stays on by default —
  that is what writing a whole-flash image has always meant here — but it is
  mutually exclusive with `--skip-flashed`, since nothing can already match a
  chip that was just wiped. Asking for both is an error rather than a silent
  no-op.

### Fixed
- The `rich-click` requirement is now `>=1.9`, which is the version that
  actually added native command aliases. The declared `>=1.8` floor allowed
  1.8.9 to be installed, where the `aliases=` kwarg on a command raises
  `TypeError: Command.__init__() got an unexpected keyword argument 'aliases'`
  at import time, so every idftool invocation failed.

## [v0.7.0] — 2026-08-18

### Added
- Filesystem partitions can now be built, flashed, listed, and extracted, for
  all three filesystems ESP-IDF mounts from a data partition — FAT, littlefs,
  and SPIFFS:
  - `create-fs` builds an image from a directory, offline. Size and filesystem
    come from `--size`/`--type`, or from `--partition` and its subtype.
  - `write-fs` builds an image and flashes it to a named partition. A prebuilt
    image is passed through instead of rebuilt, the way `write-nvs` handles a
    prebuilt NVS binary.
  - `read-fs` reads a partition off the device and extracts it to a directory;
    `extract-fs` does the same for a local image file.
  - `print-fs` (alias `list-fs`) lists the contents of an image file or of a
    partition on the device.

  The filesystem is taken from `--type` if given, else from the partition's
  subtype (`fat`, `littlefs`, `spiffs`), else detected from the image, so most
  invocations need neither. Per-filesystem options (`--fat-sector-size`,
  `--littlefs-name-max`, `--spiffs-page-size`, …) default to ESP-IDF's own
  Kconfig defaults and only need setting when the device's sdkconfig differs.
- FAT images are wrapped in ESP-IDF's wear levelling container by default —
  how `esp_vfs_fat_spiflash_mount_rw_wl` expects a `fat` partition in SPI flash
  to look — and unwrapped transparently when read back, including images the
  device has since written to, where the filesystem has been shifted and
  rotated by the wear levelling layer. `--no-fat-wear-levelling` produces a
  bare image.
- New dependencies: `pyfatfs` (used through its low-level `PyFat` API, so its
  PyFilesystem2 dependency is never imported) and `littlefs-python` (the same
  library `esp_littlefs` generates images with). SPIFFS needs no dependency:
  ESP-IDF's `spiffsgen` is vendored, and idftool adds the reader ESP-IDF does
  not ship. The backends are imported lazily, so no other command pays for
  them at startup.

### Changed
- The command layer is split out of `idftool/__main__.py`, which had grown to
  ~1,500 lines holding all 25 commands plus every shared helper. Commands now
  live one family per module under `idftool/commands/`, with the pieces they
  share in `idftool/cli.py` (the command group and global options),
  `idftool/state.py` (`State`/`Loaded`), `idftool/partitions.py`,
  `idftool/apps.py`, `idftool/nvs.py`, `idftool/ports.py`, and
  `idftool/params.py`. `__main__.py` is now just the entry point.

  No behaviour change: every command's `--help` output is byte-identical, and
  the library API (`from idftool import State, write_image, …`) is unchanged —
  it now resolves each name from the module that defines it. Code reaching into
  `idftool.__main__` for internals (e.g. `idftool.__main__.State`) must import
  from the defining module instead.

## [v0.6.1] — 2026-08-10

### Fixed
- A flash read that fails while printing the partition table no longer aborts
  the command before it starts. The otadata read (used only to mark the active
  app partition) now reports a warning and carries on, and unreadable app
  slots render as `<READ ERROR>` in the table instead of raising esptool's
  `FatalError` out of the printer — requires `esp-idf-defs` v0.1.5.

## [v0.6.0] — 2026-08-02

### Added
- idftool is now importable as a library, esptool-style: every command's
  logic is exposed as a plain function (`write_image`, `factory`, `ota`,
  `read_partition`, `write_partitions`, `dump_bundle`, …) that takes a
  `State` — which owns the serial connection — plus the same arguments as
  the CLI command. `from idftool import State, write_image, factory, ota`
  works with no import side effects, letting callers drive several
  operations over a single connection. The CLI is unchanged: each command
  is now a thin wrapper around its function.
- `enter-bootloader` with no `-p/--port` now shows an interactive picker of
  the visible serial ports (best-guess Espressif device first) with a
  "type manually" escape hatch, since the target may not have appeared yet.
  Falls back to the usual usage error when stdin isn't a TTY.

### Fixed
- `write-bundle` no longer crashes with an internal `StopIteration` when
  flashing a bundle that includes the partition table; it now uses the
  resolved partition-table offset directly.
- `print-table`/`list` moved from the Discovery help panel into the
  Partition table panel.

## [v0.5.1] — 2026-07-18

### Fixed
- Addressing a partition by a numeric offset that matches no partition now
  reports a clear error instead of crashing with an internal `StopIteration`
  (affects `read`, `write`, `erase`, `view`, `create-image`, `create-bundle`,
  and `create-nvs`/`write-nvs`).

## [v0.5.0] — 2026-07-17

### Changed
- The CLI is rebuilt on [click](https://click.palletsprojects.com/) and
  [rich-click](https://github.com/ewels/rich-click) (like esptool): `--help`,
  usage, and errors now render in boxed panels, and commands are grouped into
  labelled sections. Command names, arguments, options, aliases (`reflash`,
  `list`), and behaviour are unchanged — only the help/usage and error
  formatting differ.

### Added
- `create-table` as an alias for `convert-table`.
- `create-nvs` and `write-nvs` now accept a pre-built NVS binary image as
  input (auto-detected), not only a CSV — it is validated against the
  partition size and padded to it, instead of failing when parsed as CSV.
- A test suite: an offline suite (no hardware) plus a real-device suite that
  cycles every command against a connected ESP, with fixtures built from a
  small ESP-IDF app (`make test-offline` / `make test-device`).

### Fixed
- `write-bundle` now reports a clear error when a bundle entry is larger than
  its target partition, instead of crashing with an `AttributeError`.

## [v0.3.0] — 2026-07-15

### Added
- Partition table file commands that make the table itself the subject
  (a positional file), keeping the global `--partition-table-file`
  option for its existing role of overriding the layout of other
  commands:
  - `convert-table <in> <out>` — convert a partition table between CSV
    and binary, offline. Format is auto-detected on input and inferred
    from the output extension (`.csv`/`.bin`) or `--format`.
  - `print-table [file]` — pretty-print a partition table from a CSV or
    binary file (offline), or from the device when no file is given.
  - `dump-table [output]` — read the partition table from the device and
    save it as CSV or binary (default filename
    `{chip}-{mac}-{timestamp}-partition-table.csv`).
  - `write-table <file>` — flash a partition table from a CSV or binary
    file to the device. Requires an explicit file (so it can never read
    the device's current table and write it back to itself), verifies
    the table before flashing (override with `--force`), and warns that
    only the map is replaced — existing partition data is not moved or
    erased.

### Changed
- `list` is now an alias of `print-table` (both print the device's
  partition table identically). Its `--partition-type`,
  `--partition-subtype`, and `--partition-name` flags — which were never
  implemented — have been removed.

### Fixed
- Working with empty or truncated input files now reports a clear error
  instead of a confusing parser failure (e.g. "Partition table is
  missing an end-of-table marker" for a zero-byte image). Empty images,
  app binaries, and bundle ZIPs are rejected up front, as are images too
  small to reach the partition table offset and corrupt (non-ZIP)
  bundles.
- A parsed-but-empty partition table (all-`0xFF` binary region,
  comment-only CSV, etc.) is now rejected across all parse paths rather
  than silently yielding a table with no partitions.
- Loading a partition table CSV that contains bootloader rows (such as one
  produced by `dump-table`) no longer requires re-specifying
  `--primary-bootloader-offset`: the offset embedded in the CSV is
  recovered from the file, so dumped tables round-trip through
  `print-table`, `convert-table`, `write-table`, and `--partition-table-file`
  without extra arguments. The flag still works as an explicit override.

## [v0.2.2] — 2026-06-15

### Added
- `print-image` — inspect a flash image file offline: prints the
  partition table and, for each app partition that contains a valid
  app, the project name, version, IDF version, compile time, ELF
  SHA256, and target chip.
- `print-bundle` — same output for a partition bundle ZIP, driven by
  the embedded `partition_table.csv` and the per-partition `.bin`
  entries.

### Changed
- Bumped `esp-idf-defs` to `~=0.1.3`. `print_partition_table` now takes
  a `(offset, length) -> bytes` accessor instead of an `ESPLoader`
  instance, so the same printing path serves device, image, and bundle
  inspection.

## [v0.2.1] — 2026-06-10

### Changed
- Boot partition state now uses `OtaImageState` for clearer reporting.

## [v0.2.0] — 2026-05-26

### Added
- `create-nvs` and `write-nvs` commands for generating NVS partition
  images from CSV.
- Rich-style boxed traceback formatter for friendlier error output.

### Documentation
- README installation section and command index table.
- Recommend `pipx` over the standalone binary for installation.

## [v0.1.0] — 2026-05-25

### Added
- Initial release.

[v0.7.0]: https://github.com/nebkat/idftool/releases/tag/v0.7.0
[v0.6.1]: https://github.com/nebkat/idftool/releases/tag/v0.6.1
[v0.6.0]: https://github.com/nebkat/idftool/releases/tag/v0.6.0
[v0.5.1]: https://github.com/nebkat/idftool/releases/tag/v0.5.1
[v0.5.0]: https://github.com/nebkat/idftool/releases/tag/v0.5.0
[v0.3.0]: https://github.com/nebkat/idftool/releases/tag/v0.3.0
[v0.2.2]: https://github.com/nebkat/idftool/releases/tag/v0.2.2
[v0.2.1]: https://github.com/nebkat/idftool/releases/tag/v0.2.1
[v0.2.0]: https://github.com/nebkat/idftool/releases/tag/v0.2.0
[v0.1.0]: https://github.com/nebkat/idftool/releases/tag/v0.1.0
