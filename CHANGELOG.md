# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/).

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

[v0.3.0]: https://github.com/nebkat/idftool/releases/tag/v0.3.0
[v0.2.2]: https://github.com/nebkat/idftool/releases/tag/v0.2.2
[v0.2.1]: https://github.com/nebkat/idftool/releases/tag/v0.2.1
[v0.2.0]: https://github.com/nebkat/idftool/releases/tag/v0.2.0
[v0.1.0]: https://github.com/nebkat/idftool/releases/tag/v0.1.0
