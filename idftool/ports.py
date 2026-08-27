"""The interactive serial port picker.

Port discovery itself lives in ``esp_pylib.serial_ports`` — the same
implementation esptool uses. Import ``get_port_list`` (``ListPortInfo``) or
``get_port_names`` (device paths) from there directly rather than through here.
"""
import sys
from typing import Optional

import questionary
import rich_click as click

import esp_pylib.serial_ports as serial_ports


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
    choices = [
        questionary.Choice(title=f"{p.device}   {p.description}", value=p.device)
        for p in serial_ports.get_port_list()
    ]
    choices.append(questionary.Choice(title="Enter a port manually…", value=MANUAL))
    choices.append(questionary.Choice(title="Quit", value=QUIT))

    answer = questionary.select("Select port", choices=choices).ask()
    if answer is None or answer == QUIT:
        raise click.Abort()
    if answer == MANUAL:
        return questionary.text("Port:").ask() or None
    return answer
