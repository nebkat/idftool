import argparse
import os.path
import re
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional, Literal
from zipfile import BadZipFile, ZipFile

import rich_click as click

from esptool import ESPLoader, CHIP_DEFS, flash_size_bytes
from serial import SerialException
from serial.tools import list_ports
from serial.tools.list_ports_common import ListPortInfo

from idftool.traceback import install as install_excepthook, print_exception

from esp_idf_defs import ImageMetadata, ChipId
from esp_idf_defs.otadata import OtaDataParameters, OtaDataSelectEntry, OtaImageState
from esp_idf_defs.partitions import PARTITION_TABLE_SIZE, PARTITION_TABLE_OFFSET, PartitionTable, print_partition_table, \
    PartitionDefinition, BOOTLOADER_TYPE, SUBTYPES, PARTITION_TABLE_TYPE, APP_TYPE, NUM_PARTITION_SUBTYPE_APP_OTA, \
    DATA_TYPE, get_encoding
from esptool.cmds import detect_chip, read_flash, write_flash, erase_region, merge_bin, erase_flash, detect_flash_size

PARTITION_SLICE_REGEX = re.compile(r"^(?P<partition>.+?)(?:\[(?:(?P<start>[-+]?(?:0x)?[0-9A-Fa-f]+)?(?::(?P<stop>[-+]?(?:0x)?[0-9A-Fa-f]+)?)?)?])?$")
PARTITION_OFFSET_REGEX = re.compile(r"^(?P<partition>.+?)(?:\[(?:(?P<start>[-+]?(?:0x)?[0-9A-Fa-f]+)?)?])?$")

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

class BasedIntParamType(click.ParamType):
    """Integer accepting any base via a 0x/0o/0b prefix (like int(x, 0))."""
    name = "integer"

    def convert(self, value, param, ctx):
        if isinstance(value, int):
            return value
        try:
            return int(value, 0)
        except ValueError:
            self.fail(f"{value!r} is not a valid integer", param, ctx)

class BootloaderOffsetParamType(click.ParamType):
    """A flash offset, or a chip name (e.g. esp32s3) resolved to its bootloader offset."""
    name = "offset|chip"

    def convert(self, value, param, ctx):
        if isinstance(value, int):
            return value
        try:
            return int(value, 0)
        except ValueError:
            pass
        chip = CHIP_DEFS.get(value)
        if chip is None:
            self.fail(f"Invalid bootloader offset or chip name: {value}", param, ctx)
        return chip.BOOTLOADER_FLASH_OFFSET

BASED_INT = BasedIntParamType()
BOOTLOADER_OFFSET = BootloaderOffsetParamType()

def get_esp(port: str | None, baud: int) -> ESPLoader:
    esp: ESPLoader | None = None
    if port:
        esp = detect_chip(port, baud=baud)
    else:
        ports = get_port_list()
        for port in ports:
            try:
                print(f"Serial port {port} (baud={baud})")
                esp = detect_chip(port, baud=baud)
                break
            except RuntimeError:
                pass
    if not esp:
        raise RuntimeError("No ESP found")
    return esp.run_stub()

def get_partition(
        partition_table: PartitionTable,
        partition_table_entry: PartitionDefinition,
        bootloader_entry: Optional[PartitionDefinition],
        label: str
) -> PartitionDefinition:
    try:
        try:
            address = int(label, 0)
            return next(
                (p for p in partition_table if p.offset == address),
            )
        except ValueError:
            pass
        return partition_table[label]
    except ValueError:
        if label == partition_table_entry.name:
            return partition_table_entry
        elif bootloader_entry and label == bootloader_entry.name:
            return bootloader_entry
        else:
            raise

def get_partition_slice(
        partition_table: PartitionTable,
        partition_table_entry: PartitionDefinition,
        bootloader_entry: Optional[PartitionDefinition],
        s: str
) -> tuple[PartitionDefinition, int, int]:
    match = PARTITION_SLICE_REGEX.fullmatch(s)
    if not match:
        raise ValueError(f"Invalid partition slice format: {s}")

    partition_name = match.group("partition")
    start_str = match.group("start")
    stop_str = match.group("stop")

    partition = get_partition(partition_table, partition_table_entry, bootloader_entry, partition_name)

    start = int(start_str, 0) if start_str else 0
    if start < 0:
        start += partition.size

    if stop_str is None:
        stop = partition.size
    else:
        stop = int(stop_str, 0)
        if stop_str[0] == '+':
            stop = start + stop
        if stop < 0:
            stop += partition.size

    if start < 0 or stop < 0 or start > partition.size or stop > partition.size or start > stop:
        raise ValueError(f"Invalid slice range [{start:#x}:{stop:#x}] for partition {partition_name} of size {partition.size:#x}")

    return partition, partition.offset + start, stop - start

def get_partition_address(
        partition_table: PartitionTable,
        partition_table_entry: PartitionDefinition,
        bootloader_entry: Optional[PartitionDefinition],
        s: str
) -> tuple[PartitionDefinition, int]:
    match = PARTITION_OFFSET_REGEX.fullmatch(s)
    if not match:
        raise ValueError(f"Invalid partition offset format: {s}")

    partition_name = match.group("partition")
    start_str = match.group("start")

    partition = get_partition(partition_table, partition_table_entry, bootloader_entry, partition_name)

    start = int(start_str, 0) if start_str else 0
    if start < 0:
        start += partition.size

    if start < 0 or start > partition.size:
        raise ValueError(f"Invalid offset [{start:#x}] for partition {partition_name} of size {partition.size:#x}")

    return partition, partition.offset + start

def hexdump(data: bytes, start = 0, width = 16):
    def to_printable_ascii(byte):
        return chr(byte) if 32 <= byte <= 126 else "."

    # If start is not aligned, print an empty row with padding
    misalignment = start % width
    start -= misalignment

    offset = 0
    while offset < len(data):
        chunk = data[offset : offset + width - misalignment]
        hex_values = "-- " * misalignment +  " ".join(f"{byte:02x}" for byte in chunk) + " " + "-- " * (width - len(chunk) - misalignment)
        ascii_values = "-" * misalignment + "".join(to_printable_ascii(byte) for byte in chunk) + "-" * (width - len(chunk) - misalignment)
        print(f"0x{start + offset:08x}  {hex_values} |{ascii_values}|")
        offset += width - misalignment
        misalignment = 0

def read_otadata(esp: ESPLoader, partition_table: PartitionTable) -> tuple[PartitionDefinition, OtaDataParameters]:
    ota_app_count = len(list(
        part for part in partition_table if
        part.type == APP_TYPE and SUBTYPES[APP_TYPE]['ota_0'] <= part.subtype <=
        SUBTYPES[APP_TYPE]['ota_15'] + NUM_PARTITION_SUBTYPE_APP_OTA)
    )
    if ota_app_count == 0:
        raise ValueError("No OTA partitions found") # TODO Better error type

    otadata_partition = next(
        (p for p in partition_table if p.type == DATA_TYPE and p.subtype == SUBTYPES[DATA_TYPE]['ota']),
        None
    )
    if not otadata_partition:
        raise ValueError("No otadata partition found") # TODO Better error type

    otadata_a_bytes = esp.read_flash(otadata_partition.offset, OtaDataSelectEntry.SIZE)
    otadata_b_bytes = esp.read_flash(otadata_partition.offset + esp.FLASH_SECTOR_SIZE, OtaDataSelectEntry.SIZE)
    otadata_a = OtaDataSelectEntry.from_bytes(otadata_a_bytes)
    otadata_b = OtaDataSelectEntry.from_bytes(otadata_b_bytes)

    otadata, a_or_b = OtaDataSelectEntry.select(otadata_a, otadata_b)

    return otadata_partition, OtaDataParameters(otadata, a_or_b, ota_app_count)

def write_otadata(esp: ESPLoader, partition: PartitionDefinition, otadata: OtaDataParameters):
    esp.flash_begin(
        size=OtaDataSelectEntry.SIZE,
        offset=partition.offset + (0 if otadata.a_or_b == 'a' else esp.FLASH_SECTOR_SIZE)
    )
    esp.flash_block(data=otadata.otadata.to_bytes(), seq=0)
    esp.flash_finish()

def check_image_file(path: str, partition_table_offset: int) -> int:
    """Validate an image file is non-empty and large enough to contain a partition table.

    Returns the image size in bytes.
    """
    image_size = os.path.getsize(path)
    if image_size == 0:
        raise RuntimeError(f"Image file '{path}' is empty")
    if image_size <= partition_table_offset:
        raise RuntimeError(
            f"Image file '{path}' ({image_size:#x} bytes) is smaller than the partition table "
            f"offset {partition_table_offset:#x}; it does not appear to contain a partition table")
    return image_size

def require_partitions(partition_table: PartitionTable, source: str) -> PartitionTable:
    """Raise a clear error if a parsed partition table contains no partitions."""
    if len(partition_table) == 0:
        raise RuntimeError(f"Partition table from {source} is empty (no partitions defined)")
    return partition_table

def parse_partition_table_csv(
        csv_text: str,
        source: str,
        partition_table_offset: int,
        primary_bootloader_offset: Optional[int],
        recovery_bootloader_offset: Optional[int],
) -> PartitionTable:
    """Parse partition-table CSV text into a PartitionTable.

    A CSV with bootloader rows can't be parsed without the bootloader offsets. idftool writes those
    offsets into the CSV as literal bootloader rows, so recover them from the text itself when the
    caller didn't supply them — this lets a dumped table (file or bundle) round-trip without
    re-specifying --primary-bootloader-offset. `source` names the origin for error messages.
    """
    if primary_bootloader_offset is None or recovery_bootloader_offset is None:
        csv_primary, csv_recovery = _extract_csv_bootloader_offsets(csv_text)
        if primary_bootloader_offset is None:
            primary_bootloader_offset = csv_primary
        if recovery_bootloader_offset is None:
            recovery_bootloader_offset = csv_recovery
    try:
        partition_table = PartitionTable.from_csv(
            csv_text,
            partition_table_offset=partition_table_offset,
            primary_bootloader_offset=primary_bootloader_offset,
            recovery_bootloader_offset=recovery_bootloader_offset,
        )
    except RuntimeError as e:
        msg = str(e)
        if 'bootloader offset is not provided' in msg.lower():
            which = 'recovery' if 'recovery' in msg.lower() else 'primary'
            flag = ('--primary-bootloader-offset (an address or a chip name, e.g. esp32s3)'
                    if which == 'primary' else '--recovery-bootloader-offset')
            line = re.search(r'line (\d+)', msg)
            where = f" (line {line.group(1)})" if line else ""
            raise RuntimeError(
                f"The {which} bootloader entry in {source}{where} has no offset. "
                f"Add the offset to the CSV, or pass {flag}."
            ) from None
        raise
    return require_partitions(partition_table, source)

def load_partition_table_file(
        path: str,
        partition_table_offset: int,
        primary_bootloader_offset: Optional[int],
        recovery_bootloader_offset: Optional[int],
) -> PartitionTable:
    """Load a partition table from a CSV or binary file (format auto-detected)."""
    if os.path.getsize(path) == 0:
        raise RuntimeError(f"Partition table file '{path}' is empty")
    with open(path, 'rb') as f:
        data = f.read()

    if data[:2] == PartitionDefinition.MAGIC_BYTES:
        return require_partitions(PartitionTable.from_binary(data), f"file '{path}'")

    csv_text = data.decode(get_encoding(data))
    return parse_partition_table_csv(
        csv_text, f"file '{path}'", partition_table_offset,
        primary_bootloader_offset, recovery_bootloader_offset)

def resolve_partition_table_format(output_file: Optional[str], explicit: Optional[str]) -> Literal['csv', 'bin']:
    """Pick the output format from an explicit flag, else the output file extension (default csv)."""
    if explicit:
        return explicit
    if output_file:
        ext = os.path.splitext(output_file)[1].lower()
        if ext == '.csv':
            return 'csv'
        if ext in ('.bin', '.img'):
            return 'bin'
        raise RuntimeError(
            f"Cannot infer partition table format from '{output_file}'; "
            f"pass --format csv|bin or use a .csv/.bin extension")
    return 'csv'

def write_partition_table_file(partition_table: PartitionTable, output_file: str, output_format: Literal['csv', 'bin']):
    """Serialise a partition table to CSV or binary and write it to output_file."""
    if output_format == 'csv':
        data = partition_table.to_csv().encode('utf-8')
    else:
        data = partition_table.to_binary()
    with open(output_file, 'wb') as f:
        f.write(data)
    print(f"Wrote {output_format} partition table ({len(data):#x} bytes) to {output_file}")

def validate_app_binary(esp: ESPLoader, app_binary: bytes) -> tuple[bytes, ImageMetadata]:
    try:
        image_metadata = ImageMetadata.from_bytes(app_binary, app_required=True)
    except ValueError as e:
        raise RuntimeError(f"Invalid application binary: {e}")

    if image_metadata.header.chip_id.value != esp.IMAGE_CHIP_ID:
        raise RuntimeError(
            f"Chip ID mismatch: "
            f"attempting to flash {image_metadata.header.chip_id.name} image "
            f"to {ChipId(esp.IMAGE_CHIP_ID).name} device"
        )

    return app_binary, image_metadata

def load_app_binary(esp: ESPLoader, app_binary_file: str, partition: PartitionDefinition) -> tuple[bytes, ImageMetadata]:
    app_binary_size = os.path.getsize(app_binary_file)
    if app_binary_size == 0:
        raise RuntimeError(f"Application binary '{app_binary_file}' is empty")
    if app_binary_size > partition.size:
        raise RuntimeError(
            f"Application binary size '{app_binary_size}' is greater than partition size {partition.size}")

    app_binary = open(app_binary_file, 'rb').read()
    return validate_app_binary(esp, app_binary)

def check_write_bundle_has_partition_table(file: str) -> bool:
    if os.path.getsize(file) == 0:
        raise RuntimeError(f"Bundle '{file}' is empty")
    try:
        bundle_zip = ZipFile(file, 'r')
    except BadZipFile as e:
        raise RuntimeError(f"Bundle '{file}' is not a valid ZIP archive") from e
    with bundle_zip as zf:
        return any(m == 'partition_table.csv' for m in zf.namelist())

def generate_nvs_image(csv_file: str, size: int) -> bytes:
    import tempfile
    from esp_idf_nvs_partition_gen import nvs_partition_gen

    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        tmp_path = tmp.name
    try:
        nvs_args = argparse.Namespace(
            input=csv_file,
            output=os.path.basename(tmp_path),
            outdir=os.path.dirname(tmp_path),
            size=f"{size:#x}",
            version=2,
        )
        nvs_partition_gen.generate(nvs_args)
        with open(tmp_path, 'rb') as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

def print_app_info(desc, header, indent: str = ""):
    print(f"{indent}Project name:     {desc.project_name}")
    print(f"{indent}Version:          {desc.version}")
    print(f"{indent}IDF version:      {desc.idf_version}")
    print(f"{indent}Secure version:   {desc.secure_version}")
    if desc.compiled:
        print(f"{indent}Compiled:         {desc.compiled}")
    print(f"{indent}ELF SHA256:       {desc.elf_sha256.hex()}")
    max_rev = header.max_chip_rev_full if header.max_chip_rev_full != 0xFFFF else None
    rev_range = f"rev {header.min_chip_rev_full} to {max_rev}" if max_rev else f"rev {header.min_chip_rev_full}+"
    print(f"{indent}Chip:             {header.chip_id.name} ({rev_range})")

def print_partition_table_and_apps(
        partition_table: PartitionTable,
        read: Callable[[int, int], bytes],
):
    print_partition_table(partition_table, read)

    for part in partition_table:
        if part.type != APP_TYPE:
            continue
        app_binary = read(part.offset, part.size)
        try:
            image_metadata = ImageMetadata.from_bytes(app_binary, app_required=True)
        except (RuntimeError, ValueError):
            continue
        print()
        print(f"Partition '{part.name}' (offset={part.offset:#x}):")
        print_app_info(image_metadata.app_description, image_metadata.header, indent="  ")

def _extract_csv_bootloader_offsets(csv_text: str) -> tuple[Optional[int], Optional[int]]:
    primary = recovery = None
    for line in csv_text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 4 or parts[1] != 'bootloader':
            continue
        try:
            offset = int(parts[3], 0)
        except ValueError:
            continue
        if parts[2] == 'primary' and primary is None:
            primary = offset
        elif parts[2] == 'recovery' and recovery is None:
            recovery = offset
    return primary, recovery

@dataclass
class Loaded:
    """What State.setup() produces: the (optional) device connection, the loaded partition table,
    and the virtual partition-table/bootloader entries used to resolve partitions by name."""
    esp: Optional[ESPLoader]
    partition_table: PartitionTable
    partition_table_entry: PartitionDefinition
    bootloader_entry: Optional[PartitionDefinition]


class State:
    """Holds the global options and the (lazily-established) device connection."""

    def __init__(self, *, port, baud, no_reset, partition_table_file, partition_table_offset,
                 partition_table_size, primary_bootloader_offset, recovery_bootloader_offset):
        self.port = port
        self.baud = baud
        self.no_reset = no_reset
        self.partition_table_file = partition_table_file
        self.partition_table_offset = partition_table_offset
        self.partition_table_size = partition_table_size
        self.primary_bootloader_offset = primary_bootloader_offset
        self.recovery_bootloader_offset = recovery_bootloader_offset
        self.esp: Optional[ESPLoader] = None

    def connect(self) -> ESPLoader:
        if self.esp is None:
            self.esp = get_esp(port=self.port, baud=self.baud)
            # Fall back to the connected chip's bootloader offset only when the user didn't set one
            # explicitly, keeping the precedence: explicit CLI > chip default > value present in CSV.
            if self.primary_bootloader_offset is None:
                self.primary_bootloader_offset = self.esp.BOOTLOADER_FLASH_OFFSET
        return self.esp

    def _load_partition_table(self, image_file, bundle_file) -> PartitionTable:
        # Load from the most specific source available, matching the original precedence:
        # image file > --partition-table-file > bundle CSV > the connected device.
        if image_file is not None:
            check_image_file(image_file, self.partition_table_offset)
            with open(image_file, 'rb') as f:
                f.seek(self.partition_table_offset)
                partition_table = PartitionTable.from_binary(f.read(self.partition_table_size))
        elif self.partition_table_file:
            partition_table = load_partition_table_file(
                self.partition_table_file,
                partition_table_offset=self.partition_table_offset,
                primary_bootloader_offset=self.primary_bootloader_offset,
                recovery_bootloader_offset=self.recovery_bootloader_offset,
            )
        elif bundle_file is not None and check_write_bundle_has_partition_table(bundle_file):
            with ZipFile(bundle_file, 'r') as tar:
                partition_table_csv = tar.read('partition_table.csv')
                if not partition_table_csv:
                    raise RuntimeError("Partition table could not be loaded from bundle")
                partition_table = parse_partition_table_csv(
                    partition_table_csv.decode('utf-8'), f"bundle '{bundle_file}'",
                    self.partition_table_offset, self.primary_bootloader_offset,
                    self.recovery_bootloader_offset,
                )
        else:
            try:
                binary = self.esp.read_flash(offset=self.partition_table_offset, length=self.partition_table_size)
                partition_table = PartitionTable.from_binary(binary)
            except RuntimeError as e:
                raise RuntimeError("Partition table could not be loaded") from e
        return require_partitions(partition_table, "the loaded source")

    def setup(self, *, needs_device=True, image_file=None, bundle_file=None) -> 'Loaded':
        """Connect if required, load and print the partition table, and build the virtual
        partition-table/bootloader entries the command handlers expect.

        Returns a ``Loaded`` whose ``esp`` is ``None`` for commands that ran entirely from a file.
        """
        table_from_file = (
            image_file is not None
            or bool(self.partition_table_file)
            or (bundle_file is not None and check_write_bundle_has_partition_table(bundle_file))
        )
        if needs_device or not table_from_file:
            self.connect()
        esp = self.esp

        partition_table = self._load_partition_table(image_file, bundle_file)

        # Read otadata so the printed table can mark the active app partition
        otadata_params: Optional[OtaDataParameters] = None
        if esp:
            try:
                _, otadata_params = read_otadata(esp, partition_table)
            except ValueError:
                pass

        print_partition_table(partition_table, esp.read_flash if esp else None, otadata=otadata_params)

        if esp:
            partition_table.verify_size_fits(flash_size_bytes(detect_flash_size(esp)))

        # Add a virtual partition table entry if not present
        partition_table_entry = next(
            (e for e in partition_table if e.type == PARTITION_TABLE_TYPE and e.subtype == SUBTYPES[e.type]['primary']),
            PartitionDefinition.default_partition_table(
                offset=self.partition_table_offset,
                size=self.partition_table_size,
            )
        )

        # Add a virtual bootloader partition if not present and a bootloader offset is known
        bootloader_entry = next(
            (e for e in partition_table if e.type == BOOTLOADER_TYPE and e.subtype == SUBTYPES[e.type]['primary']),
            PartitionDefinition.default_bootloader(
                offset=self.primary_bootloader_offset,
                size=self.partition_table_offset - self.primary_bootloader_offset,
            ) if self.primary_bootloader_offset is not None else None
        )

        return Loaded(esp, partition_table, partition_table_entry, bootloader_entry)


pass_state = click.make_pass_decorator(State)

CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])

# esptool-style help: rich-click already renders options/commands in boxed panels; group the
# commands into labelled panels (matching the README) instead of one long list. Command aliases
# (reflash, list) are declared natively via `aliases=` on the commands and shown by rich-click.
# Show the alias in the same column as the name ("name_with_aliases") rather than a separate
# column, which otherwise squeezes the name column down to an ellipsis.
click.rich_click.COMMANDS_TABLE_COLUMN_TYPES = ['name_with_aliases', 'help']
click.rich_click.COMMAND_GROUPS = {
    '*': [
        {'name': 'Discovery', 'commands': ['devices', 'print-table']},
        {'name': 'Partition I/O', 'commands': ['read', 'write', 'erase', 'view']},
        {'name': 'Firmware', 'commands': ['ota', 'factory']},
        {'name': 'Boot selection', 'commands': ['get-boot', 'set-boot', 'clear-boot']},
        {'name': 'Images', 'commands': ['create-image', 'dump-image', 'write-image', 'print-image']},
        {'name': 'Bundles', 'commands': ['create-bundle', 'dump-bundle', 'write-bundle', 'print-bundle']},
        {'name': 'Partition table', 'commands': ['convert-table', 'dump-table', 'write-table']},
        {'name': 'NVS', 'commands': ['create-nvs', 'write-nvs']},
        {'name': 'Misc', 'commands': ['enter-bootloader']},
    ],
}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('-p', '--port', default=None, help='Serial port device')
@click.option('-b', '--baud', type=int, default=ESPLoader.ESP_ROM_BAUD, show_default=True, help='Serial port baud rate')
@click.option('--no-reset', is_flag=True, help='Do not reset the chip after operations')
@click.option('--partition-table-file', default=None,
              help='Path to a partition table CSV or binary file to use instead of reading from the device')
@click.option('--partition-table-offset', type=BASED_INT, default=PARTITION_TABLE_OFFSET, show_default=True,
              help='Partition table offset (where to read the table from flash, or place it when loading from CSV)')
@click.option('--partition-table-size', type=BASED_INT, default=PARTITION_TABLE_SIZE, show_default=True,
              help='Partition table size')
@click.option('--primary-bootloader-offset', type=BOOTLOADER_OFFSET, default=None,
              help='Primary bootloader offset or chip type e.g. esp32s3 (used when loading a table from CSV)')
@click.option('--recovery-bootloader-offset', type=BASED_INT, default=None,
              help='Recovery bootloader offset (used when loading a table from CSV)')
@click.pass_context
def cli(ctx, port, baud, no_reset, partition_table_file, partition_table_offset,
        partition_table_size, primary_bootloader_offset, recovery_bootloader_offset):
    """Utility for flashing, provisioning, and interacting with Espressif SOCs running ESP-IDF."""
    ctx.obj = State(
        port=port,
        baud=baud,
        no_reset=no_reset,
        partition_table_file=partition_table_file,
        partition_table_offset=partition_table_offset,
        partition_table_size=partition_table_size,
        primary_bootloader_offset=primary_bootloader_offset,
        recovery_bootloader_offset=recovery_bootloader_offset,
    )


@cli.result_callback()
@pass_state
def _reset_after_command(state, result, **_global_options):
    # Runs only after a command succeeds (a command that raises propagates past this), so the chip
    # is reset only on success. Offline commands never connect, so state.esp stays None.
    if state.esp is not None and not state.no_reset:
        state.esp.hard_reset()


@cli.command('devices', help='List serial ports with hardware IDs')
def cmd_devices():
    for d in _get_port_list():
        print(f"{d.device} || {d.description} || {d.hwid}")


@cli.command('read', help='Read a partition (or slice) into a file')
@click.argument('partition')
@click.argument('output_file')
@pass_state
def cmd_read(state, partition, output_file):
    loaded = state.setup()
    partition, address, length = get_partition_slice(
        loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition)
    print(f"Reading partition {partition.name} (offset={address:#x}, size={length:#x})")
    read_flash(loaded.esp, address=address, size=length, output=output_file)


@cli.command('write', help='Write one or more files to named partitions')
@click.argument('files', nargs=-1, required=True, metavar='PARTITION FILENAME ...')
@pass_state
def cmd_write(state, files):
    loaded = state.setup()
    files = list(files)
    if len(files) % 2 != 0:
        raise ValueError("Files list must contain pairs of partition name and input file")

    addr_data = []
    for partition_name, input_file in list(zip(files[::2], files[1::2])):
        partition, address = get_partition_address(
            loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition_name)
        input_file_size = os.path.getsize(input_file)
        if input_file_size > partition.size:
            raise ValueError(f"Input file {input_file} size {input_file_size:#x} exceeds partition {partition.name} size {partition.size:#x}")
        addr_data.append((address, input_file))
        print(f"Writing file {input_file} (size={input_file_size:#x}) to partition {partition.name} (offset={address:#x}, size={partition.size:#x})")

    write_flash(
        esp=loaded.esp,
        addr_data=addr_data,
        flash_size='detect'
    )


@cli.command('erase', help='Erase a partition (or slice)')
@click.argument('partition')
@pass_state
def cmd_erase(state, partition):
    loaded = state.setup()
    partition, address, length = get_partition_slice(
        loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition)
    print(f"Erasing partition {partition.name} (offset={address:#x}, size={length:#x})")
    erase_region(loaded.esp, address=address, size=length)


@cli.command('view', help="Pretty-print a partition's contents")
@click.argument('partition')
@click.option('-w', '--width', type=int, default=16, show_default=True, help='Width of the hexdump')
@click.option('--hex', 'output', flag_value='hex', default=True, help='Output as a hex dump')
@click.option('-s', '--string', 'output', flag_value='str', help='Output as a string')
@pass_state
def cmd_view(state, partition, width, output):
    loaded = state.setup()
    partition, address, length = get_partition_slice(
        loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition)
    print(f"Viewing partition {partition.name} (offset={address:#x}, size={length:#x})")
    buffer = loaded.esp.read_flash(offset=address, length=length)
    if output == 'str':
        print(buffer.decode('utf-8', errors='replace'), flush=True)
    elif output == 'hex':
        hexdump(buffer, address - partition.offset, width)
    else:
        raise ValueError(f"Invalid output type {output}")


@cli.command('create-image', help='Merge partition binaries into a single flash image')
@click.option('-o', '--output', 'output_file', required=True, help='Output filename')
@click.option('-f', '--format', 'output_format', type=click.Choice(['raw', 'uf2', 'hex']),
              default='raw', show_default=True, help='Output format')
@click.option('--flash-partition-table', is_flag=True, help='Include the partition table in the image')
@click.argument('files', nargs=-1, required=True, metavar='PARTITION FILENAME ...')
@pass_state
def cmd_create_image(state, output_file, output_format, flash_partition_table, files):
    loaded = state.setup(needs_device=False)
    files = list(files)
    if len(files) % 2 != 0:
        raise ValueError("Files list must contain pairs of partition name and input file")

    addr_data = []
    for partition_name, input_file in list(zip(files[::2], files[1::2])):
        partition, address = get_partition_address(
            loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition_name)
        input_file_size = os.path.getsize(input_file)
        if input_file_size > partition.size:
            raise ValueError(
                f"Input file {input_file} size {input_file_size:#x} exceeds partition {partition.name} size {partition.size:#x}")
        addr_data.append((address, input_file))
        print(f"Merging file {input_file} (size={input_file_size:#x}) to partition {partition.name} (offset={address:#x}, size={partition.size:#x})")

    # Add partition table if requested
    if flash_partition_table:
        partition_table_binary = loaded.partition_table.to_binary()
        addr_data.append((loaded.partition_table_entry.offset, partition_table_binary))

    merge_bin(
        addr_data=addr_data,
        flash_freq="keep",
        flash_mode="keep",
        flash_size="keep",
        chip='esp32', # Does not matter since we are keeping all flash parameters
        output=output_file,
        output_format=output_format,
        partition_table=loaded.partition_table
    )


@cli.command('dump-image', help='Dump the entire flash to an image file')
@click.argument('output_file', required=False)
@pass_state
def cmd_dump_image(state, output_file):
    esp = state.connect()
    flash_size = flash_size_bytes(detect_flash_size(esp))

    if not output_file:
        chip = esp.CHIP_NAME.lower().replace(' ', '-')
        try:
            mac = esp.read_mac("BASE_MAC")
            serial = ''.join(f"{b:02x}" for b in mac)
        except Exception:
            serial = "unknown"
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        output_file = f"{chip}-{serial}-{timestamp}.img"

    print(f"Dumping {flash_size:#x} bytes of flash to {output_file}...")
    read_flash(esp, address=0, size=flash_size, output=output_file)


@cli.command('write-image', aliases=['reflash'], help='Write a full flash image to the device')
@click.argument('image_file')
@pass_state
def cmd_write_image(state, image_file):
    loaded = state.setup(image_file=image_file)
    esp = loaded.esp
    if os.path.getsize(image_file) == 0:
        raise RuntimeError(f"Image file '{image_file}' is empty")
    f = open(image_file, 'rb')
    f.seek(loaded.bootloader_entry.offset)
    bootloader_binary = f.read(loaded.bootloader_entry.size)
    try:
        bootloader_image_metadata = ImageMetadata.from_bytes(bootloader_binary)
    except (RuntimeError, ValueError) as e:
        raise RuntimeError("Invalid bootloader in image, check the input file (chip type may be incorrect)")\
            from e

    if bootloader_image_metadata.header.chip_id.value != esp.IMAGE_CHIP_ID:
        raise RuntimeError(
            f"Chip ID mismatch: "
            f"attempting to flash {bootloader_image_metadata.header.chip_id.name} image "
            f"to {ChipId(esp.IMAGE_CHIP_ID).name} device"
        )

    with open(image_file, 'rb') as image_f:
        image_f.seek(esp.BOOTLOADER_FLASH_OFFSET)
        image_data = image_f.read()

    print(f"Writing image '{image_file}'...")
    erase_flash(esp)
    write_flash(
        esp=esp,
        addr_data=[(esp.BOOTLOADER_FLASH_OFFSET, image_data)],
        flash_size='detect',
    )


@cli.command('print-image', help='Print partition table and app info from a flash image file')
@click.argument('image_file')
@pass_state
def cmd_print_image(state, image_file):
    image_size = check_image_file(image_file, state.partition_table_offset)
    with open(image_file, 'rb') as f:
        f.seek(state.partition_table_offset)
        partition_table_binary = f.read(state.partition_table_size)
        try:
            partition_table = PartitionTable.from_binary(partition_table_binary)
        except (RuntimeError, ValueError) as e:
            raise RuntimeError(f"Could not parse partition table at offset {state.partition_table_offset:#x}") from e
        require_partitions(partition_table, f"image at offset {state.partition_table_offset:#x}")

        def read(offset: int, length: int) -> bytes:
            if offset >= image_size:
                return b"\xff" * length
            f.seek(offset)
            data = f.read(length)
            if len(data) < length:
                data += b"\xff" * (length - len(data))
            return data

        print(f"Image: {image_file} ({image_size:#x} bytes)")
        print()
        print_partition_table_and_apps(partition_table, read)


@cli.command('create-bundle', help='Pack partition images into a ZIP bundle')
@click.option('-o', '--output', 'output_file', required=True, help='Output ZIP filename')
@click.option('--flash-partition-table', is_flag=True, help='Include the partition table in the bundle')
@click.argument('files', nargs=-1, required=True, metavar='PARTITION FILENAME ...')
@pass_state
def cmd_create_bundle(state, output_file, flash_partition_table, files):
    loaded = state.setup(needs_device=False)
    files = list(files)
    if len(files) % 2 != 0:
        raise ValueError("Files list must contain pairs of partition name and input file")

    with ZipFile(output_file, 'w') as zf:
        # Add specified partition files
        for partition_name, input_file in list(zip(files[::2], files[1::2])):
            partition = get_partition(
                loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition_name)
            input_file_size = os.path.getsize(input_file)
            if input_file_size > partition.size:
                raise ValueError(
                    f"Input file {input_file} size {input_file_size:#x} exceeds partition {partition.name} size {partition.size:#x}")
            zf.write(input_file, arcname=f"{partition.name}.bin")
            print(f"Adding file {input_file} (size={input_file_size:#x}) to bundle as partition {partition.name} (offset={partition.offset:#x}, size={partition.size:#x})")

        # Add partition table if requested
        if flash_partition_table:
            zf.writestr("partition_table.csv", loaded.partition_table.to_csv())
            print(f"Adding partition table CSV to bundle")


@cli.command('dump-bundle', help='Pack every partition from the device into a ZIP')
@click.argument('output_file', required=False)
@pass_state
def cmd_dump_bundle(state, output_file):
    loaded = state.setup()
    esp, partition_table = loaded.esp, loaded.partition_table
    if not output_file:
        chip = esp.CHIP_NAME.lower().replace(' ', '-')
        try:
            mac = esp.read_mac("BASE_MAC")
            serial = ''.join(f"{b:02x}" for b in mac)
        except Exception:
            serial = "unknown"
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        output_file = f"{chip}-{serial}-{timestamp}.zip"

    with ZipFile(output_file, 'w') as zf:
        for partition in partition_table:
            print(f"Reading partition {partition.name} (offset={partition.offset:#x}, size={partition.size:#x})")
            data = esp.read_flash(partition.offset, partition.size)
            zf.writestr(f"{partition.name}.bin", data)
        zf.writestr("partition_table.csv", partition_table.to_csv())
        print(f"Adding partition table CSV to bundle")

    print(f"Bundle written to {output_file}")


@cli.command('write-bundle', help='Flash every binary in a bundle ZIP')
@click.argument('input_file')
@pass_state
def cmd_write_bundle(state, input_file):
    loaded = state.setup(bundle_file=input_file)
    esp, partition_table = loaded.esp, loaded.partition_table
    with ZipFile(input_file, 'r') as zf:
        # Flash files
        addr_data = []
        for member in filter(lambda m: m.endswith('.bin'), zf.namelist()):
            partition_name = member[:-4]  # Remove .bin extension
            partition = get_partition(
                partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition_name)
            data = zf.read(member)
            if len(data) > partition.size:
                raise ValueError(f"Bundle entry {member} size {len(data):#x} exceeds partition {partition.name} size {partition.size:#x}")

            if partition.type == APP_TYPE:
                validate_app_binary(esp, data)

            print(f"Writing partition {partition.name} (offset={partition.offset:#x}, size={partition.size:#x}) from bundle")
            addr_data.append((partition.offset, data))

        # Flash partition table if present
        if any(m == 'partition_table.csv' for m in zf.namelist()):
            partition = next(
                (e for e in partition_table if e.type == PARTITION_TABLE_TYPE and e.subtype == SUBTYPES[e.type]['primary'])
            )
            print(f"Writing partition table (offset={partition.offset:#x}, size={partition.size:#x}) from bundle")
            addr_data.append((partition.offset, partition_table.to_binary()))

        # Perform write
        write_flash(
            esp=esp,
            addr_data=addr_data,
            flash_size='detect',
        )


@cli.command('print-bundle', help='Print partition table and app info from a bundle ZIP')
@click.argument('bundle_file')
@pass_state
def cmd_print_bundle(state, bundle_file):
    bundle_size = os.path.getsize(bundle_file)
    if bundle_size == 0:
        raise RuntimeError(f"Bundle '{bundle_file}' is empty")
    try:
        bundle_zip = ZipFile(bundle_file, 'r')
    except BadZipFile as e:
        raise RuntimeError(f"Bundle '{bundle_file}' is not a valid ZIP archive") from e
    with bundle_zip as zf:
        names = zf.namelist()
        if 'partition_table.csv' not in names:
            raise RuntimeError(f"Bundle {bundle_file} does not contain a partition_table.csv")
        csv_text = zf.read('partition_table.csv').decode('utf-8')
        if not csv_text.strip():
            raise RuntimeError(f"Bundle '{bundle_file}' contains an empty partition_table.csv")

        partition_table = parse_partition_table_csv(
            csv_text, f"bundle '{bundle_file}'", state.partition_table_offset,
            state.primary_bootloader_offset, state.recovery_bootloader_offset)

        partition_data = {
            name[:-4]: zf.read(name)
            for name in names if name.endswith('.bin')
        }

        def read(offset: int, length: int) -> bytes:
            for part in partition_table:
                if part.offset <= offset < part.offset + part.size:
                    local = offset - part.offset
                    data = partition_data.get(part.name, b"")
                    chunk = data[local:local + length]
                    if len(chunk) < length:
                        chunk += b"\xff" * (length - len(chunk))
                    return chunk
            return b"\xff" * length

        included = sorted(partition_data.keys())
        print(f"Bundle: {bundle_file} ({bundle_size:#x} bytes)")
        print(f"Partitions included: {', '.join(included) if included else '(none)'}")
        print()
        print_partition_table_and_apps(partition_table, read)


@cli.command('print-table', aliases=['list'], help='Print a partition table from a CSV or binary file, or from the device')
@click.argument('table_file', required=False)
@pass_state
def cmd_print_table(state, table_file):
    if table_file:
        partition_table = load_partition_table_file(
            table_file, state.partition_table_offset,
            state.primary_bootloader_offset, state.recovery_bootloader_offset)
        print(f"Partition table: {table_file}")
        print()
        print_partition_table(partition_table)
        return
    # No file: fall through to the shared loader, whose printout is the output (like the device table)
    state.setup(needs_device=False)


@cli.command('convert-table', aliases=['create-table'], help='Convert a partition table file between CSV and binary')
@click.argument('input_file')
@click.argument('output_file')
@click.option('-f', '--format', 'output_format', type=click.Choice(['csv', 'bin']), default=None,
              help='Output format (default: inferred from the output extension)')
@pass_state
def cmd_convert_table(state, input_file, output_file, output_format):
    partition_table = load_partition_table_file(
        input_file, state.partition_table_offset,
        state.primary_bootloader_offset, state.recovery_bootloader_offset)
    output_format = resolve_partition_table_format(output_file, output_format)
    write_partition_table_file(partition_table, output_file, output_format)


@cli.command('dump-table', help='Read the partition table from the device into a file')
@click.argument('output_file', required=False)
@click.option('-f', '--format', 'output_format', type=click.Choice(['csv', 'bin']), default=None,
              help='Output format (default: inferred from the output extension, else csv)')
@pass_state
def cmd_dump_table(state, output_file, output_format):
    loaded = state.setup()
    if not output_file:
        esp = loaded.esp
        chip = esp.CHIP_NAME.lower().replace(' ', '-')
        try:
            mac = esp.read_mac("BASE_MAC")
            serial = ''.join(f"{b:02x}" for b in mac)
        except Exception:
            serial = "unknown"
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        extension = 'bin' if output_format == 'bin' else 'csv'
        output_file = f"{chip}-{serial}-{timestamp}-partition-table.{extension}"
    output_format = resolve_partition_table_format(output_file, output_format)
    write_partition_table_file(loaded.partition_table, output_file, output_format)


@cli.command('write-table', help='Flash a partition table from a CSV or binary file to the device')
@click.argument('table_file')
@click.option('--force', is_flag=True, help='Flash even if the partition table fails verification')
@pass_state
def cmd_write_table(state, table_file, force):
    esp = state.connect()
    partition_table = load_partition_table_file(
        table_file, state.partition_table_offset,
        state.primary_bootloader_offset, state.recovery_bootloader_offset)

    try:
        partition_table.verify(partition_table_offset=state.partition_table_offset)
    except RuntimeError as e:
        if not force:
            raise RuntimeError(
                f"Partition table failed verification: {e}. Re-run with --force to flash it anyway.") from e
        print(f"Warning: partition table failed verification: {e} (continuing due to --force)")

    partition_table.verify_size_fits(flash_size_bytes(detect_flash_size(esp)))

    binary = partition_table.to_binary()
    print_partition_table(partition_table)
    print()
    print(f"Writing partition table ({len(binary):#x} bytes) to offset {state.partition_table_offset:#x}...")
    print("Note: this replaces only the partition map; existing partition data on flash is not moved, "
          "resized, or erased. A table that no longer matches the flash contents can make the device unbootable.")
    write_flash(esp=esp, addr_data=[(state.partition_table_offset, binary)], flash_size='detect')
    print("Partition table written")


@cli.command('create-nvs', help='Generate an NVS partition image from a CSV file')
@click.argument('csv_file')
@click.option('-o', '--output', 'output_file', required=True, help='Output binary filename (.bin)')
@click.option('--size', type=BASED_INT, default=None, help='Partition size in bytes (e.g. 0x6000)')
@click.option('--partition', default=None, help='Partition name to read the size from the partition table')
@pass_state
def cmd_create_nvs(state, csv_file, output_file, size, partition):
    if (size is None) == (partition is None):
        raise click.UsageError("Provide exactly one of --size or --partition")
    if partition is not None:
        loaded = state.setup(needs_device=False)
        size = get_partition(
            loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition).size

    print(f"Generating NVS image from '{csv_file}' (size={size:#x})...")
    image = generate_nvs_image(csv_file, size)
    with open(output_file, 'wb') as f:
        f.write(image)
    print(f"Wrote {len(image):#x} bytes to '{output_file}'")


@cli.command('write-nvs', help='Generate an NVS image from CSV and flash it')
@click.argument('partition')
@click.argument('csv_file')
@pass_state
def cmd_write_nvs(state, partition, csv_file):
    loaded = state.setup()
    partition = get_partition(
        loaded.partition_table, loaded.partition_table_entry, loaded.bootloader_entry, partition)
    print(f"Generating NVS image from '{csv_file}' for partition '{partition.name}' (size={partition.size:#x})...")
    image = generate_nvs_image(csv_file, partition.size)
    print(f"Writing NVS image to partition '{partition.name}' (offset={partition.offset:#x}, size={partition.size:#x})")
    write_flash(esp=loaded.esp, addr_data=[(partition.offset, image)], flash_size='detect')


@cli.command('factory', help='Flash an app to the factory partition')
@click.argument('app_binary_file')
@pass_state
def cmd_factory(state, app_binary_file):
    loaded = state.setup()
    esp, partition_table = loaded.esp, loaded.partition_table
    partition = next(
        (part for part in partition_table if part.type == APP_TYPE and part.subtype == SUBTYPES[APP_TYPE]['factory']),
        None
    )
    if not partition:
        partition = next(
            (part for part in partition_table if part.type == APP_TYPE and part.subtype == SUBTYPES[APP_TYPE]['ota_0']),
            None
        )
    if not partition:
        raise ValueError("No factory or OTA partition found")

    app_binary, image_metadata = load_app_binary(esp, app_binary_file, partition)

    print(f"Writing '{image_metadata.app_description.title}' to partition '{partition.name}'...")
    write_flash(esp=esp, addr_data=[(partition.offset, app_binary)])

    otadata_partition = next(
        (p for p in partition_table if p.type == DATA_TYPE and p.subtype == SUBTYPES[DATA_TYPE]['ota']),
        None
    )
    if otadata_partition:
        print("Erasing 'otadata' partition...")
        esp.erase_region(offset=otadata_partition.offset, size=otadata_partition.size)


@cli.command('ota', help='Push an app to the next OTA slot and switch to it')
@click.argument('app_binary_file')
@pass_state
def cmd_ota(state, app_binary_file):
    loaded = state.setup()
    esp, partition_table = loaded.esp, loaded.partition_table
    otadata_partition, otadata = read_otadata(esp, partition_table)

    next_slot = otadata.next_slot
    partition = next(
        (p for p in partition_table if p.type == APP_TYPE and p.subtype == SUBTYPES[APP_TYPE]['ota_0'] + next_slot),
        None
    )
    if not partition:
        raise ValueError(f"Partition ota_{next_slot} not found")

    app_binary, image_metadata = load_app_binary(esp, app_binary_file, partition)

    print(f"Writing '{image_metadata.app_description.title}' to partition '{partition.name}'...")
    write_flash(esp=esp, addr_data=[(partition.offset, app_binary)])

    print(f"Setting boot partition to 'ota_{next_slot}'...")
    otadata = otadata.incremented_and_swapped(next_slot)
    write_otadata(esp, otadata_partition, otadata)


@cli.command('get-boot', help='Show the currently-active OTA slot')
@pass_state
def cmd_get_boot(state):
    loaded = state.setup()
    _, otadata = read_otadata(loaded.esp, loaded.partition_table)

    if otadata.slot is None:
        print("OTA slot not set")
    else:
        print(f"OTA slot 'ota_{otadata.slot}' (seq={otadata.otadata.seq}, state={otadata.otadata.ota_state.name})")


@cli.command('set-boot', help='Force the next boot to a specific OTA partition')
@click.argument('partition')
@pass_state
def cmd_set_boot(state, partition):
    loaded = state.setup()
    otadata_partition, otadata = read_otadata(loaded.esp, loaded.partition_table)

    label = partition
    partition = loaded.partition_table[label]
    if partition.type != APP_TYPE:
        raise ValueError(f"Partition {label} is not an app partition")
    if partition.subtype < SUBTYPES[APP_TYPE]['ota_0'] or partition.subtype > SUBTYPES[APP_TYPE]['ota_15']:
        raise ValueError(f"Partition {label} is not an OTA partition")
    ota_slot = partition.subtype & 0x0F

    print(f"Setting boot partition to '{partition.name}'...")
    otadata = otadata.incremented_and_swapped(ota_slot)
    otadata.otadata.ota_state = OtaImageState.VALID
    write_otadata(loaded.esp, otadata_partition, otadata)


@cli.command('clear-boot', help='Erase otadata and let the bootloader fall back')
@pass_state
def cmd_clear_boot(state):
    loaded = state.setup()
    otadata_partition = next(
        (p for p in loaded.partition_table if p.type == DATA_TYPE and p.subtype == SUBTYPES[DATA_TYPE]['ota']),
        None
    )
    if not otadata_partition:
        raise ValueError("No otadata partition found") # TODO Better error type

    print("Clearing boot partition...")
    loaded.esp.erase_region(offset=otadata_partition.offset, size=otadata_partition.size)


@cli.command('enter-bootloader', help='Fast-poll the serial port and drop the chip into ROM bootloader '
                                      'mode as soon as it appears, then exit without resetting')
@pass_state
def cmd_enter_bootloader(state):
    if not state.port:
        raise click.UsageError("enter-bootloader requires -p/--port")
    port, baud, poll_interval = state.port, state.baud, 0.05
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


def _main():
    install_excepthook()
    try:
        cli.main(args=sys.argv[1:], standalone_mode=False)
    except click.exceptions.Abort:
        sys.exit(130)
    except click.ClickException as e:
        e.show()
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        sys.exit(130)
    except SystemExit:
        # click/rich-click already handled it (e.g. --help, no-args help, usage errors that
        # sys.exit directly); just let it carry its exit code out.
        raise
    except BaseException as e:
        print_exception(e)
        sys.exit(1)


if __name__ == "__main__":
    _main()
