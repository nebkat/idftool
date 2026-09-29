# Scripting

`idftool` works the same in scripts and CI, with a few differences from
interactive use.

## No questions

Without a terminal, `idftool` never shows the device list. Give the device with
`-p` or `-m`; otherwise it tries each port in turn and uses the first ESP that
answers. `-y` does the same in an interactive shell.

```bash
idftool -m 7c:2c:67:92:79:c0 write-bundle release.zip
```

Prefer `-m` for anything that runs unattended: a port name follows the USB
socket, the MAC follows the board.

## Output

- `get-nvs` prints only the values to stdout; everything else goes to stderr.
- Tables (partition table, NVS, filesystem listings) are plain Markdown when
  stdout isn't a terminal, with the running app marked `*`.
- `idftool devices` prints space-separated columns when piped.

## Several boards

Loop over MAC addresses:

```bash
for mac in 7c:2c:67:92:79:c0 9c:13:9e:1b:d4:6c b8:f8:62:49:ea:14; do
  idftool -m "$mac" ota build/my-app.bin
done
```

Or flash them all in parallel, one process per board:

```bash
for mac in 7c:2c:67:92:79:c0 9c:13:9e:1b:d4:6c; do
  idftool -m "$mac" ota build/my-app.bin &
done
wait
```

## Offline

Commands that only need the partition layout run without a device when given
`--partition-table-file`, so bundles, images, and NVS images can be built in CI:

```bash
idftool --partition-table-file partitions.csv create-nvs example.csv -o nvs.bin --partition nvs
```

## From Python

Every command is also a function, for when a shell script isn't enough. See
[Python library](../library.md).
