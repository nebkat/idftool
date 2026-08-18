"""Serial port discovery and the interactive port picker."""
import sys
from typing import Optional

import questionary
import rich_click as click

from serial.tools import list_ports
from serial.tools.list_ports_common import ListPortInfo

# TODO: Use esptool version when merged
def get_port_list() -> list[str]:
    """Get the list of serial ports names with optional filters.

    For backwards compatibility, this function returns a list of port names.
    """
    return [port.device for port in _get_port_list()]


def _get_port_list() -> list[ListPortInfo]:
    ports = []
    for port in list_ports.comports():
        if sys.platform == "darwin" and port.device.endswith(
            ("Bluetooth-Incoming-Port", "wlan-debug", "cu.debug-console")
        ):
            continue
        ports.append(port)

    # Constants for sorting optimization
    ESPRESSIF_VID = 0x303A
    LINUX_DEVICE_PATTERNS = ("ttyUSB", "ttyACM")
    MACOS_DEVICE_PATTERNS = ("usbserial", "usbmodem")

    def _port_sort_key_linux(port_info: ListPortInfo) -> tuple[int, str]:
        if port_info.vid == ESPRESSIF_VID:
            return (3, port_info.device)

        if any(pattern in port_info.device for pattern in LINUX_DEVICE_PATTERNS):
            return (2, port_info.device)

        return (1, port_info.device)

    def _port_sort_key_macos(port_info: ListPortInfo) -> tuple[int, str]:
        if port_info.vid == ESPRESSIF_VID:
            return (3, port_info.device)

        if any(pattern in port_info.device for pattern in MACOS_DEVICE_PATTERNS):
            return (2, port_info.device)

        return (1, port_info.device)

    def _port_sort_key_windows(port_info: ListPortInfo) -> tuple[int, str]:
        if port_info.vid == ESPRESSIF_VID:
            return (2, port_info.device)

        return (1, port_info.device)

    if sys.platform == "win32":
        key_func = _port_sort_key_windows
    elif sys.platform == "darwin":
        key_func = _port_sort_key_macos
    else:
        key_func = _port_sort_key_linux

    sorted_port_info = sorted(ports, key=key_func)
    return sorted_port_info


def prompt_for_port() -> Optional[str]:
    """Interactively ask the user to pick a serial port.

    Shows the currently-visible ports (best-guess Espressif device first) plus a
    "type manually" escape hatch, since the target may not be present yet — the
    common enter-bootloader case is a device that hasn't appeared in the list.

    Returns the chosen port device, or None if selection isn't possible (no TTY)
    or the manual entry was left empty — callers should treat None as "no port
    supplied". Raises click.Abort if the user quits or hits Ctrl-C.
    """
    if not sys.stdin.isatty():
        return None

    MANUAL = "\0manual"  # sentinel values that can't collide with a device path
    QUIT = "\0quit"
    # _get_port_list sorts the best-guess (Espressif VID) port last; show it first.
    choices = [
        questionary.Choice(title=f"{p.device}   {p.description}", value=p.device)
        for p in reversed(_get_port_list())
    ]
    choices.append(questionary.Choice(title="Enter a port manually…", value=MANUAL))
    choices.append(questionary.Choice(title="Quit", value=QUIT))

    answer = questionary.select("Select port", choices=choices).ask()
    if answer is None or answer == QUIT:
        raise click.Abort()
    if answer == MANUAL:
        return questionary.text("Port:").ask() or None
    return answer
