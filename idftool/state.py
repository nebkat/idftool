"""The connection and global options every command is handed.

``State`` owns the (lazily-established) serial connection and the global CLI options;
``State.setup()`` resolves the partition table from the most specific source available
and returns a ``Loaded`` for the command to work with."""
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional, cast

import rich_click as click

from esptool import ESPLoader, flash_size_bytes
from esptool.cmds import detect_chip, detect_flash_size

from esp_idf_defs.otadata import OtaDataParameters
from esp_idf_defs.partitions import PartitionTable, PartitionDefinition, \
    BOOTLOADER_TYPE, SUBTYPES, PARTITION_TABLE_OFFSET, PARTITION_TABLE_SIZE, PARTITION_TABLE_TYPE

from esp_pylib.serial_ports import get_port_names

from idftool import network
from idftool.apps import BOOTLOADER_OFFSETS, device_flash_size, device_partition_table_offset, \
    recorded_partition_table_offset
from idftool.display import print_partition_table, say
from idftool.partitions import check_image_file, image_reader, is_erased, load_partition_table_file, \
    read_otadata, require_partitions
from idftool.ports import Identify, find_port_for_mac, find_port_for_usb_serial, print_rerun_hint, \
    select_device, warn_holders

def get_esp(port: str | None, baud: int) -> ESPLoader:
    esp: ESPLoader | None = None
    if port:
        network.prepare(port)
        warn_holders(port)
        esp = detect_chip(port, baud=baud)
    else:
        ports = get_port_names()
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


def check_table_offset(declared: int, recorded: Optional[int], what: str, *, writing: bool, force: bool) -> None:
    """Check a table `declared` at an offset by `what` against where the bootloader `recorded`
    it (None if it records none, and anything goes). A table written elsewhere is one the
    bootloader won't find: refused unless `force`. Reading one elsewhere is only warned about,
    as a leftover table can be worth a look."""
    if recorded is None or declared == recorded:
        return
    mismatch = f"{what} puts the partition table at {declared:#x}, but the bootloader records {recorded:#x}"
    if not writing:
        print(f"Warning: {mismatch}; reading {declared:#x}", file=sys.stderr)
    elif not force:
        raise RuntimeError(f"{mismatch}, so it would not find the table there. Re-run with --force to write it "
                           "anyway.")
    else:
        say(f"Warning: {mismatch} (writing it anyway, --force)")


def check_table_row(partition_table: PartitionTable, recorded: Optional[int], *, writing: bool, force: bool) -> None:
    """:func:`check_table_offset` for the table's own primary ``partition_table`` row, if it has one."""
    row = next((e for e in partition_table
                if e.type == PARTITION_TABLE_TYPE and e.subtype == SUBTYPES[e.type]['primary']), None)
    if row is not None:
        check_table_offset(row.offset, recorded, f"The table's {row.name} row", writing=writing, force=force)


@dataclass
class Loaded:
    """What State.setup() produces: the (optional) device connection, the loaded partition table,
    and the virtual partition-table/bootloader entries used to resolve partitions by name."""
    esp: Optional[ESPLoader]
    partition_table: PartitionTable
    partition_table_entry: PartitionDefinition
    bootloader_entry: Optional[PartitionDefinition]
    #: Where `partition_table` was read from, or placed when it came from a CSV.
    partition_table_offset: int = field(default=PARTITION_TABLE_OFFSET, kw_only=True)


class State:
    """Holds the global options and the (lazily-established) device connection."""

    def __init__(self, *, port: Optional[str], baud: int, no_reset: bool,
                 partition_table_file: Optional[str], partition_table_offset: Optional[int],
                 partition_table_size: int, primary_bootloader_offset: Optional[int],
                 recovery_bootloader_offset: Optional[int], assume_yes: bool = False,
                 mac: Optional[str] = None, usb_serial: Optional[str] = None, probe: bool = False,
                 identify: Optional[Identify] = None, baud_explicit: bool = False,
                 quiet: bool = False) -> None:
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
        self.partition_table_file = partition_table_file
        #: ``--partition-table-offset``, or None to have :meth:`table_offset` find it.
        self.explicit_partition_table_offset = partition_table_offset
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
                    partition_table_offset=None, partition_table_size=PARTITION_TABLE_SIZE,
                    primary_bootloader_offset=esp.BOOTLOADER_FLASH_OFFSET, recovery_bootloader_offset=None,
                    assume_yes=assume_yes, quiet=True)
        state.esp = esp
        return state

    def resolve_port(self, allow_none: bool = False) -> Optional[str]:
        """The port to connect to: ``-p``, else ``--mac``'s or ``--usb-serial``'s, else ask
        interactively (unless ``-y``). With `allow_none`, the question offers no device."""
        if self.mac and not self.port:
            return find_port_for_mac(self.mac, self.baud, probe=self.probe)
        if self.usb_serial and not self.port:
            return find_port_for_usb_serial(self.usb_serial)
        if self.port or self.assume_yes or not sys.stdin.isatty():
            return self.port
        found = select_device(self.baud, probe=self.probe, identify=self.identify,
                              allow_none=allow_none)
        assert found is not None  # select_device asks whenever stdin is a terminal
        if found["port"] is not None:
            print_rerun_hint(found["port"], found.get("mac"), found.get("serial"))
        return cast(Optional[str], found["port"])

    def connect(self) -> ESPLoader:
        if self.esp is None:
            self.port = self.resolve_port()
            self.esp = get_esp(port=self.port, baud=self.baud)
            # Fall back to the connected chip's bootloader offset only when the user didn't set one
            # explicitly, keeping the precedence: explicit CLI > chip default > value present in CSV.
            if self.primary_bootloader_offset is None:
                self.primary_bootloader_offset = self.esp.BOOTLOADER_FLASH_OFFSET
        return self.esp

    @property
    def partition_table_offset(self) -> int:
        """The partition table offset in use: see :meth:`table_offset`. Setting it makes it explicit,
        as ``--partition-table-offset`` does."""
        return self.table_offset()

    @partition_table_offset.setter
    def partition_table_offset(self, offset: Optional[int]) -> None:
        self.explicit_partition_table_offset = offset

    def recorded_table_offset(self, image: Optional[Callable[[int, int], bytes]] = None,
                              image_size: Optional[int] = None) -> Optional[int]:
        """Where the bootloader records the partition table: the one in the flash image `image`
        reads (``read(offset, length)``, `image_size` bytes long), or the connected device's when
        no image is given. None if it records none, or nothing is connected."""
        if image is not None:
            return recorded_partition_table_offset(image, sorted(set(BOOTLOADER_OFFSETS.values())), image_size)
        if self.esp is None:
            return None
        bootloader = self.primary_bootloader_offset
        if bootloader is None:
            bootloader = self.esp.BOOTLOADER_FLASH_OFFSET
        esp = self.esp
        return device_partition_table_offset(esp, bootloader, lambda: device_flash_size(esp))

    def resolve_table_offset(self, recorded: Optional[int], *, writing: bool = False, force: bool = False) -> int:
        """``--partition-table-offset``, else `recorded`, else ESP-IDF's default. An explicit
        offset the bootloader doesn't record is checked by :func:`check_table_offset`."""
        explicit = self.explicit_partition_table_offset
        if explicit is None:
            return recorded if recorded is not None else PARTITION_TABLE_OFFSET
        check_table_offset(explicit, recorded, "--partition-table-offset", writing=writing, force=force)
        return explicit

    def table_offset(self, image: Optional[Callable[[int, int], bytes]] = None,
                     image_size: Optional[int] = None, *, writing: bool = False, force: bool = False) -> int:
        """Where the partition table is: :meth:`resolve_table_offset` of
        :meth:`recorded_table_offset`. `writing` and `force` are as :func:`check_table_offset`'s."""
        return self.resolve_table_offset(self.recorded_table_offset(image, image_size), writing=writing, force=force)

    def _load_partition_table(self, image_file: Optional[str], *, writing: bool = False,
                              force: bool = False) -> tuple[PartitionTable, int]:
        """The table, and the offset it was read from (or placed at, from a CSV). Loads from the
        most specific source available: image file > --partition-table-file > the connected
        device. `writing` and `force` are as :func:`check_table_offset`'s."""
        if image_file is not None:
            size = os.path.getsize(image_file)
            with open(image_file, 'rb') as f:
                read = image_reader(f, size)
                recorded = self.recorded_table_offset(read, size)
                offset = self.resolve_table_offset(recorded, writing=writing, force=force)
                check_image_file(image_file, offset)
                binary = read(offset, self.partition_table_size)
            source = f"the image at offset {offset:#x}"
        elif self.partition_table_file:
            recorded = self.recorded_table_offset()
            offset = self.resolve_table_offset(recorded, writing=writing, force=force)
            partition_table = load_partition_table_file(
                self.partition_table_file,
                partition_table_offset=offset,
                primary_bootloader_offset=self.primary_bootloader_offset,
                recovery_bootloader_offset=self.recovery_bootloader_offset,
            )
            check_table_row(partition_table, recorded, writing=writing, force=force)
            return require_partitions(partition_table, f"file '{self.partition_table_file}'"), offset
        else:
            assert self.esp is not None  # connected, as no file names the table
            recorded = self.recorded_table_offset()
            offset = self.resolve_table_offset(recorded, writing=writing, force=force)
            binary = self.esp.read_flash(offset=offset, length=self.partition_table_size)
            source = f"the device at offset {offset:#x}"
        if is_erased(binary):
            raise RuntimeError(f"No partition table in {source}: the flash there is erased")
        try:
            partition_table = PartitionTable.from_binary(binary)
        except RuntimeError as e:
            raise RuntimeError(f"Partition table in {source} could not be loaded") from e
        check_table_row(partition_table, recorded, writing=writing, force=force)
        return require_partitions(partition_table, source), offset

    def setup(self, *, needs_device: bool = True, image_file: Optional[str] = None, writing: bool = False,
              force: bool = False) -> 'Loaded':
        """Connect if required, load and print the partition table (unless `quiet`), and build
        the virtual partition-table/bootloader entries the command handlers expect. `writing`
        and `force` are as :func:`check_table_offset`'s, for a command that writes the table.

        Returns a ``Loaded`` whose ``esp`` is ``None`` for commands that ran entirely from a file.
        """
        table_from_file = image_file is not None or bool(self.partition_table_file)
        if needs_device or not table_from_file:
            self.connect()
        esp = self.esp

        partition_table, offset = self._load_partition_table(image_file, writing=writing, force=force)
        if not self.quiet:
            self._print_partition_table(partition_table)

        if esp:
            partition_table.verify_size_fits(flash_size_bytes(detect_flash_size(esp)))

        return Loaded(esp, partition_table, *self.virtual_entries(partition_table, partition_table_offset=offset),
                      partition_table_offset=offset)

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
            return self._load_partition_table(None)[0]
        binary = self.connect().read_flash(offset=self.table_offset(), length=self.partition_table_size)
        try:
            table = PartitionTable.from_binary(binary)
        except (RuntimeError, ValueError):  # blank or corrupt
            return None
        return table if len(table) else None

    def virtual_entries(
            self, partition_table: PartitionTable, *, partition_table_offset: Optional[int] = None
    ) -> tuple[PartitionDefinition, Optional[PartitionDefinition]]:
        """The partition-table and bootloader entries names resolve to, as the table's own rows
        or at `partition_table_offset` (by default :meth:`table_offset`) and the configured
        bootloader offset."""
        if partition_table_offset is None:
            partition_table_offset = self.table_offset()
        # Add a virtual partition table entry if not present
        partition_table_entry = next(
            (e for e in partition_table if e.type == PARTITION_TABLE_TYPE and e.subtype == SUBTYPES[e.type]['primary']),
            PartitionDefinition.default_partition_table(
                offset=partition_table_offset,
                size=self.partition_table_size,
            )
        )

        # Add a virtual bootloader partition if not present and a bootloader offset is known
        bootloader_entry = next(
            (e for e in partition_table if e.type == BOOTLOADER_TYPE and e.subtype == SUBTYPES[e.type]['primary']),
            PartitionDefinition.default_bootloader(
                offset=self.primary_bootloader_offset,
                size=partition_table_offset - self.primary_bootloader_offset,
            ) if self.primary_bootloader_offset is not None else None
        )
        return partition_table_entry, bootloader_entry
