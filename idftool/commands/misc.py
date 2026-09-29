"""Discovery and one-off commands: ``devices``, ``monitor`` and ``enter-bootloader``."""
import os.path
import sys
import time

import rich_click as click

from esptool.cmds import detect_chip

from esp_pylib.constants import ESPRESSIF_VID
from esp_pylib.serial_ports import get_port_list

from idftool.cli import cli, pass_state
from idftool.ports import adapter_name, prompt_for_port, usb_port_for_mac

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


def monitor(state, monitor_args=()):
    """Run esp-idf-monitor on the device chosen by ``-p``, ``-m`` or the picker."""
    from esp_idf_monitor import idf_monitor

    argv = list(monitor_args)
    # Help needs no device, and a port given to the monitor itself wins.
    if not {"-h", "--help", "-p", "--port"} & set(argv) and (port := state.resolve_port()):
        argv = ["--port", port, *argv]
    if state.no_reset:
        argv.append("--no-reset")
    sys.argv = ["idftool monitor", *argv]
    idf_monitor.main()


@cli.command('monitor', help='Open esp-idf-monitor on the selected device. Arguments after '
                             '`monitor` go to esp-idf-monitor (`idftool monitor -h` lists them)',
             context_settings=dict(ignore_unknown_options=True, help_option_names=[]))
@click.argument('monitor_args', nargs=-1, type=click.UNPROCESSED)
@pass_state
def cmd_monitor(state, monitor_args):
    return monitor(state, monitor_args)


def enter_bootloader(state):
    baud, poll_interval = state.baud, 0.05
    if state.mac and not state.port:
        # Only a USB serial number can name a port that isn't there yet.
        print(f"Waiting for {state.mac}...", file=sys.stderr)
        while (port := usb_port_for_mac(state.mac)) is None:
            time.sleep(poll_interval)
    else:
        port = state.port or prompt_for_port()
        if not port:
            raise click.UsageError("enter-bootloader requires -p/--port or -m/--mac")
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
