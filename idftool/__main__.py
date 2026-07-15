import argparse
import os.path
import re
import sys
import time
from typing import Callable, Optional, Literal
from zipfile import BadZipFile, ZipFile

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

def parse_bootloader_offset(x):
    try:
        return int(x, 0)
    except ValueError:
        if CHIP_DEFS[x] is None:
            raise argparse.ArgumentTypeError(f"Invalid bootloader offset or chip name: {x}")
        return CHIP_DEFS[x].BOOTLOADER_FLASH_OFFSET

def auto_int(x):
    return int(x, 0)

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
        partition_table = PartitionTable.from_binary(data)
    else:
        csv_text = data.decode(get_encoding(data))
        # A CSV with bootloader rows can't be parsed without the bootloader offsets. idftool writes
        # those offsets into the CSV as literal bootloader rows, so recover them from the file itself
        # when the user didn't pass them — this lets a dumped table round-trip without re-specifying
        # --primary-bootloader-offset. (print-bundle does the same for bundle CSVs.)
        if primary_bootloader_offset is None or recovery_bootloader_offset is None:
            csv_primary, csv_recovery = _extract_csv_bootloader_offsets(csv_text)
            if primary_bootloader_offset is None:
                primary_bootloader_offset = csv_primary
            if recovery_bootloader_offset is None:
                recovery_bootloader_offset = csv_recovery
        partition_table = PartitionTable.from_csv(
            csv_text,
            partition_table_offset=partition_table_offset,
            primary_bootloader_offset=primary_bootloader_offset,
            recovery_bootloader_offset=recovery_bootloader_offset,
        )
    return require_partitions(partition_table, f"file '{path}'")

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

def command_read(
        esp: ESPLoader,
        partition_table: PartitionTable,
        partition_table_entry: PartitionDefinition,
        bootloader_entry: Optional[PartitionDefinition],
        label: str,
        output_file: str
):
    partition, address, length = get_partition_slice(partition_table, partition_table_entry, bootloader_entry, label)
    print(f"Reading partition {partition.name} (offset={address:#x}, size={length:#x})")
    read_flash(esp, address=address, size=length, output=output_file)

def command_write(
        esp: ESPLoader,
        partition_table: PartitionTable,
        partition_table_entry: PartitionDefinition,
        bootloader_entry: Optional[PartitionDefinition],
        files: list[str]
):
    if len(files) % 2 != 0:
        raise ValueError("Files list must contain pairs of partition name and input file")

    addr_data = []
    for partition_name, input_file in list(zip(files[::2], files[1::2])):
        partition, address = get_partition_address(partition_table, partition_table_entry, bootloader_entry, partition_name)
        input_file_size = os.path.getsize(input_file)
        if input_file_size > partition.size:
            raise ValueError(f"Input file {input_file} size {input_file_size:#x} exceeds partition {partition.name} size {partition.size:#x}")
        addr_data.append((address, input_file))
        print(f"Writing file {input_file} (size={input_file_size:#x}) to partition {partition.name} (offset={address:#x}, size={partition.size:#x})")

    write_flash(
        esp=esp,
        addr_data=addr_data,
        flash_size='detect'
    )

def command_erase(
        esp: ESPLoader,
        partition_table: PartitionTable,
        partition_table_entry: PartitionDefinition,
        bootloader_entry: Optional[PartitionDefinition],
        label: str
):
    partition, address, length = get_partition_slice(partition_table, partition_table_entry, bootloader_entry, label)

    print(f"Erasing partition {partition.name} (offset={address:#x}, size={length:#x})")
    erase_region(esp, address=address, size=length)

def command_view(
        esp: ESPLoader,
        partition_table: PartitionTable,
        partition_table_entry: PartitionDefinition,
        bootloader_entry: Optional[PartitionDefinition],
        label: str,
        output: Literal['str', 'hex'],
        width: int
):
    partition, address, length = get_partition_slice(partition_table, partition_table_entry, bootloader_entry, label)

    print(f"Viewing partition {partition.name} (offset={address:#x}, size={length:#x})")
    buffer = esp.read_flash(offset=address, length=length)
    if output == 'str':
        print(buffer.decode('utf-8', errors='replace'), flush=True)
    elif output == 'hex':
        hexdump(buffer, address - partition.offset, width)
    else:
        raise ValueError(f"Invalid output type {output}")

def command_create_image(
        partition_table: PartitionTable,
        partition_table_entry: PartitionDefinition,
        bootloader_entry: Optional[PartitionDefinition],
        files: list[str],
        flash_partition_table: bool,
        output_format: Literal['raw', 'uf2', 'hex'],
        output_file: str
):
    if len(files) % 2 != 0:
        raise ValueError("Files list must contain pairs of partition name and input file")

    addr_data = []
    for partition_name, input_file in list(zip(files[::2], files[1::2])):
        partition, address = get_partition_address(partition_table, partition_table_entry, bootloader_entry, partition_name)
        input_file_size = os.path.getsize(input_file)
        if input_file_size > partition.size:
            raise ValueError(
                f"Input file {input_file} size {input_file_size:#x} exceeds partition {partition.name} size {partition.size:#x}")
        addr_data.append((address, input_file))
        print(f"Merging file {input_file} (size={input_file_size:#x}) to partition {partition.name} (offset={address:#x}, size={partition.size:#x})")

    # Add partition table if requested
    if flash_partition_table:
        partition_table_binary = partition_table.to_binary()
        addr_data.append((partition_table_entry.offset, partition_table_binary))

    merge_bin(
        addr_data=addr_data,
        flash_freq="keep",
        flash_mode="keep",
        flash_size="keep",
        chip='esp32', # Does not matter since we are keeping all flash parameters
        output=output_file,
        output_format=output_format,
        partition_table=partition_table
    )

def command_create_bundle(
        partition_table: PartitionTable,
        partition_table_entry: PartitionDefinition,
        bootloader_entry: Optional[PartitionDefinition],
        files: list[str],
        flash_partition_table: bool,
        output_file: str
):
    if len(files) % 2 != 0:
        raise ValueError("Files list must contain pairs of partition name and input file")

    with ZipFile(output_file, 'w') as zf:
        # Add specified partition files
        for partition_name, input_file in list(zip(files[::2], files[1::2])):
            partition = get_partition(partition_table, partition_table_entry, bootloader_entry, partition_name)
            input_file_size = os.path.getsize(input_file)
            if input_file_size > partition.size:
                raise ValueError(
                    f"Input file {input_file} size {input_file_size:#x} exceeds partition {partition.name} size {partition.size:#x}")
            zf.write(input_file, arcname=f"{partition.name}.bin")
            print(f"Adding file {input_file} (size={input_file_size:#x}) to bundle as partition {partition.name} (offset={partition.offset:#x}, size={partition.size:#x})")

        # Add empty files for erased partitions
        # for partition_name in args.erase:
        #     partition = get_partition(partition_table, partition_table_entry, bootloader_entry, partition_name)
        #     zf.writestr(f"{partition.name}.bin", bytes(partition.size))
        #     print(f"Adding empty file to bundle for erased partition {partition.name} (offset={partition.offset:#x}, size={partition.size:#x})")

        # Add partition table if requested
        if flash_partition_table:
            zf.writestr("partition_table.csv", partition_table.to_csv())
            print(f"Adding partition table CSV to bundle")

def check_write_bundle_has_partition_table(file: str) -> bool:
    if os.path.getsize(file) == 0:
        raise RuntimeError(f"Bundle '{file}' is empty")
    try:
        bundle_zip = ZipFile(file, 'r')
    except BadZipFile as e:
        raise RuntimeError(f"Bundle '{file}' is not a valid ZIP archive") from e
    with bundle_zip as zf:
        return any(m == 'partition_table.csv' for m in zf.namelist())

def command_write_bundle(
        esp: ESPLoader,
        partition_table: PartitionTable,
        partition_table_entry: PartitionDefinition,
        bootloader_entry: Optional[PartitionDefinition],
        input_file: str
):
    with ZipFile(input_file, 'r') as zf:
        # Flash files
        addr_data = []
        for member in filter(lambda m: m.endswith('.bin'), zf.namelist()):
            partition_name = member[:-4]  # Remove .bin extension
            partition = get_partition(partition_table, partition_table_entry, bootloader_entry, partition_name)
            data = zf.read(member)
            if len(data) > partition.size:
                raise ValueError(f"Input file {member.name} size {len(data):#x} exceeds partition {partition.name} size {partition.size:#x}")

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

def command_get_boot(esp: ESPLoader, partition_table: PartitionTable):
    _, otadata = read_otadata(esp, partition_table)

    if otadata.slot is None:
        print("OTA slot not set")
    else:
        print(f"OTA slot 'ota_{otadata.slot}' (seq={otadata.otadata.seq}, state={otadata.otadata.ota_state.name})")

def command_set_boot(esp: ESPLoader, partition_table: PartitionTable, label: str):
    otadata_partition, otadata = read_otadata(esp, partition_table)

    partition = partition_table[label]
    if partition.type != APP_TYPE:
        raise ValueError(f"Partition {label} is not an app partition")
    if partition.subtype < SUBTYPES[APP_TYPE]['ota_0'] or partition.subtype > SUBTYPES[APP_TYPE]['ota_15']:
        raise ValueError(f"Partition {label} is not an OTA partition")
    ota_slot = partition.subtype & 0x0F

    print(f"Setting boot partition to '{partition.name}'...")
    otadata = otadata.incremented_and_swapped(ota_slot)
    otadata.otadata.ota_state = OtaImageState.VALID
    write_otadata(esp, otadata_partition, otadata)

def command_clear_boot(esp: ESPLoader, partition_table: PartitionTable):
    otadata_partition = next(
        (p for p in partition_table if p.type == DATA_TYPE and p.subtype == SUBTYPES[DATA_TYPE]['ota']),
        None
    )
    if not otadata_partition:
        raise ValueError("No otadata partition found") # TODO Better error type

    print("Clearing boot partition...")
    esp.erase_region(offset=otadata_partition.offset, size=otadata_partition.size)

def command_ota(esp: ESPLoader, partition_table: PartitionTable, app_binary_file: str):
    otadata_partition, otadata = read_otadata(esp, partition_table)

    partition: PartitionDefinition
    next_slot = otadata.next_slot
    partition = next(
        (p for p in partition_table if p.type == APP_TYPE and p.subtype == SUBTYPES[APP_TYPE]['ota_0'] + next_slot),
        None
    )
    if not partition:
        raise ValueError(f"Partition ota_{next_slot} not found")

    app_binary, image_metadata = load_app_binary(esp, app_binary_file, partition)

    print(f"Writing '{image_metadata.app_description.title}' to partition '{partition.name}'...")
    write_flash(esp=esp, addr_data=[(partition.offset, app_binary_file)])

    print(f"Setting boot partition to 'ota_{next_slot}'...")
    otadata = otadata.incremented_and_swapped(next_slot)
    write_otadata(esp, otadata_partition, otadata)

def command_factory(esp: ESPLoader, partition_table: PartitionTable, app_binary_file: str):
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

def command_create_nvs(csv_file: str, size: int, output_file: str):
    print(f"Generating NVS image from '{csv_file}' (size={size:#x})...")
    image = generate_nvs_image(csv_file, size)
    with open(output_file, 'wb') as f:
        f.write(image)
    print(f"Wrote {len(image):#x} bytes to '{output_file}'")

def command_write_nvs(
        esp: ESPLoader,
        partition_table: PartitionTable,
        partition_table_entry: PartitionDefinition,
        bootloader_entry: Optional[PartitionDefinition],
        csv_file: str,
        partition_name: str
):
    partition = get_partition(partition_table, partition_table_entry, bootloader_entry, partition_name)
    print(f"Generating NVS image from '{csv_file}' for partition '{partition.name}' (size={partition.size:#x})...")
    image = generate_nvs_image(csv_file, partition.size)
    print(f"Writing NVS image to partition '{partition.name}' (offset={partition.offset:#x}, size={partition.size:#x})")
    write_flash(esp=esp, addr_data=[(partition.offset, image)], flash_size='detect')

def command_app_info(app_binary_file: str):
    if os.path.getsize(app_binary_file) == 0:
        raise RuntimeError(f"Application binary '{app_binary_file}' is empty")
    app_binary = open(app_binary_file, 'rb').read()
    try:
        image_metadata = ImageMetadata.from_bytes(app_binary, app_required=True)
    except ValueError as e:
        raise RuntimeError(f"Invalid application binary: {e}")

    print_app_info(image_metadata.app_description, image_metadata.header)

def command_write_image(esp: ESPLoader, bootloader_entry: PartitionDefinition, image_file_path: str):
    if os.path.getsize(image_file_path) == 0:
        raise RuntimeError(f"Image file '{image_file_path}' is empty")
    image_file = open(image_file_path, 'rb')
    image_file.seek(bootloader_entry.offset)
    bootloader_binary = image_file.read(bootloader_entry.size)
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

    image_data: bytes
    with open(image_file_path, 'rb') as image_file:
        image_file.seek(esp.BOOTLOADER_FLASH_OFFSET)
        image_data = image_file.read()

    print(f"Writing image '{image_file_path}'...")
    erase_flash(esp)
    write_flash(
        esp=esp,
        addr_data=[(esp.BOOTLOADER_FLASH_OFFSET, image_data)],
        flash_size='detect',
    )

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

def command_print_image(image_file_path: str, partition_table_offset: int, partition_table_size: int):
    image_size = check_image_file(image_file_path, partition_table_offset)
    with open(image_file_path, 'rb') as f:
        f.seek(partition_table_offset)
        partition_table_binary = f.read(partition_table_size)
        try:
            partition_table = PartitionTable.from_binary(partition_table_binary)
        except (RuntimeError, ValueError) as e:
            raise RuntimeError(f"Could not parse partition table at offset {partition_table_offset:#x}") from e
        require_partitions(partition_table, f"image at offset {partition_table_offset:#x}")

        def read(offset: int, length: int) -> bytes:
            if offset >= image_size:
                return b"\xff" * length
            f.seek(offset)
            data = f.read(length)
            if len(data) < length:
                data += b"\xff" * (length - len(data))
            return data

        print(f"Image: {image_file_path} ({image_size:#x} bytes)")
        print()
        print_partition_table_and_apps(partition_table, read)

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

def command_print_bundle(
        bundle_file_path: str,
        partition_table_offset: int,
        primary_bootloader_offset: Optional[int],
        recovery_bootloader_offset: Optional[int],
):
    bundle_size = os.path.getsize(bundle_file_path)
    if bundle_size == 0:
        raise RuntimeError(f"Bundle '{bundle_file_path}' is empty")
    try:
        bundle_zip = ZipFile(bundle_file_path, 'r')
    except BadZipFile as e:
        raise RuntimeError(f"Bundle '{bundle_file_path}' is not a valid ZIP archive") from e
    with bundle_zip as zf:
        names = zf.namelist()
        if 'partition_table.csv' not in names:
            raise RuntimeError(f"Bundle {bundle_file_path} does not contain a partition_table.csv")
        csv_text = zf.read('partition_table.csv').decode('utf-8')
        if not csv_text.strip():
            raise RuntimeError(f"Bundle '{bundle_file_path}' contains an empty partition_table.csv")

        # idftool-generated bundles encode bootloader offsets in the CSV; PartitionTable.from_csv
        # ignores the literal for bootloader rows and requires an explicit offset, so fall back to
        # what the CSV says when the user didn't pass one.
        if primary_bootloader_offset is None or recovery_bootloader_offset is None:
            csv_primary, csv_recovery = _extract_csv_bootloader_offsets(csv_text)
            if primary_bootloader_offset is None:
                primary_bootloader_offset = csv_primary
            if recovery_bootloader_offset is None:
                recovery_bootloader_offset = csv_recovery

        partition_table = PartitionTable.from_csv(
            csv_text,
            partition_table_offset=partition_table_offset,
            primary_bootloader_offset=primary_bootloader_offset,
            recovery_bootloader_offset=recovery_bootloader_offset,
        )
        require_partitions(partition_table, f"bundle '{bundle_file_path}'")

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
        print(f"Bundle: {bundle_file_path} ({bundle_size:#x} bytes)")
        print(f"Partitions included: {', '.join(included) if included else '(none)'}")
        print()
        print_partition_table_and_apps(partition_table, read)

def command_print_table(
        table_file_path: str,
        partition_table_offset: int,
        primary_bootloader_offset: Optional[int],
        recovery_bootloader_offset: Optional[int],
):
    partition_table = load_partition_table_file(
        table_file_path, partition_table_offset, primary_bootloader_offset, recovery_bootloader_offset)
    print(f"Partition table: {table_file_path}")
    print()
    print_partition_table(partition_table)

def command_convert_table(
        input_file: str,
        output_file: str,
        output_format: Optional[str],
        partition_table_offset: int,
        primary_bootloader_offset: Optional[int],
        recovery_bootloader_offset: Optional[int],
):
    partition_table = load_partition_table_file(
        input_file, partition_table_offset, primary_bootloader_offset, recovery_bootloader_offset)
    output_format = resolve_partition_table_format(output_file, output_format)
    write_partition_table_file(partition_table, output_file, output_format)

def command_dump_table(
        esp: ESPLoader,
        partition_table: PartitionTable,
        output_file: Optional[str],
        output_format: Optional[str],
):
    if not output_file:
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
    write_partition_table_file(partition_table, output_file, output_format)

def command_write_table(
        esp: ESPLoader,
        table_file_path: str,
        partition_table_offset: int,
        primary_bootloader_offset: Optional[int],
        recovery_bootloader_offset: Optional[int],
        force: bool = False,
):
    partition_table = load_partition_table_file(
        table_file_path, partition_table_offset, primary_bootloader_offset, recovery_bootloader_offset)

    try:
        partition_table.verify(partition_table_offset=partition_table_offset)
    except RuntimeError as e:
        if not force:
            raise RuntimeError(
                f"Partition table failed verification: {e}. Re-run with --force to flash it anyway.") from e
        print(f"Warning: partition table failed verification: {e} (continuing due to --force)")

    partition_table.verify_size_fits(flash_size_bytes(detect_flash_size(esp)))

    binary = partition_table.to_binary()
    print_partition_table(partition_table)
    print()
    print(f"Writing partition table ({len(binary):#x} bytes) to offset {partition_table_offset:#x}...")
    print("Note: this replaces only the partition map; existing partition data on flash is not moved, "
          "resized, or erased. A table that no longer matches the flash contents can make the device unbootable.")
    write_flash(esp=esp, addr_data=[(partition_table_offset, binary)], flash_size='detect')
    print("Partition table written")

def command_dump_image(esp: ESPLoader, output_file: Optional[str]):
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

def command_dump_bundle(esp: ESPLoader, partition_table: PartitionTable, output_file: Optional[str]):
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

def command_enter_bootloader(port: str, baud: int, poll_interval: float = 0.05):
    """Fast-poll a serial port path and enter the ROM bootloader as soon as it appears.

    Skips run_stub and does not reset the chip on exit, so the device is left
    parked in the ROM bootloader (download mode) for the next tool to grab.
    """
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

def main(args):
    if args.command == 'devices':
        devices = _get_port_list()
        for d in devices:
            print(f"{d.device} || {d.description} || {d.hwid}")
        return

    if args.command == 'enter-bootloader':
        command_enter_bootloader(port=args.port, baud=args.baud)
        return

    # create-nvs with explicit --size needs neither device nor partition table
    if args.command == 'create-nvs' and args.size is not None:
        command_create_nvs(args.csv_file, args.size, args.output)
        return

    # print-image inspects a local image file and needs neither device nor external partition table
    if args.command == 'print-image':
        command_print_image(
            image_file_path=args.image_file,
            partition_table_offset=args.partition_table_offset,
            partition_table_size=args.partition_table_size,
        )
        return

    # print-bundle inspects a local bundle ZIP and needs neither device nor external partition table
    if args.command == 'print-bundle':
        command_print_bundle(
            bundle_file_path=args.bundle_file,
            partition_table_offset=args.partition_table_offset,
            primary_bootloader_offset=args.primary_bootloader_offset,
            recovery_bootloader_offset=args.recovery_bootloader_offset,
        )
        return

    # print-table and convert-table operate purely on a local partition table file. print-table
    # with no file falls through to the shared loader, printing the device's table (or the one
    # given via --partition-table-file).
    if args.command == 'print-table' and args.table_file:
        command_print_table(
            table_file_path=args.table_file,
            partition_table_offset=args.partition_table_offset,
            primary_bootloader_offset=args.primary_bootloader_offset,
            recovery_bootloader_offset=args.recovery_bootloader_offset,
        )
        return

    if args.command == 'convert-table':
        command_convert_table(
            input_file=args.input_file,
            output_file=args.output_file,
            output_format=args.format,
            partition_table_offset=args.partition_table_offset,
            primary_bootloader_offset=args.primary_bootloader_offset,
            recovery_bootloader_offset=args.recovery_bootloader_offset,
        )
        return

    # Connect to ESP device if required
    requires_esp = args.command not in ['create-image', 'create-bundle', 'create-nvs', 'print-table'] or not args.partition_table_file
    esp = get_esp(port=args.port, baud=args.baud) if requires_esp else None
    if not args.primary_bootloader_offset and esp:
        args.primary_bootloader_offset = esp.BOOTLOADER_FLASH_OFFSET

    # write-table flashes a partition table from its own positional file, bypassing the shared
    # loader so it never reads the device's current table and writes it straight back to itself.
    if args.command == 'write-table':
        command_write_table(
            esp=esp,
            table_file_path=args.table_file,
            partition_table_offset=args.partition_table_offset,
            primary_bootloader_offset=args.primary_bootloader_offset,
            recovery_bootloader_offset=args.recovery_bootloader_offset,
            force=args.force,
        )
        if esp and not args.no_reset:
            esp.hard_reset()
        return

    if args.command == 'dump-image':
        command_dump_image(esp=esp, output_file=args.output_file)
        return

    # Load partition table
    if args.command == 'write-image':
        check_image_file(args.image_file, args.partition_table_offset)
        with open(args.image_file, 'rb') as f:
            f.seek(args.partition_table_offset)
            partition_table_binary = f.read(args.partition_table_size)
            partition_table = PartitionTable.from_binary(partition_table_binary)
    elif args.partition_table_file:
        partition_table = load_partition_table_file(
            args.partition_table_file,
            partition_table_offset=args.partition_table_offset,
            primary_bootloader_offset=args.primary_bootloader_offset,
            recovery_bootloader_offset=args.recovery_bootloader_offset,
        )
    elif args.command == 'write-bundle' and check_write_bundle_has_partition_table(args.input_file):
        with ZipFile(args.input_file, 'r') as tar:
            partition_table_file = tar.read('partition_table.csv')
            if not partition_table_file:
                raise RuntimeError("Partition table could not be loaded from bundle")
            partition_table = PartitionTable.from_csv(
                partition_table_file.decode('utf-8'),
                partition_table_offset=args.partition_table_offset,
                primary_bootloader_offset=args.primary_bootloader_offset,
                recovery_bootloader_offset=args.recovery_bootloader_offset
            )
    else:
        try:
            partition_table_binary = esp.read_flash(offset=args.partition_table_offset, length=args.partition_table_size)
            partition_table = PartitionTable.from_binary(partition_table_binary)
        except RuntimeError as e:
            raise RuntimeError("Partition table could not be loaded") from e

    require_partitions(partition_table, "the loaded source")

    # Read otadata so the printed table can mark the active app partition
    otadata_params: OtaDataParameters | None = None
    if esp:
        try:
            _, otadata_params = read_otadata(esp, partition_table)
        except ValueError:
            pass

    # Print partition table
    print_partition_table(partition_table, esp.read_flash if esp else None, otadata=otadata_params)

    if esp:
        flash_size_str = detect_flash_size(esp)
        flash_size = flash_size_bytes(flash_size_str)
        partition_table.verify_size_fits(flash_size)

    # Add virtual partition table entry if not present
    partition_table_entry = next(
        (e for e in partition_table if e.type == PARTITION_TABLE_TYPE and e.subtype == SUBTYPES[e.type]['primary']),
        PartitionDefinition.default_partition_table(
            offset=args.partition_table_offset,
            size=args.partition_table_size
        )
    )

    # Add virtual bootloader partition if not present and bootloader offsets are provided
    bootloader_entry = next(
        (e for e in partition_table if e.type == BOOTLOADER_TYPE and e.subtype == SUBTYPES[e.type]['primary']),
        PartitionDefinition.default_bootloader(
            offset=args.primary_bootloader_offset,
            size=args.partition_table_offset - args.primary_bootloader_offset
        ) if args.primary_bootloader_offset is not None else None
    )

    if args.command == 'print-table':
        # The shared path above already loaded and printed the table.
        pass
    elif args.command == 'read':
        command_read(
            esp=esp,
            partition_table=partition_table,
            partition_table_entry=partition_table_entry,
            bootloader_entry=bootloader_entry,
            label=args.partition,
            output_file=args.output_file
        )
    elif args.command == 'write':
        command_write(
            esp=esp,
            partition_table=partition_table,
            partition_table_entry=partition_table_entry,
            bootloader_entry=bootloader_entry,
            files=args.files
        )
    elif args.command == 'erase':
        command_erase(
            esp=esp,
            partition_table=partition_table,
            partition_table_entry=partition_table_entry,
            bootloader_entry=bootloader_entry,
            label=args.partition
        )
    elif args.command == 'view':
        command_view(
            esp=esp,
            partition_table=partition_table,
            partition_table_entry=partition_table_entry,
            bootloader_entry=bootloader_entry,
            label=args.partition,
            output=args.output,
            width=args.width
        )
    elif args.command == 'create-image':
        command_create_image(
            partition_table=partition_table,
            partition_table_entry=partition_table_entry,
            bootloader_entry=bootloader_entry,
            files=args.files,
            flash_partition_table=args.flash_partition_table,
            output_format=args.format,
            output_file=args.output_file
        )
    elif args.command == 'create-bundle':
        command_create_bundle(
            partition_table=partition_table,
            partition_table_entry=partition_table_entry,
            bootloader_entry=bootloader_entry,
            files=args.files,
            flash_partition_table=args.flash_partition_table,
            output_file=args.output_file
        )
    elif args.command == 'dump-bundle':
        command_dump_bundle(esp=esp, partition_table=partition_table, output_file=args.output_file)
    elif args.command == 'dump-table':
        command_dump_table(
            esp=esp,
            partition_table=partition_table,
            output_file=args.output_file,
            output_format=args.format,
        )
    elif args.command == 'write-bundle':
        command_write_bundle(
            esp=esp,
            partition_table=partition_table,
            partition_table_entry=partition_table_entry,
            bootloader_entry=bootloader_entry,
            input_file=args.input_file
        )
    elif args.command == 'get-boot':
        command_get_boot(esp=esp, partition_table=partition_table)
    elif args.command == 'set-boot':
        command_set_boot(esp=esp, partition_table=partition_table, label=args.partition)
    elif args.command == 'clear-boot':
        command_clear_boot(esp=esp, partition_table=partition_table)
    elif args.command == 'ota':
        command_ota(esp=esp, partition_table=partition_table, app_binary_file=args.app_binary_file)
    elif args.command == 'factory':
        command_factory(esp=esp, partition_table=partition_table, app_binary_file=args.app_binary_file)
    elif args.command == 'write-image':
        command_write_image(esp=esp, bootloader_entry=bootloader_entry, image_file_path=args.image_file)
    elif args.command == 'create-nvs':
        partition = get_partition(partition_table, partition_table_entry, bootloader_entry, args.partition)
        command_create_nvs(args.csv_file, partition.size, args.output)
    elif args.command == 'write-nvs':
        command_write_nvs(
            esp=esp,
            partition_table=partition_table,
            partition_table_entry=partition_table_entry,
            bootloader_entry=bootloader_entry,
            csv_file=args.csv_file,
            partition_name=args.partition
        )
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)

    if esp and not args.no_reset: esp.hard_reset()

def _main():
    install_excepthook()
    parser = argparse.ArgumentParser()

    parser.add_argument('-p', '--port', help='Serial port device')
    parser.add_argument('-b', '--baud', type=int, help='Serial port baud rate', default=ESPLoader.ESP_ROM_BAUD)
    parser.add_argument('--no-reset', action='store_true', help='Do not reset the chip after operations')

    parser.add_argument('--partition-table-file', help='Path to partition table CSV or binary file to use instead of reading from device')
    parser.add_argument('--partition-table-offset', type=auto_int, help='Partition table offset (where to read partition table from flash, or where to place when loading partition table from CSV)', default=PARTITION_TABLE_OFFSET)
    parser.add_argument('--partition-table-size', type=auto_int, help='Partition table size', default=PARTITION_TABLE_SIZE)
    parser.add_argument('--primary-bootloader-offset', type=parse_bootloader_offset, help='Primary bootloader offset or chip type e.g. esp32s3 (used when loading partition table from CSV)')
    parser.add_argument('--recovery-bootloader-offset', type=auto_int, help='Recovery bootloader offset (used when loading partition table from CSV)')

    # Create subparsers
    subparsers = parser.add_subparsers(dest='command', help='Available commands', required=True)

    # Devices subcommand
    devices_parser = subparsers.add_parser('devices', help='Device list')

    # Read subcommand
    read_parser = subparsers.add_parser('read', help='Read partition')
    read_parser.add_argument('partition', help='Name of the partition to read')
    read_parser.add_argument('output_file', help='Output file to save the partition data')

    # Write subcommand
    write_parser = subparsers.add_parser('write', help='Write partitions')
    write_parser.add_argument(
        'files',
        nargs='+',
        metavar=('PARTITION', 'FILENAME'),
        help='Partition and filename pairs'
    )

    # Erase subcommand
    erase_parser = subparsers.add_parser('erase', help='Erase partition')
    erase_parser.add_argument('partition', help='Name of the partition to erase')

    # View subcommand
    view_parser = subparsers.add_parser('view', help='View partition')
    view_parser.add_argument('partition', help='Name of the partition to view')
    view_parser.add_argument('-w', '--width', type=int, help='Width of the hexdump', default=16)
    view_parser.add_argument('--hex', action='store_const', const='hex', dest='output', help='Output as hex dump', default='hex')
    view_parser.add_argument('-s', '--string', action='store_const', const='str', dest='output', help='Output as string')

    # Create Image subcommand
    create_image_parser = subparsers.add_parser('create-image', help='Merge partition binaries into a single flash image')
    create_image_parser.add_argument('-o', '--output', help='Output filename', dest='output_file', required=True)
    create_image_parser.add_argument('-f', '--format', help='Output format', choices=['raw', 'uf2', 'hex'], default='raw')
    create_image_parser.add_argument('--flash-partition-table', action='store_true', help='Include partition table in the image')
    create_image_parser.add_argument(
        'files',
        nargs='+',
        metavar='PARTITION FILENAME',
        help='Partition and filename pairs'
    )

    # Dump Image subcommand
    dump_image_parser = subparsers.add_parser('dump-image', help='Dump the entire flash to an image file')
    dump_image_parser.add_argument('output_file', nargs='?', help='Output filename (default: {chip}-{mac}-{timestamp}.img)')

    # Write Image subcommand (alias: reflash)
    write_image_parser = subparsers.add_parser('write-image', aliases=['reflash'], help='Write a full flash image to the device')
    write_image_parser.add_argument('image_file', help='Input file containing flash image')

    # Print Image subcommand
    print_image_parser = subparsers.add_parser('print-image', help='Print partition table and app info from a flash image file')
    print_image_parser.add_argument('image_file', help='Input file containing flash image')

    # Create bundle subcommand
    create_bundle_parser = subparsers.add_parser('create-bundle', help='Create a ZIP bundle of partition binaries')
    create_bundle_parser.add_argument('-o', '--output', help='Output ZIP filename', dest='output_file', required=True)
    create_bundle_parser.add_argument('--flash-partition-table', action='store_true', help='Include partition table in the bundle')
    create_bundle_parser.add_argument(
        'files',
        nargs='+',
        metavar='PARTITION FILENAME',
        help='Partition and filename pairs'
    )

    # Dump Bundle subcommand
    dump_bundle_parser = subparsers.add_parser('dump-bundle', help='Dump all partitions to a ZIP bundle')
    dump_bundle_parser.add_argument('output_file', nargs='?', help='Output ZIP filename (default: {chip}-{mac}-{timestamp}.zip)')

    # Write bundle subcommand
    write_bundle_parser = subparsers.add_parser('write-bundle', help='Write a ZIP bundle of partition binaries')
    write_bundle_parser.add_argument('input_file', help='Input ZIP filename')

    # Print Bundle subcommand
    print_bundle_parser = subparsers.add_parser('print-bundle', help='Print partition table and app info from a partition bundle ZIP')
    print_bundle_parser.add_argument('bundle_file', help='Input ZIP bundle file')

    # Print Table subcommand
    print_table_parser = subparsers.add_parser('print-table', aliases=['list'], help='Print a partition table from a CSV or binary file, or from the device')
    print_table_parser.add_argument('table_file', nargs='?', help='Partition table CSV or binary file (default: read from the device, or --partition-table-file)')

    # Convert Table subcommand
    convert_table_parser = subparsers.add_parser('convert-table', help='Convert a partition table file between CSV and binary')
    convert_table_parser.add_argument('input_file', help='Input partition table CSV or binary file')
    convert_table_parser.add_argument('output_file', help='Output partition table file (.csv or .bin)')
    convert_table_parser.add_argument('-f', '--format', choices=['csv', 'bin'], help='Output format (default: inferred from output extension)')

    # Dump Table subcommand
    dump_table_parser = subparsers.add_parser('dump-table', help='Dump the partition table from the device to a file')
    dump_table_parser.add_argument('output_file', nargs='?', help='Output file (default: {chip}-{mac}-{timestamp}-partition-table.csv)')
    dump_table_parser.add_argument('-f', '--format', choices=['csv', 'bin'], help='Output format (default: inferred from output extension, else csv)')

    # Write Table subcommand
    write_table_parser = subparsers.add_parser('write-table', help='Write a partition table from a CSV or binary file to the device')
    write_table_parser.add_argument('table_file', help='Partition table CSV or binary file to flash')
    write_table_parser.add_argument('--force', action='store_true', help='Flash even if the partition table fails verification')

    # Create NVS subcommand
    create_nvs_parser = subparsers.add_parser('create-nvs', help='Generate NVS partition image from CSV')
    create_nvs_parser.add_argument('csv_file', help='Input NVS CSV file')
    create_nvs_parser.add_argument('-o', '--output', required=True, help='Output binary filename (.bin)')
    create_nvs_size_group = create_nvs_parser.add_mutually_exclusive_group(required=True)
    create_nvs_size_group.add_argument('--size', type=auto_int, help='Partition size in bytes (e.g. 0x6000)')
    create_nvs_size_group.add_argument('--partition', help='Partition name to read size from partition table')

    # Write NVS subcommand
    write_nvs_parser = subparsers.add_parser('write-nvs', help='Generate NVS partition image from CSV and write to device')
    write_nvs_parser.add_argument('partition', help='NVS partition name')
    write_nvs_parser.add_argument('csv_file', help='Input NVS CSV file')

    # Factory subcommand
    factory_parser = subparsers.add_parser('factory', help='Perform factory flash and clear boot partition')
    factory_parser.add_argument('app_binary_file', help='Input file containing app binary')

    # Ota subcommand
    ota_parser = subparsers.add_parser('ota', help='Perform OTA')
    ota_parser.add_argument('app_binary_file', help='Input file containing app binary')

    # Get Boot subcommand
    get_boot_parser = subparsers.add_parser('get-boot', help='Get boot partition')

    # Set Boot subcommand
    set_boot_parser = subparsers.add_parser('set-boot', help='Set boot partition')
    set_boot_parser.add_argument('partition', help='Name of the partition to set as boot')

    # Clear Boot subcommand
    clear_boot_parser = subparsers.add_parser('clear-boot', help='Clear boot partition')

    # Enter Bootloader subcommand
    enter_bootloader_parser = subparsers.add_parser('enter-bootloader', help='Fast-poll the serial port and enter the ROM bootloader as soon as the device appears, then exit without resetting')

    args = parser.parse_args()
    if args.command == 'reflash':
        args.command = 'write-image'
    if args.command == 'list':
        args.command = 'print-table'
    if args.command == 'enter-bootloader' and not args.port:
        parser.error("enter-bootloader requires -p/--port")
    try:
        main(args)
    except KeyboardInterrupt:
        sys.exit(130)
    except BaseException as e:
        print_exception(e)
        sys.exit(1)

if __name__ == "__main__":
    _main()
