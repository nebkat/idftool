# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/).

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

[v0.2.2]: https://github.com/nebkat/idftool/releases/tag/v0.2.2
[v0.2.1]: https://github.com/nebkat/idftool/releases/tag/v0.2.1
[v0.2.0]: https://github.com/nebkat/idftool/releases/tag/v0.2.0
[v0.1.0]: https://github.com/nebkat/idftool/releases/tag/v0.1.0
