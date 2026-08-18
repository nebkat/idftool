"""The connection and global options every command is handed.

``State`` owns the (lazily-established) serial connection and the global CLI options;
``State.setup()`` resolves the partition table from the most specific source available
and returns a ``Loaded`` for the command to work with."""
import sys
from dataclasses import dataclass
from typing import Optional
from zipfile import ZipFile

from esptool import ESPLoader, flash_size_bytes
from esptool.cmds import detect_chip, detect_flash_size

from esp_idf_defs.otadata import OtaDataParameters
from esp_idf_defs.partitions import PartitionTable, print_partition_table, PartitionDefinition, \
    BOOTLOADER_TYPE, SUBTYPES, PARTITION_TABLE_TYPE

from idftool.partitions import check_image_file, check_write_bundle_has_partition_table, \
    load_partition_table_file, parse_partition_table_csv, read_otadata, require_partitions
from idftool.ports import get_port_list

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
            except Exception as e:
                print(f"Warning: failed to read otadata: {type(e).__name__}: {e}", file=sys.stderr)

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
