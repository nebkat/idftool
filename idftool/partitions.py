"""Partition addressing and partition-table files.

Resolving a partition by name or offset (including the ``name[start:stop]`` slice
syntax), reading and writing otadata, and loading/saving partition tables as CSV or
binary. Everything here works on a table plus, where a device is involved, an
``ESPLoader`` — no CLI state."""
import os.path
import re
from typing import Optional, Literal
from zipfile import BadZipFile, ZipFile

from esptool import ESPLoader

from esp_idf_defs.otadata import OtaDataParameters, OtaDataSelectEntry
from esp_idf_defs.partitions import PartitionTable, PartitionDefinition, APP_TYPE, DATA_TYPE, \
    NUM_PARTITION_SUBTYPE_APP_OTA, SUBTYPES, get_encoding

PARTITION_SLICE_REGEX = re.compile(r"^(?P<partition>.+?)(?:\[(?:(?P<start>[-+]?(?:0x)?[0-9A-Fa-f]+)?(?::(?P<stop>[-+]?(?:0x)?[0-9A-Fa-f]+)?)?)?])?$")
PARTITION_OFFSET_REGEX = re.compile(r"^(?P<partition>.+?)(?:\[(?:(?P<start>[-+]?(?:0x)?[0-9A-Fa-f]+)?)?])?$")


def get_partition(
        partition_table: PartitionTable,
        partition_table_entry: PartitionDefinition,
        bootloader_entry: Optional[PartitionDefinition],
        label: str
) -> PartitionDefinition:
    # A numeric label addresses a partition by its exact start offset.
    try:
        address = int(label, 0)
    except ValueError:
        address = None
    if address is not None:
        match = next((p for p in partition_table if p.offset == address), None)
        if match is None:
            raise ValueError(f"No partition at offset {address:#x}")
        return match
    # Otherwise it's a name: real partitions first, then the virtual bootloader/table entries.
    try:
        return partition_table[label]
    except (KeyError, ValueError):
        pass
    if label == partition_table_entry.name:
        return partition_table_entry
    if bootloader_entry and label == bootloader_entry.name:
        return bootloader_entry
    raise ValueError(f"No partition named '{label}'")

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


def check_write_bundle_has_partition_table(file: str) -> bool:
    if os.path.getsize(file) == 0:
        raise RuntimeError(f"Bundle '{file}' is empty")
    try:
        bundle_zip = ZipFile(file, 'r')
    except BadZipFile as e:
        raise RuntimeError(f"Bundle '{file}' is not a valid ZIP archive") from e
    with bundle_zip as zf:
        return any(m == 'partition_table.csv' for m in zf.namelist())


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
