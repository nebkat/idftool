"""Terminal rendering: the partition table boxed and coloured when stdout is a terminal.

Anything else (a pipe, a file, a captured test) gets esp_idf_defs' plain table unchanged."""
import sys
from typing import Callable, Optional

from esp_idf_defs.app_description import AppDescription
from esp_idf_defs.otadata import OtaDataParameters
from esp_idf_defs.partitions import PartitionDefinition, APP_TYPE, DATA_TYPE, SUBTYPES, TYPES, \
    print_partition_table as print_plain_partition_table

#: Type name → colour of its Type cell.
TYPE_STYLES = {"app": "cyan", "data": "magenta", "bootloader": "yellow", "partition_table": "yellow"}


def _keyword(value: int, keywords: dict) -> str:
    return next((k for k, v in keywords.items() if v == value), str(value))


def _size(size: int) -> str:
    for unit, suffix in ((0x100000, "M"), (0x400, "K")):
        if size % unit == 0:
            return f"{size // unit}{suffix}"
    return f"0x{size:x}"


def print_partition_table(partition_table: list[PartitionDefinition],
                          read: Optional[Callable[[int, int], bytes]] = None,
                          otadata: Optional[OtaDataParameters] = None) -> None:
    """Print `partition_table`, with app descriptions if `read` is given and the active
    app marked if `otadata` is."""
    from rich import box
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = Console(file=sys.stdout, highlight=False)
    if not console.is_terminal:
        print_plain_partition_table(partition_table, read, otadata=otadata)
        return

    has_flags = any(part.get_flags_list() for part in partition_table)
    active = (SUBTYPES[APP_TYPE]["ota_0"] + otadata.slot
              if otadata is not None and otadata.slot is not None else None)

    table = Table(box=box.ROUNDED, border_style="dim", header_style="bold", pad_edge=True)
    table.add_column("Name", style="bold", overflow="ellipsis")
    table.add_column("Type", overflow="ellipsis")
    table.add_column("Subtype", overflow="ellipsis")
    table.add_column("Offset", justify="right", no_wrap=True)
    table.add_column("Size", justify="right", no_wrap=True)
    if has_flags:
        table.add_column("Flags", overflow="ellipsis")
    if read:
        table.add_column("App")

    for part in partition_table:
        type_name = _keyword(part.type, TYPES)
        subtype = Text(_keyword(part.subtype, SUBTYPES.get(part.type, {})))
        if otadata is not None and part.type == DATA_TYPE and part.subtype == SUBTYPES[DATA_TYPE]["ota"]:
            subtype.append(f" ({otadata.a_or_b.upper()})" if otadata.a_or_b else " (invalid)",
                           style=None if otadata.a_or_b else "red")
        is_active = part.type == APP_TYPE and part.subtype == active
        cells = [Text(part.name, style="green" if is_active else ""),
                 Text(type_name, style=TYPE_STYLES.get(type_name, "")),
                 subtype,
                 Text(f"0x{part.offset:x}", style="dim"),
                 Text(_size(part.size))]
        if has_flags:
            cells.append(Text(":".join(part.get_flags_list())))
        if read:
            description = Text()
            if part.type == APP_TYPE:
                try:
                    found = AppDescription.from_bytes_or_none(read(
                        part.offset + AppDescription.FIRMWARE_BINARY_OFFSET, AppDescription.SIZE))
                    description.append(found.title if found else "empty", style="" if found else "dim")
                except Exception:  # noqa: BLE001
                    # Reads fail for reasons unrelated to the table (comms errors, protected
                    # regions); flag the row instead of taking down the caller.
                    description.append("read error", style="red")
            if is_active:
                description.append(" ● active", style="green")
            cells.append(description)
        table.add_row(*cells)

    console.print(table)
