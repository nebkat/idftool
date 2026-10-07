"""Interactive device selection."""
import concurrent.futures
import contextlib
import re
import shlex
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Callable, Optional

import questionary
import rich_click as click

import esp_pylib.serial_ports as serial_ports
from esp_pylib.constants import ESPRESSIF_VID

#: Given a connected ``ESPLoader``, returns an object naming the device, or None.
Identify = Callable[["ESPLoader"], object]

REFRESH = "\0refresh"
MANUAL = "\0manual"
QUIT = "\0quit"
KILL = "\0kill"
NONE = "\0none"

#: Whether `k` can kill a process holding a port.
CAN_KILL = sys.platform != "win32"


def _style():
    return questionary.Style([
        # Matches `idftool devices`: bold green ports, ESP USB in cyan, adapter chips in yellow.
        ("meta", "bold fg:cyan"),
        ("node", "bold fg:green"),
        ("native", "fg:cyan"),
        ("adapter", "fg:yellow"),
        ("chip", "bold"),
        ("pending", "fg:ansibrightblack"),
        ("detail", ""),
        ("bad", "fg:red"),
    ])


@contextlib.contextmanager
def quiet_esptool():
    """Silence esptool's output."""
    from esptool.logger import log

    previous = getattr(log, "_verbosity", "auto")
    log.set_verbosity("silent")
    try:
        yield
    finally:
        log.set_verbosity(previous)


#: ``(vid, pid)`` → adapter name; a ``None`` pid matches any product from that vendor.
USB_ADAPTERS = {
    (ESPRESSIF_VID, 0x1001): "ESP USB-Serial/JTAG",
    (ESPRESSIF_VID, 0x0002): "ESP USB-OTG (ROM)",
    (ESPRESSIF_VID, None): "ESP USB",
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


def usb_record(port) -> dict:
    """What USB says about `port`, without opening it: an ESP USB-Serial/JTAG port's
    serial number is the chip's MAC."""
    native = port.vid == ESPRESSIF_VID
    return {"port": port.device, "chip": None,
            "mac": normalize_mac(port.serial_number) if native else None, "native": native,
            "serial": port.serial_number, "adapter": adapter_name(port),
            "description": port.description, "error": None}


def port_holders(port: str) -> list[tuple[int, str]]:
    """``(pid, program)`` for each process holding `port`, via lsof if available. A Python
    process is named by what it runs (``esp-rfc2217-relay``, ``idf_monitor``), not as Python."""
    import subprocess

    try:
        listed = subprocess.run(["lsof", "-F", "pc", port],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    holders, pid = [], None
    for line in listed.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("c") and pid is not None:
            holders.append((pid, program_name(pid) or line[1:]))
            pid = None
    return holders


def program_name(pid: int) -> Optional[str]:
    """What a Python process runs: the module of ``python -m <module>``, else its script's
    name. None for anything else, or when ps can't say."""
    import subprocess

    try:
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=5).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    if not command or "python" not in Path(command[0]).name.lower():
        return None
    arguments = iter(command[1:])
    for argument in arguments:
        if argument == "-m":
            return next(arguments, None)
        if argument == "-c":
            return None
        if argument in ("-X", "-W", "-Q"):
            next(arguments, None)
        elif not argument.startswith("-"):
            return Path(argument).name
    return None


def held_reason(port: str) -> Optional[str]:
    """Which other processes hold `port`, or None."""
    import os

    others = [(pid, name) for pid, name in port_holders(port) if pid != os.getpid()]
    if not others:
        return None
    return "held by " + ", ".join(f"{name} (pid {pid})" for pid, name in others)


def _probe_failure(error: BaseException, port: Optional[str] = None) -> str:
    """A one-line reason a probe failed."""
    import errno

    from serial import SerialException

    seen = error
    while seen is not None:
        code = getattr(seen, "errno", None)
        if code in (errno.EAGAIN, errno.EBUSY):
            reason = held_reason(port) if port else None
            if reason:
                return reason
            if port and port_holders(port):
                return "held by this process"
            return "locked, but no holder found (device re-enumerating?)"
        if code in (errno.EACCES, errno.EPERM):
            return "permission denied on the device node"
        if code in (errno.ENOENT, errno.ENXIO):
            return "device node is gone (unplugged?)"
        if isinstance(seen, SerialException):
            return f"port could not be opened ({seen})"
        seen = seen.__cause__ or seen.__context__

    from esptool.util import FatalError

    if isinstance(error, FatalError):
        first = str(error).splitlines()[0]
        return ("no response (not an ESP, or not in download mode)"
                if "Failed to connect" in first else first)
    return f"{type(error).__name__}: {error}"


def _release(esp) -> None:
    """Reset the chip out of download mode and close the port."""
    with contextlib.suppress(Exception):
        esp.hard_reset()
    for attribute in ("_port", "port"):
        handle = getattr(esp, attribute, None)
        if hasattr(handle, "close"):
            try:
                handle.close()
            except OSError:
                pass


def probe_port(port: str, baud: int, identify: Optional[Identify] = None) -> dict:
    """Chip, MAC and ``identify(esp)`` for `port`; failures go in ``error``."""
    from esptool.cmds import detect_chip

    with quiet_esptool():
        try:
            esp = detect_chip(port, baud=baud, connect_attempts=1)
        except Exception as error:  # noqa: BLE001
            return {"port": port, "chip": None, "mac": None,
                    "error": _probe_failure(error, port)}
        try:
            mac = None
            with contextlib.suppress(Exception):
                mac = ":".join(f"{b:02x}" for b in esp.read_mac("BASE_MAC"))
            found = {"port": port, "chip": esp.CHIP_NAME, "mac": mac, "identity": None,
                     "error": None}
            if identify is not None:
                try:
                    found["identity"] = identify(esp)
                except Exception as error:  # noqa: BLE001
                    found["identify_error"] = str(error) or type(error).__name__
            return found
        finally:
            _release(esp)


def device_fields(candidate: dict) -> tuple[str, str, str, str]:
    """``(port, adapter, name, detail)`` for display; `name` is empty until probed."""
    port, adapter = candidate["port"], candidate.get("adapter") or ""
    if candidate.get("error"):
        return port, adapter, "Unavailable", candidate["error"]
    chip, mac = candidate.get("chip"), candidate.get("mac")
    if chip is None:
        if adapter:
            return port, adapter, "", mac or ""
        return port, adapter, "Unidentified", candidate.get("description") or ""
    if candidate.get("identity") is not None:
        return port, adapter, str(candidate["identity"]), " · ".join(filter(None, [chip, mac]))
    if candidate.get("identify_error"):
        return port, adapter, chip, " · ".join(filter(None, [
            mac, f"could not identify: {candidate['identify_error']}"]))
    return port, adapter, chip, mac or ""


def _columns(cells, widths) -> str:
    """`cells` padded to `widths`, skipping columns no candidate fills."""
    return "   ".join(f"{c:<{w}}" for c, w in zip(cells, widths) if w).rstrip()


def device_label(candidate: dict, widths: Optional[tuple[int, int, int]] = None) -> str:
    """A device as one line, padded to `widths` if given."""
    port, adapter, what, detail = device_fields(candidate)
    if widths is not None:
        return _columns((port, adapter, what, detail), (*widths, 1))
    if candidate.get("error"):
        return f"{port} — {what}: {detail}"
    return f"{port} — " + " · ".join(filter(None, [what, adapter, detail]))


def device_widths(candidates: list[dict]) -> tuple[int, int, int]:
    """Port, adapter and name column widths for `candidates`."""
    rows = [device_fields(c) for c in candidates]
    return tuple(max((len(r[i]) for r in rows), default=0) for i in range(3))


def device_labels(candidates: list[dict]) -> list[str]:
    """Column-aligned labels for `candidates`."""
    widths = device_widths(candidates)
    return [device_label(c, widths) for c in candidates]


def normalize_mac(text: Optional[str]) -> Optional[str]:
    """`text` as ``aa:bb:cc:dd:ee:ff`` if it is a MAC address, else None."""
    digits = re.sub(r"[:.-]", "", text or "").lower()
    if not re.fullmatch(r"[0-9a-f]{12}", digits):
        return None
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2))


def usb_port_for_mac(mac: str) -> Optional[str]:
    """The port whose USB serial number is `mac`, as ESP USB-Serial/JTAG ports report it."""
    return next((p.device for p in serial_ports.get_port_list()
                 if normalize_mac(p.serial_number) == mac), None)


def usb_ports_for_serial(serial: str) -> list[str]:
    """The ports whose USB serial number is `serial`. FTDI's Windows driver appends the
    channel letter (``A50285BI`` → ``A50285BIA``), so that matches too."""
    return [p.device for p in serial_ports.get_port_list()
            if p.serial_number == serial
            or (p.vid == 0x0403 and p.serial_number and p.serial_number[:-1] == serial)]


def find_port_for_usb_serial(serial: str) -> str:
    """The one port whose USB serial number is `serial`."""
    click.echo(f"Looking for USB serial number {serial}…", err=True)
    found = usb_ports_for_serial(serial)
    if not found:
        raise click.ClickException(f"No port with USB serial number {serial} found")
    if len(found) > 1:
        raise click.ClickException(
            f"USB serial number {serial} is shared by {', '.join(found)}; use -p to pick one")
    click.echo(click.style("Device: ", bold=True) + found[0], err=True)
    return found[0]


def find_port_for_mac(mac: str, baud: int, *, probe: bool = False) -> str:
    """The port of the device with `mac`: from USB serial numbers, else (with `probe`) by
    connecting to the ports whose serial number isn't a MAC."""
    click.echo(f"Looking for {mac}…", err=True)
    port = usb_port_for_mac(mac) or (_probe_for_mac(mac, baud) if probe else None)
    if port is None:
        raise click.ClickException(
            f"No USB-Serial/JTAG device with MAC {mac} found "
            "(--probe also connects to USB-serial adapter ports, resetting their boards)")
    click.echo(click.style("Device: ", bold=True) + port, err=True)
    return port


def _probe_for_mac(mac: str, baud: int) -> Optional[str]:
    """The port of the device with `mac`, found by connecting to each unidentified port."""
    # Ports whose serial number is some other MAC are known not to match.
    others = [p.device for p in serial_ports.get_port_list()
              if normalize_mac(p.serial_number) is None]
    if others:
        click.echo(f"Probing {len(others)} port(s)…", err=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(others))) as pool:
            for found in pool.map(lambda p: probe_port(p, baud), others):
                if found["mac"] == mac:
                    return found["port"]
    return None


def print_rerun_hint(port: str, mac: Optional[str] = None,
                     usb_serial: Optional[str] = None) -> None:
    """Print the command with ``-p`` filled in, and with ``-m`` if the MAC is known, else
    ``--usb-serial`` if the port has a serial number no other port shares."""
    arguments = [shlex.quote(a) for a in sys.argv[1:]]
    alternative = None
    if mac:
        alternative = ["-m", mac]
    elif usb_serial and len(usb_ports_for_serial(usb_serial)) == 1:
        alternative = ["--usb-serial", shlex.quote(usb_serial)]
    click.echo(click.style("Re-run with: ", fg="cyan", bold=True)
               + click.style(" ".join(["idftool", "-p", port, *arguments]), fg="cyan"), err=True)
    if alternative:
        click.echo(click.style("         or: ", fg="cyan", bold=True)
                   + click.style(" ".join(["idftool", *alternative, *arguments]), fg="cyan"),
                   err=True)


def kill_holders(port: str) -> None:
    """Offer to kill the processes holding `port`."""
    import os

    holders = [(pid, name) for pid, name in port_holders(port) if pid != os.getpid()]
    if not holders:
        click.echo(f"Nothing else is holding {port}.", err=True)
        return
    names = ", ".join(f"{name} (pid {pid})" for pid, name in holders)
    try:
        if not questionary.confirm(f"Kill {names}?", default=False).unsafe_ask():
            return
    except KeyboardInterrupt:
        raise click.Abort() from None
    for pid, name in holders:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            click.echo(f"Not allowed to kill {name} (pid {pid}).", err=True)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(
            pid != os.getpid() for pid, _ in port_holders(port)):
        time.sleep(0.1)


def select_device(baud: int, *, identify: Optional[Identify] = None,
                  message: str = "Select device", probe: Optional[bool] = None,
                  allow_none: bool = False) -> Optional[dict]:
    """Ask the user to pick a device; returns its :func:`usb_record` or :func:`probe_port`
    record, or ``{"port": None}`` if `allow_none` and they chose no device.

    Ports are described from USB alone, so no board is reset. ``p`` connects to the
    highlighted port to identify its board; `probe` does that for every port up front.
    `probe` defaults to whether `identify` is given, since it needs a connection."""
    if not sys.stdin.isatty():
        return None
    if probe is None:
        probe = identify is not None

    while True:
        answer, found = _pick(serial_ports.get_port_list(), baud, probe=probe,
                              identify=identify, message=message, allow_none=allow_none)
        if answer is REFRESH:
            continue
        if answer is NONE:
            return {"port": None}
        if answer is KILL:
            kill_holders(found["port"])
            continue
        if answer is None or answer is QUIT:
            raise click.Abort()
        if answer is MANUAL:
            try:
                typed = questionary.text("Port:").unsafe_ask()
            except KeyboardInterrupt:
                raise click.Abort() from None
            if not typed:
                continue
            with quiet_esptool():
                found = probe_port(typed, baud, identify)
            if found["error"]:
                raise click.ClickException(f"{typed}: {found['error']}")
        click.echo(click.style("Device: ", bold=True) + device_label(found), err=True)
        return found


def prompt_for_port(message: str = "Select port") -> Optional[str]:
    """Ask the user to pick a port without connecting to it."""
    if not sys.stdin.isatty():
        return None

    while True:
        answer, found = _pick(serial_ports.get_port_list(), None, probe=False,
                              message=message, allow_probe=False)
        if answer is REFRESH:
            continue
        if answer is KILL:
            kill_holders(found["port"])
            continue
        if answer is None or answer is QUIT:
            raise click.Abort()
        if answer is MANUAL:
            return questionary.text("Port:").ask() or None
        return answer


def _pick(listed, baud: Optional[int], *, probe: bool, message: str,
          identify: Optional[Identify] = None, allow_probe: bool = True,
          allow_none: bool = False):
    """Show the picker once; returns ``(answer, record)``."""
    from questionary.prompts.common import InquirerControl

    ports = [p.device for p in listed]
    results: dict[str, dict] = {p.device: usb_record(p) for p in listed}
    # Ports being connected to; the rest are only checked for a process holding them.
    to_probe = set(ports) if probe else set()

    choices = [questionary.Choice(title="", value=p) for p in ports]
    if not ports:
        choices.append(questionary.Choice(title=[("class:meta", "no serial ports found")],
                                          value=None, disabled="plug one in"))
    if allow_none:
        choices.append(questionary.Choice(title=[("class:meta", "∅ No device")], value=NONE))
    choices.append(questionary.Choice(title=[("class:meta", "✎ Enter a port manually…")],
                                      value=MANUAL))
    choices.append(questionary.Choice(title=[("class:meta", "↻ Refresh")], value=REFRESH))
    choices.append(questionary.Choice(title=[("class:meta", "✕ Quit")], value=QUIT))

    question = questionary.select(
        message, choices=choices, style=_style(),
        instruction="(↑↓ move · ↵ select"
                    + (" · p probe" if allow_probe else "") + " · r refresh"
                    + (" · k kill" if CAN_KILL else "") + " · q quit)")
    question.application.erase_when_done = True
    control = next(c for c in question.application.layout.find_all_controls()
                   if isinstance(c, InquirerControl))
    rows = dict(zip(ports, control.choices))

    @question.application.key_bindings.add("r", eager=True)
    def _refresh(event):
        event.app.exit(result=REFRESH)

    @question.application.key_bindings.add("q", eager=True)
    def _quit(event):
        event.app.exit(result=QUIT)

    to_kill = {}

    if CAN_KILL:
        @question.application.key_bindings.add("k", eager=True)
        def _kill(event):
            if control.get_pointed_at().value in rows:
                to_kill["port"] = control.get_pointed_at().value
                event.app.exit(result=KILL)

    checked: set[str] = set()

    def repaint() -> None:
        def cells(port: str) -> tuple[str, str, str]:
            _, adapter, what, detail = device_fields(results[port])
            if port in to_probe and port not in checked:
                return adapter, "identifying…", detail
            return adapter, what, detail

        painted = {p: cells(p) for p in ports}
        port_width = max([len(p) for p in ports] or [0])
        adapter_width = max([len(a) for a, _, _ in painted.values()] or [0])
        what_width = max([len(w) for _, w, _ in painted.values()] or [0])
        room = (shutil.get_terminal_size().columns - port_width - adapter_width - what_width
                - 13)
        for port, row in rows.items():
            adapter, what, detail = painted[port]
            failed = port in checked and results[port]["error"]
            pending = port in to_probe and port not in checked
            if len(detail) > room > 1:
                detail = detail[:room - 1] + "…"
            title = [("class:node", f"{port:<{port_width}}")]
            if adapter_width:
                title += [("", "   "),
                          ("class:native" if results[port].get("native") else "class:adapter",
                           f"{adapter:<{adapter_width}}")]
            if what_width:
                title += [("", "   "),
                          ("class:bad" if failed else "class:pending" if pending else "class:chip",
                           f"{what:<{what_width}}")]
            if detail:
                title += [("", "   "), ("class:bad" if failed else "class:detail", detail)]
            row.title = title

    repaint()
    executor = None
    futures: list[concurrent.futures.Future] = []
    if ports:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(ports)))
        latest: dict[str, concurrent.futures.Future] = {}

        def annotate(target: str):
            def done(future):
                if latest.get(target) is not future:
                    return  # superseded by a probe started with `p`
                try:
                    results[target] = {**results[target], **future.result()}
                except Exception as error:  # noqa: BLE001
                    results[target] = {**results[target],
                                       "error": _probe_failure(error, target)}
                checked.add(target)
                repaint()
                with contextlib.suppress(Exception):
                    question.application.invalidate()
            return done

        def submit(task, target: str) -> None:
            future = executor.submit(task, target)
            latest[target] = future
            future.add_done_callback(annotate(target))
            futures.append(future)

        for target in ports:
            if target in to_probe:
                submit(lambda t: probe_port(t, baud, identify), target)
            else:
                submit(lambda t: {"error": held_reason(t)}, target)

        if allow_probe:
            @question.application.key_bindings.add("p", eager=True)
            def _probe(event):
                # Every press probes again (to see if a board is back, say), unless one is
                # already in flight for this port.
                target = control.get_pointed_at().value
                if target in rows and (target in checked or target not in to_probe):
                    to_probe.add(target)
                    checked.discard(target)
                    repaint()
                    submit(lambda t: probe_port(t, baud, identify), target)

    try:
        with quiet_esptool():
            answer = question.unsafe_ask()
    except KeyboardInterrupt:
        raise click.Abort() from None
    finally:
        if executor is not None:
            # Wait for probes to release their ports before the caller connects.
            if not all(f.done() for f in futures):
                click.echo("Waiting for the other ports to finish identifying…", err=True)
            executor.shutdown(wait=True)

    if answer is KILL:
        return KILL, {"port": to_kill["port"]}
    if answer in (REFRESH, MANUAL, QUIT, NONE) or answer is None:
        return answer, None
    chosen = results[answer]
    if chosen["error"]:
        raise click.ClickException(f"{answer}: {chosen['error']}")
    return answer, chosen
