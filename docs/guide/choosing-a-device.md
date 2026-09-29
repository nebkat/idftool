# Choosing a device

Every command that talks to a device needs a port. There are three ways to give
it one.

| How | When |
|-----|------|
| `-p PORT` | You know the port. |
| `-m MAC` | You know the board. Survives replugging into a different socket. |
| Neither | idftool lists the connected devices and asks. |

## The picker

Without `-p` or `-m`, idftool shows the serial ports and asks which to use:

```text
? Select device (↑↓ move · ↵ select · p probe · r refresh · k kill · q quit)
 » /dev/cu.usbmodem11401         ESP USB-Serial/JTAG   ESP32-S3      7c:2c:67:92:79:c0
   /dev/cu.usbmodem11201         ESP USB-Serial/JTAG                 9c:13:9e:1b:d4:6c
   /dev/cu.PL2303G-USBtoUART10   PL2303GT
   /dev/cu.usbserial-150         CP210x                Unavailable   held by idf.py (pid 4417)
```

The list comes from USB alone, so no board is reset by looking at it:

- **ESP USB-Serial/JTAG** ports (the chip's built-in USB) report the chip's MAC
  as their USB serial number, so the MAC shows straight away.
- **USB-serial adapters** (CP210x, CH340, FTDI, PL2303) show the adapter type
  only. Their serial number belongs to the adapter, not the ESP.
- A port another process has open shows who holds it.

| Key | Action |
|-----|--------|
| ++arrow-up++ ++arrow-down++ | Move |
| ++enter++ | Use the highlighted device |
| ++p++ | Connect to the highlighted port to identify its chip. Resets that board. Press again to re-check. |
| ++r++ | Rescan the ports |
| ++k++ | Kill the process holding the highlighted port (macOS/Linux) |
| ++q++ | Quit |

`--probe` connects to every port up front instead, so the chip column is filled
for all of them.

After you pick, idftool prints how to skip the picker next time:

```text
Device: /dev/cu.usbmodem11401 — ESP32-S3 · ESP USB-Serial/JTAG · 7c:2c:67:92:79:c0
Re-run with: idftool -p /dev/cu.usbmodem11401 get-boot
         or: idftool -m 7c:2c:67:92:79:c0 get-boot
```

`-y` skips the picker, as does running without a terminal (in a script or CI).
esptool then picks the port itself.

## By MAC address

```bash
idftool -m 9c:13:9e:1b:d4:6c write-bundle release.zip
```

The MAC is matched against USB serial numbers, so boards on their built-in USB
port are found without connecting to anything. Any spelling works:
`9C:13:9E:1B:D4:6C`, `9c-13-9e-1b-d4-6c`, `9c139e1bd46c`.

A board behind a USB-serial adapter has no MAC in its USB descriptors. Add
`--probe` to connect to the adapter ports and read the MAC from the chip, which
resets those boards.

## Port names on macOS

`/dev/cu.usbmodem*` names come from where the device is plugged in, not from
the device: `usbmodem11401` is bus 1 → hub port 1 → port 4. The same socket
gives the same name to whatever is plugged into it, and moving a board changes
its name. The `LOCATION` column of [`idftool devices`](../commands/discovery.md#devices)
shows that path.

Use the `cu.*` nodes, not `tty.*`. Opening a `tty.*` node waits for a modem
carrier signal that never comes.
