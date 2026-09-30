# Choosing a device

Every command that talks to a device needs a port. There are four ways to give
it one.

| How | When |
|-----|------|
| Default | `idftool` lists the connected devices and asks. |
| `-p PORT` | You know the port. |
| `-m MAC` | You know the chip. Survives replugging into a different socket. |
| `--usb-serial SERIAL` | The board has a USB-serial adapter with its own serial number. |

## The picker

Without `-p` or `-m`, `idftool` shows the serial ports and asks which to use:

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

After you pick, `idftool` prints how to skip the picker next time:

```text
Device: /dev/cu.usbmodem11401 — ESP32-S3 · ESP USB-Serial/JTAG · 7c:2c:67:92:79:c0
Re-run with: idftool -p /dev/cu.usbmodem11401 get-boot
         or: idftool -m 7c:2c:67:92:79:c0 get-boot
```

`-y` skips the picker, as does running without a terminal (in a script or CI).
`esptool` then picks the port itself.

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

## By USB serial number

A board with a USB-serial adapter soldered on can be named by the adapter's
serial number instead, as shown in the "USB serial #" column of
[`idftool devices`](../reference/discovery.md#devices):

```bash
idftool --usb-serial A50285BI write-bundle release.zip
```

Unlike `-m`, this never connects to anything. It needs an adapter with a unique
serial number: FTDI and CP2102N chips have one, while many CH340 and older
CP2102 chips have none or share `0001`. If two ports share the number,
`idftool` stops and asks for `-p`.

On Windows, FTDI's driver adds a channel letter to the serial number
(`A50285BIA`). The number without it still matches.

For boards on their built-in USB port the serial number is the MAC, so `-m` and
`--usb-serial` find the same port.

## With `idf.py`

[`idftool idf.py`](../reference/idf-py.md) runs ESP-IDF's `idf.py` and asks for
a device when the actions need one:

```bash
idftool idf.py build flash monitor
```

Alias it to put every `idf.py` call through the device list:

```bash
alias idf.py='idftool idf.py'
```
