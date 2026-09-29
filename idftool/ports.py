"""Interactive device selection."""
import concurrent.futures
import contextlib
import re
import shlex
import shutil
import signal
import sys
import time
from typing import Callable, Optional

import questionary
import rich_click as click

import esp_pylib.serial_ports as serial_ports

#: Given a connected ``ESPLoader``, returns an object naming the device, or None.
Identify = Callable[["ESPLoader"], object]

REFRESH = "\0refresh"
MANUAL = "\0manual"
QUIT = "\0quit"
KILL = "\0kill"

#: Whether `k` can kill a process holding a port.
CAN_KILL = sys.platform != "win32"


def _style():
    return questionary.Style([
        ("meta", "bold fg:cyan"),
        ("node", "fg:green"),
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


def port_holders(port: str) -> list[tuple[int, str]]:
    """``(pid, command)`` for each process holding `port`, via lsof if available."""
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
            holders.append((pid, line[1:]))
            pid = None
    return holders


def _probe_failure(error: BaseException, port: Optional[str] = None) -> str:
    """A one-line reason a probe failed."""
    import errno
    import os

    from serial import SerialException

    seen = error
    while seen is not None:
        code = getattr(seen, "errno", None)
        if code in (errno.EAGAIN, errno.EBUSY):
            holders = port_holders(port) if port else []
            others = [(pid, name) for pid, name in holders if pid != os.getpid()]
            if others:
                return "held by " + ", ".join(f"{name} (pid {pid})" for pid, name in others)
            if holders:
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


def device_fields(candidate: dict) -> tuple[str, str, str]:
    """``(port, name, detail)`` for display."""
    port = candidate["port"]
    if candidate.get("error"):
        return port, "Unavailable", candidate["error"]
    chip, mac = candidate.get("chip"), candidate.get("mac")
    if chip is None:
        return port, "Unidentified", candidate.get("description") or ""
    if candidate.get("identity") is not None:
        return port, str(candidate["identity"]), " · ".join(filter(None, [chip, mac]))
    if candidate.get("identify_error"):
        return port, chip, " · ".join(filter(None, [
            mac, f"could not identify: {candidate['identify_error']}"]))
    return port, chip, mac or candidate.get("description") or ""


def device_label(candidate: dict, widths: Optional[tuple[int, int]] = None) -> str:
    """A device as one line, padded to `widths` if given."""
    port, what, detail = device_fields(candidate)
    if widths is not None:
        port_width, what_width = widths
        return f"{port:<{port_width}}   {what:<{what_width}}   {detail}".rstrip()
    if not detail:
        return f"{port} — {what}"
    if candidate.get("error"):
        return f"{port} — {what}: {detail}"
    return f"{port} — {what} ({detail})"


def device_widths(candidates: list[dict]) -> tuple[int, int]:
    """Port and name column widths for `candidates`."""
    rows = [device_fields(c) for c in candidates]
    return (max((len(r[0]) for r in rows), default=0),
            max((len(r[1]) for r in rows), default=0))


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


def find_port_for_mac(mac: str, baud: int) -> str:
    """The port of the device with `mac`: from USB serial numbers, else by probing the rest."""
    click.echo(f"Looking for {mac}…", err=True)
    port = usb_port_for_mac(mac) or _probe_for_mac(mac, baud)
    click.echo(click.style("Device: ", bold=True) + port, err=True)
    return port


def _probe_for_mac(mac: str, baud: int) -> str:
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
    raise click.ClickException(f"No device with MAC {mac} found")


def print_rerun_hint(port: str, mac: Optional[str] = None) -> None:
    """Print the command with ``-p`` filled in, and with ``-m`` if the MAC is known."""
    arguments = [shlex.quote(a) for a in sys.argv[1:]]
    click.echo(click.style("Re-run with: ", fg="cyan", bold=True)
               + click.style(" ".join(["idftool", "-p", port, *arguments]), fg="cyan"), err=True)
    if mac:
        click.echo(click.style("         or: ", fg="cyan", bold=True)
                   + click.style(" ".join(["idftool", "-m", mac, *arguments]), fg="cyan"), err=True)


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
                  message: str = "Select device") -> Optional[dict]:
    """Ask the user to pick a device; returns its :func:`probe_port` record."""
    if not sys.stdin.isatty():
        return None

    while True:
        answer, found = _pick(serial_ports.get_port_list(), baud, probe=True,
                              identify=identify, message=message)
        if answer is REFRESH:
            continue
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
        answer, _ = _pick(serial_ports.get_port_list(), None, probe=False, message=message)
        if answer is REFRESH:
            continue
        if answer is None or answer is QUIT:
            raise click.Abort()
        if answer is MANUAL:
            return questionary.text("Port:").ask() or None
        return answer


def _pick(listed, baud: Optional[int], *, probe: bool, message: str,
          identify: Optional[Identify] = None):
    """Show the picker once; returns ``(answer, record)``."""
    from questionary.prompts.common import InquirerControl

    ports = [p.device for p in listed]
    results: dict[str, dict] = {
        p.device: {"port": p.device, "chip": None, "mac": None,
                   "description": p.description, "error": None}
        for p in listed
    }
    pending = "identifying…" if probe else ""

    choices = [questionary.Choice(title="", value=p) for p in ports]
    if not ports:
        choices.append(questionary.Choice(title=[("class:meta", "no serial ports found")],
                                          value=None, disabled="plug one in"))
    choices.append(questionary.Choice(title=[("class:meta", "✎ Enter a port manually…")],
                                      value=MANUAL))
    choices.append(questionary.Choice(title=[("class:meta", "↻ Refresh")], value=REFRESH))
    choices.append(questionary.Choice(title=[("class:meta", "✕ Quit")], value=QUIT))

    question = questionary.select(
        message, choices=choices, style=_style(),
        instruction="(↑↓ move · ↵ select · r refresh"
                    + (" · k kill holder" if probe and CAN_KILL else "") + " · q quit)")
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

    if probe and CAN_KILL:
        @question.application.key_bindings.add("k", eager=True)
        def _kill(event):
            if control.get_pointed_at().value in rows:
                to_kill["port"] = control.get_pointed_at().value
                event.app.exit(result=KILL)

    probed: set[str] = set()

    def repaint() -> None:
        def cells(port: str) -> tuple[str, str]:
            if not probe:
                return results[port]["description"] or "", ""
            if port not in probed:
                return pending, results[port]["description"] or ""
            _, what, detail = device_fields(results[port])
            return what, detail

        painted = {p: cells(p) for p in ports}
        port_width = max([len(p) for p in ports] or [0])
        what_width = max([len(what) for what, _ in painted.values()] or [0])
        room = shutil.get_terminal_size().columns - port_width - what_width - 10
        for port, row in rows.items():
            what, detail = painted[port]
            failed = port in probed and results[port]["error"]
            if len(detail) > room > 1:
                detail = detail[:room - 1] + "…"
            row.title = [
                ("class:node", f"{port:<{port_width}}"),
                ("", "   "),
                ("class:bad" if failed else "class:meta", f"{what:<{what_width}}"),
                ("class:meta", f"   {detail}" if detail else ""),
            ]

    repaint()
    executor = None
    futures: list[concurrent.futures.Future] = []
    if probe and ports:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(ports)))

        def annotate(target: str):
            def done(future):
                try:
                    results[target] = {**results[target], **future.result()}
                except Exception as error:  # noqa: BLE001
                    results[target] = {**results[target],
                                       "error": _probe_failure(error, target)}
                probed.add(target)
                repaint()
                with contextlib.suppress(Exception):
                    question.application.invalidate()
            return done

        for target in ports:
            future = executor.submit(probe_port, target, baud, identify)
            future.add_done_callback(annotate(target))
            futures.append(future)

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
    if answer in (REFRESH, MANUAL, QUIT) or answer is None:
        return answer, None
    chosen = results[answer]
    if chosen["error"]:
        raise click.ClickException(f"{answer}: {chosen['error']}")
    return answer, chosen
