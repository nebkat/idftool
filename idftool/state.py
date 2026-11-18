"""The connection and global options every command is handed.

``State`` owns the (lazily-established) serial connection and the global CLI options;
``State.setup()`` resolves the partition table from the most specific source available
and returns a ``Loaded`` for the command to work with."""
import sys
from dataclasses import dataclass
from typing import Optional, cast

import rich_click as click

from esptool import ESPLoader, flash_size_bytes
from esptool.cmds import detect_chip, detect_flash_size

from esp_idf_defs.otadata import OtaDataParameters
from esp_idf_defs.partitions import PartitionTable, PartitionDefinition, \
    BOOTLOADER_TYPE, SUBTYPES, PARTITION_TABLE_OFFSET, PARTITION_TABLE_SIZE, PARTITION_TABLE_TYPE

from idftool import network
from idftool.display import print_partition_table
from idftool.partitions import check_image_file, load_partition_table_file, read_otadata, \
    require_partitions
from idftool.ports import Identify, find_only_device, find_port_for_mac, find_port_for_usb_serial, \
    print_rerun_hint, select_device, warn_holders

def get_esp(port: str, baud: int, *, kill_holders: bool = False) -> ESPLoader:
    network.prepare(port)
    warn_holders(port, kill=kill_holders)
    return detect_chip(port, baud=baud).run_stub()


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

    def __init__(self, *, port: Optional[str], baud: int, no_reset: bool,
                 partition_table_file: Optional[str], partition_table_offset: int,
                 partition_table_size: int, primary_bootloader_offset: Optional[int],
                 recovery_bootloader_offset: Optional[int], assume_yes: bool = False,
                 mac: Optional[str] = None, usb_serial: Optional[str] = None, probe: bool = False,
                 identify: Optional[Identify] = None, baud_explicit: bool = False,
                 quiet: bool = False, kill_holders: bool = False,
                 only_device: bool = False) -> None:
        self.port = port
        self.mac = mac
        self.usb_serial = usb_serial
        self.probe = probe
        #: Names a probed device; see :func:`idftool.ports.probe_port`.
        self.identify = identify
        self.baud = baud
        #: Whether `baud` was asked for rather than defaulted; only then is it passed on to
        #: the tools (``monitor``, ``idf.py``), whose own defaults may differ.
        self.baud_explicit = baud_explicit
        self.no_reset = no_reset
        self.assume_yes = assume_yes
        self.kill_holders = kill_holders
        self.only_device = only_device
        self.partition_table_file = partition_table_file
        self.partition_table_offset = partition_table_offset
        self.partition_table_size = partition_table_size
        self.primary_bootloader_offset = primary_bootloader_offset
        self.recovery_bootloader_offset = recovery_bootloader_offset
        self.esp: Optional[ESPLoader] = None
        #: Skip the tables printed on the way (the partition table, a filesystem's listing),
        #: and the device reads only they need.
        self.quiet = quiet
        #: Set by a command whose stdout is data meant to be piped or captured (get-nvs).
        #: Progress and status then belong on stderr instead — including the hard reset,
        #: which happens after the command has already printed its result.
        self.stdout_is_data = False

    @classmethod
    def attached(cls, esp: ESPLoader, *, partition_table_file: Optional[str] = None,
                 assume_yes: bool = False) -> 'State':
        """A state driving `esp`, a connection the caller already holds, for calling idftool's
        commands as functions.

        The chip is never reset: when it returns to running is the caller's decision. The
        partition table is read off the device unless `partition_table_file` names one. The
        state is quiet (see :attr:`quiet`)."""
        state = cls(port=esp.serial_port, baud=esp._port.baudrate, no_reset=True,
                    partition_table_file=partition_table_file,
                    partition_table_offset=PARTITION_TABLE_OFFSET, partition_table_size=PARTITION_TABLE_SIZE,
                    primary_bootloader_offset=esp.BOOTLOADER_FLASH_OFFSET, recovery_bootloader_offset=None,
                    assume_yes=assume_yes, quiet=True)
        state.esp = esp
        return state

    def resolve_port(self, allow_none: bool = False) -> Optional[str]:
        """The port to connect to: ``-p``, else ``--mac``'s, ``--usb-serial``'s or
        ``--only-device``'s, else ask. Never guesses: without a terminal, or with ``-y``, no
        device given is an error. With `allow_none`, the question offers no device."""
        if self.mac and not self.port:
            return find_port_for_mac(self.mac, self.baud, probe=self.probe)
        if self.usb_serial and not self.port:
            return find_port_for_usb_serial(self.usb_serial)
        if self.port:
            return self.port
        if self.only_device:
            return find_only_device()
        if self.assume_yes or not sys.stdin.isatty():
            raise click.UsageError("No device given: pass -p, -m, --usb-serial or --only-device")
        found = select_device(self.baud, probe=self.probe, identify=self.identify,
                              allow_none=allow_none)
        assert found is not None  # select_device asks whenever stdin is a terminal
        if found["port"] is not None:
            print_rerun_hint(found["port"], found.get("mac"), found.get("serial"))
        return cast(Optional[str], found["port"])

    def connect(self) -> ESPLoader:
        if self.esp is None:
            port = self.resolve_port()
            assert port is not None  # only allow_none lets the question return no device
            self.port = port
            self.esp = get_esp(port=self.port, baud=self.baud, kill_holders=self.kill_holders)
            # Fall back to the connected chip's bootloader offset only when the user didn't set one
            # explicitly, keeping the precedence: explicit CLI > chip default > value present in CSV.
            if self.primary_bootloader_offset is None:
                self.primary_bootloader_offset = self.esp.BOOTLOADER_FLASH_OFFSET
        return self.esp

    def _load_partition_table(self, image_file: Optional[str]) -> PartitionTable:
        # Load from the most specific source available:
        # image file > --partition-table-file > the connected device.
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
        else:
            assert self.esp is not None  # connected, as no file names the table
            try:
                binary = self.esp.read_flash(offset=self.partition_table_offset, length=self.partition_table_size)
                partition_table = PartitionTable.from_binary(binary)
            except RuntimeError as e:
                raise RuntimeError("Partition table could not be loaded") from e
        return require_partitions(partition_table, "the loaded source")

    def setup(self, *, needs_device: bool = True, image_file: Optional[str] = None) -> 'Loaded':
        """Connect if required, load and print the partition table (unless `quiet`), and build
        the virtual partition-table/bootloader entries the command handlers expect.

        Returns a ``Loaded`` whose ``esp`` is ``None`` for commands that ran entirely from a file.
        """
        table_from_file = image_file is not None or bool(self.partition_table_file)
        if needs_device or not table_from_file:
            self.connect()
        esp = self.esp

        partition_table = self._load_partition_table(image_file)
        if not self.quiet:
            self._print_partition_table(partition_table)

        if esp:
            partition_table.verify_size_fits(flash_size_bytes(detect_flash_size(esp)))

        return Loaded(esp, partition_table, *self.virtual_entries(partition_table))

    def _print_partition_table(self, partition_table: PartitionTable) -> None:
        """Print the table, with each app's description and the active slot read off the device
        when there is one."""
        esp = self.esp
        otadata_params: Optional[OtaDataParameters] = None
        if esp:
            try:
                _, otadata_params = read_otadata(esp, partition_table)
            except ValueError:
                pass
            except Exception as e:
                print(f"Warning: failed to read otadata: {type(e).__name__}: {e}", file=sys.stderr)

        print_partition_table(partition_table, esp.read_flash if esp else None, otadata=otadata_params)

    def read_device_table(self) -> Optional[PartitionTable]:
        """The device's table (or --partition-table-file's), or None if the device has none."""
        if self.partition_table_file:
            return self._load_partition_table(None)
        binary = self.connect().read_flash(offset=self.partition_table_offset,
                                           length=self.partition_table_size)
        try:
            table = PartitionTable.from_binary(binary)
        except (RuntimeError, ValueError):  # blank or corrupt
            return None
        return table if len(table) else None

    def virtual_entries(
            self, partition_table: PartitionTable
    ) -> tuple[PartitionDefinition, Optional[PartitionDefinition]]:
        """The partition-table and bootloader entries names resolve to, as the table's own rows
        or at the configured offsets."""
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
        return partition_table_entry, bootloader_entry
