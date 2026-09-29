"""Discovery and one-off commands: ``devices`` and ``enter-bootloader``."""
import os.path
import sys
import time

import rich_click as click

from esptool.cmds import detect_chip

from esp_pylib.constants import ESPRESSIF_VID
from esp_pylib.serial_ports import get_port_list

from idftool.cli import cli, pass_state
from idftool.ports import prompt_for_port

#: ``(vid, pid)`` → adapter name; a ``None`` pid matches any product from that vendor.
USB_ADAPTERS = {
    (0x303A, 0x1001): "ESP USB-Serial/JTAG",
    (0x303A, 0x0002): "ESP USB-OTG (ROM)",
    (0x303A, None): "ESP USB",
    (0x10C4, 0xEA60): "CP210x",
    (0x10C4, 0xEA70): "CP2105",
    (0x10C4, None): "Silicon Labs",
    (0x1A86, 0x7523): "CH340",
    (0x1A86, 0x55D3): "CH343",
    (0x1A86, 0x55D4): "CH9102",
    (0x1A86, None): "WCH",
    (0x0403, 0x6001): "FT232R",
    (0x0403, 0x6010): "FT2232H",
    (0x0403, 0x6011): "FT4232H",
    (0x0403, 0x6014): "FT232H",
    (0x0403, 0x6015): "FT231X",
    (0x0403, None): "FTDI",
    (0x067B, 0x2303): "PL2303",
    (0x067B, 0x23A3): "PL2303GC",
    (0x067B, 0x23C3): "PL2303GT",
    (0x067B, 0x23D3): "PL2303GL",
    (0x067B, None): "PL2303",
}


def adapter_name(port) -> str:
    """What kind of USB-serial adapter `port` is."""
    if port.vid is None:
        return port.description or ""
    return (USB_ADAPTERS.get((port.vid, port.pid))
            or USB_ADAPTERS.get((port.vid, None))
            or port.product or port.description or "USB serial")


def list_devices():
    ports = get_port_list()
    if not ports:
        click.echo("No serial ports found.", err=True)
        return
    header = ("PORT", "TYPE", "SERIAL", "USB ID", "LOCATION")
    rows = [(p.device, adapter_name(p), p.serial_number or "",
             f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None else "", p.location or "")
            for p in ports]
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]

    def line(cells, styles):
        padded = [f"{c:<{w}}" for c, w in zip(cells, widths)]
        padded[-1] = cells[-1]
        return "  ".join(click.style(c, **s) for c, s in zip(padded, styles))

    click.echo(line(header, [{"bold": True, "dim": True}] * len(header)))
    for port, row in zip(ports, rows):
        native = port.vid == ESPRESSIF_VID
        click.echo(line(row, [{"fg": "green", "bold": True},
                              {"fg": "cyan" if native else "yellow"},
                              {},
                              {"dim": True},
                              {"dim": True}]))


@cli.command('devices', help='List serial ports and their USB adapters')
def cmd_devices():
    return list_devices()


def enter_bootloader(state):
    port = state.port or prompt_for_port()
    if not port:
        raise click.UsageError("enter-bootloader requires -p/--port")
    baud, poll_interval = state.baud, 0.05
    print(f"Waiting for {port}...", file=sys.stderr)
    while True:
        while not os.path.exists(port):
            time.sleep(poll_interval)
        try:
            esp = detect_chip(port, baud=baud)
            break
        except Exception as e:
            print(f"Bootloader entry failed: {type(e).__name__}: {e}. Retrying...", file=sys.stderr)
            time.sleep(poll_interval)
    print(f"In download mode: {esp.CHIP_NAME} ({port})")


@cli.command('enter-bootloader', help='Fast-poll the serial port and drop the chip into ROM bootloader '
                                      'mode as soon as it appears, then exit without resetting')
@pass_state
def cmd_enter_bootloader(state):
    return enter_bootloader(state)
