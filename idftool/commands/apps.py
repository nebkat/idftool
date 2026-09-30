"""Inspecting an application binary on its own, without a device or a flash image."""
import os.path

import rich_click as click

from esp_idf_defs import ImageMetadata

from idftool.apps import parse_bootloader, print_app_info, print_bootloader
from idftool.cli import cli


def app_info(app_binary_file: str):
    """Print the app description embedded in a bare application binary."""
    if os.path.getsize(app_binary_file) == 0:
        raise RuntimeError(f"Application binary '{app_binary_file}' is empty")

    with open(app_binary_file, 'rb') as f:
        app_binary = f.read()

    try:
        image_metadata = ImageMetadata.from_bytes(app_binary, app_required=True)
    except ValueError as e:
        # No app descriptor: a bootloader is an image too.
        bootloader = parse_bootloader(app_binary)
        if bootloader is None:
            raise RuntimeError(f"Invalid application binary: {e}")
        print(f"File: {app_binary_file} ({len(app_binary):#x} bytes)")
        print_bootloader(bootloader)
        return

    print(f"App: {app_binary_file} ({len(app_binary):#x} bytes)")
    print_app_info(image_metadata.app_description, image_metadata.header)


@cli.command('app-info', aliases=['print-app'],
             help='Print the app description from an application binary')
@click.option('-f', '--file', 'app_binary_file', required=True, help='Application binary to read')
def cmd_app_info(app_binary_file):
    return app_info(app_binary_file)
