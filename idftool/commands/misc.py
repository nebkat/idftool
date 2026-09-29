"""Discovery and one-off commands: ``devices``, ``monitor`` and ``enter-bootloader``."""
import concurrent.futures
import os.path
import sys
import time

import rich_click as click

from esptool.cmds import detect_chip

from esp_pylib.constants import ESPRESSIF_VID
from esp_pylib.serial_ports import get_port_list

from idftool.cli import cli, pass_state
from idftool.ports import adapter_name, probe_port, prompt_for_port, quiet_esptool, \
    usb_port_for_mac, usb_ports_for_serial

def list_devices(state=None, probe=False):
    """List the serial ports. With `probe`, connect to each to add its chip and MAC, which
    resets the boards."""
    from rich import box
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    ports = get_port_list()
    if not ports:
        click.echo("No serial ports found.", err=True)
        return
    probed = {}
    if probe:
        click.echo(f"Probing {len(ports)} port(s)…", err=True)
        baud = state.baud if state is not None else 115200
        with quiet_esptool(), concurrent.futures.ThreadPoolExecutor(
                max_workers=min(8, len(ports))) as pool:
            for found in pool.map(lambda p: probe_port(p.device, baud), ports):
                probed[found["port"]] = found

    header = ["Port"]
    if probe:
        header += ["Chip", "MAC"]
    header += ["Type", "USB serial #", "USB ID", "Location"]
    rows, styles = [], []
    for p in ports:
        native = p.vid == ESPRESSIF_VID
        row = [p.device]
        style = [{"fg": "green", "bold": True}]
        if probe:
            found = probed[p.device]
            row += [found["chip"] or "unavailable", found["mac"] or ""]
            style += [{"bold": True} if found["chip"] else {"fg": "red"}, {}]
        row += [adapter_name(p), p.serial_number or "",
                f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None else "", p.location or ""]
        style += [{"fg": "cyan" if native else "yellow"}, {}, {"dim": True}, {"dim": True}]
        rows.append(row)
        styles.append(style)
    console = Console(highlight=False)
    if console.is_terminal:
        def rich_style(style):
            return " ".join(filter(None, ["bold" if style.get("bold") else "",
                                          "dim" if style.get("dim") else "", style.get("fg")]))

        # Drop columns from the right (Location, USB ID, and with a MAC column, the serial
        # number) rather than squeeze every column on a narrow terminal.
        shown = len(header)
        while shown > (4 if probe else 3) and (
                sum(max(len(r[i]) for r in [header, *rows]) + 3 for i in range(shown)) + 1
                > console.width):
            shown -= 1
        table = Table(box=box.ROUNDED, border_style="dim", header_style="bold")
        for heading in header[:shown]:
            table.add_column(heading, no_wrap=True)
        for row, style in zip(rows, styles):
            table.add_row(*(Text(cell, style=rich_style(st))
                            for cell, st in list(zip(row, style))[:shown]))
        console.print(table)
    else:
        # Space-separated columns when piped, for awk and friends.
        widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
        for row in [header, *rows]:
            print("  ".join(f"{c:<{w}}" for c, w in zip(row, widths)).rstrip())
    failures = [(port, found["error"]) for port, found in probed.items() if found["error"]]
    if failures:
        click.echo()
        for port, error in failures:
            click.echo(click.style(f"{port}: {error}", dim=True))


@cli.command('devices', aliases=['ports'], help='List serial ports and their USB adapters')
@click.option('--probe', is_flag=True,
              help='Connect to each port to add its chip and MAC (resets the boards)')
@pass_state
def cmd_devices(state, probe):
    return list_devices(state, probe or state.probe)


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
    if (state.mac or state.usb_serial) and not state.port:
        # Only a USB serial number can name a port that isn't there yet.
        wanted = state.mac or state.usb_serial
        print(f"Waiting for {wanted}...", file=sys.stderr)
        while (port := usb_port_for_mac(state.mac) if state.mac
               else next(iter(usb_ports_for_serial(state.usb_serial)), None)) is None:
            time.sleep(poll_interval)
    else:
        port = state.port or prompt_for_port()
        if not port:
            raise click.UsageError(
                "enter-bootloader requires -p/--port, -m/--mac or --usb-serial")
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
