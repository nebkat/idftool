"""Discovery and one-off commands: ``devices`` and ``enter-bootloader``."""
import os.path
import sys
import time

import rich_click as click

from esptool.cmds import detect_chip

from esp_pylib.serial_ports import get_port_list

from idftool.cli import cli, pass_state
from idftool.ports import prompt_for_port

def list_devices():
    for d in get_port_list():
        print(f"{d.device} || {d.description} || {d.hwid}")


@cli.command('devices', help='List serial ports with hardware IDs')
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
