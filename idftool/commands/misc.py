"""Discovery and one-off commands: ``devices``, ``monitor``, ``idf.py`` and
``enter-bootloader``."""
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
    """List the serial ports. With `probe`, connect to each to add its chip and MAC, and
    what ``state.identify`` names it, which resets the boards."""
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
        identify = state.identify if state is not None else None
        with quiet_esptool(), concurrent.futures.ThreadPoolExecutor(
                max_workers=min(8, len(ports))) as pool:
            for found in pool.map(lambda p: probe_port(p.device, baud, identify), ports):
                probed[found["port"]] = found

    # Only when something named a device, or tried to.
    identified = any(f.get("identity") is not None or f.get("identify_error")
                     for f in probed.values())
    header = ["Port"]
    if identified:
        header += ["Device"]
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
            if identified:
                identity = found.get("identity")
                row += ["" if identity is None else str(identity)]
                style += [{"bold": True}]
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
        while shown > (4 if probe else 3) + identified and (
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
    failures = [(port, found["error"] or f"could not identify: {found['identify_error']}")
                for port, found in probed.items() if found["error"] or found.get("identify_error")]
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


#: idf.py actions that talk to a device, besides any ``*-flash`` and ``efuse-*`` one.
IDF_PY_DEVICE_ACTIONS = {"monitor", "erase-flash", "read-otadata", "erase-otadata",
                         "coredump-info", "coredump-debug"}
#: ``*-flash`` / ``efuse-*`` actions that only build.
IDF_PY_BUILD_ACTIONS = {"dfu-flash", "uf2-flash", "efuse-common-table", "efuse-custom-table"}


def idf_py_needs_device(args) -> bool:
    """Whether idf.py `args` include an action that talks to a device."""
    for arg in args:
        action = arg.replace("_", "-")
        if action.startswith("-") or action in IDF_PY_BUILD_ACTIONS:
            continue
        if (action in IDF_PY_DEVICE_ACTIONS or action == "flash" or action.endswith("-flash")
                or action.startswith("efuse-")):
            return True
    return False


def _idf_py() -> list[str]:
    """How to run idf.py: from PATH, else from $IDF_PATH with ESP-IDF's own Python."""
    import shutil

    if found := shutil.which("idf.py"):
        return [found]
    tools = os.path.join(os.environ.get("IDF_PATH", ""), "tools", "idf.py")
    if not (os.environ.get("IDF_PATH") and os.path.isfile(tools)):
        raise click.ClickException("idf.py not found; run ESP-IDF's export script first")
    env = os.environ.get("IDF_PYTHON_ENV_PATH")
    python = env and shutil.which("python", path=os.path.join(
        env, "Scripts" if sys.platform == "win32" else "bin"))
    return [python, tools] if python else [tools]


def _exec(argv):
    """Replace this process with `argv`, so idf.py owns the terminal, signals and exit code."""
    if sys.platform == "win32":
        import subprocess

        sys.exit(subprocess.call(argv))
    os.execv(argv[0], argv)


def idf_py(state, idf_py_args=()):
    """Run idf.py, adding ``-p`` for the device chosen by ``-p``, ``-m``, ``--usb-serial`` or
    the picker when an action needs one."""
    args = list(idf_py_args)
    if not {"-p", "--port"} & set(args) and idf_py_needs_device(args):
        if port := state.resolve_port(allow_none=True):
            args = ["-p", port, *args]
    _exec([*_idf_py(), *args])


@cli.command('idf.py', help='Run ESP-IDF\'s idf.py, asking which device to use for actions that '
                            'need one. Arguments go to idf.py',
             context_settings=dict(ignore_unknown_options=True, help_option_names=[]))
@click.argument('idf_py_args', nargs=-1, type=click.UNPROCESSED)
@pass_state
def cmd_idf_py(state, idf_py_args):
    return idf_py(state, idf_py_args)


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
