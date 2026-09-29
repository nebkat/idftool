# Write options

Every command that writes flash (`write`, `write-image`, `write-nvs`,
`write-fs`, `write-bundle`, `factory`, `ota`) takes the same options. They go
**after** the command name, and any left unset keep the default.

```bash
idftool write storage build/spiffs.bin --diff --no-progress
```

| Option | Purpose |
|--------|---------|
| `--skip-flashed` / `--no-skip-flashed` | Skip each file whose partition already holds it. All or nothing per file. |
| `--diff` / `--no-diff` | Rewrite only the flash sectors that differ. |
| `--compress` / `--no-compress` | Compress data on the way to the device. On by default unless the flasher stub is disabled. |
| `--encrypt` | Encrypt the data as it is written. |
| `--force` | Ignore safety and content checks: chip or revision mismatch, secure boot, flash size. |
| `--ignore-flash-enc-efuse` | Ignore the flash encryption eFuse settings. |
| `--no-progress` | Don't print progress while writing. |

## Skipping what's already there

`ota` and `factory` turn on `--skip-flashed` and `--diff` by default, so
reflashing the same build is nearly instant and a small change rewrites only
the sectors it touched. Other commands leave both off unless asked.

`--skip-flashed`
:   Hashes the partition on the device and compares it with the file. The
    first sector is checked on its own first: an app keeps its version and ELF
    hash there, so a different build is usually caught in milliseconds.

`--diff`
:   Compares a sector at a time and writes each run of changed sectors. If so
    much differs that the comparison stops paying for itself, it falls back to
    writing the whole region.

`write-image` adds `--erase`/`--no-erase` (on by default), and refuses
`--skip-flashed` unless the erase is off: nothing can match a chip that was
just wiped. See [`write-image`](../commands/images.md#write-image).

`write-table` doesn't take these. Its `--force` already means "flash a table
that failed verification".

From Python, the same names work as keyword arguments. See
[Python library](../library.md#write-options).
