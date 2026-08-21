"""The NVS on-flash layout, and the types shared by the NVS modules.

Kept out of ``idftool.nvs`` itself so the parser and the editor can import them without
an import cycle, the same way :mod:`idftool.fs.common` serves the filesystem backends.

An NVS partition is a sequence of 4 KiB pages, each laid out as::

    +-------+------+-------------------------------------------------------+
    | 0x00  |   32 | page header: state, sequence number, version, CRC32    |
    | 0x20  |   32 | entry state bitmap — 2 bits per entry, 126 entries used |
    | 0x40  | 4032 | 126 entries of 32 bytes                                |
    +-------+------+-------------------------------------------------------+

and each entry as::

    +-------+------+-------------------------------------------------------+
    |   0   |    1 | namespace index (0 addresses the namespace table)      |
    |   1   |    1 | type (see TYPES)                                       |
    |   2   |    1 | span — entries this item occupies, including this one  |
    |   3   |    1 | chunk index (CHUNK_ANY unless this is blob data)       |
    |   4   |    4 | CRC32 of bytes [0:4] + [8:32], seeded 0xFFFFFFFF       |
    |   8   |   16 | key, NUL-padded                                        |
    |  24   |    8 | data: the value itself, or a descriptor of what follows|
    +-------+------+-------------------------------------------------------+

A primitive holds its value in the data field. A string or a v1 blob puts its length in
data[0:2] and the payload CRC32 in data[4:8], with the payload in the ``span - 1`` entries
that follow. A v2 blob is split: ``BLOB_DATA`` chunks each carry a slice of the payload and
may be spread across pages, and a single ``BLOB_IDX`` entry names the total size, the number
of chunks, and the index the chunks start at.

NVS never rewrites an entry in place — flash bits only go 1→0. An update appends a new entry
and flips the old one's bitmap state from WRITTEN to ERASED, which is why a parse has to walk
the bitmap rather than just reading entries end to end.
"""
import struct
from dataclasses import dataclass, field
from typing import Any, Optional, Union

#: NVS partitions are laid out as 4 KiB pages.
PAGE_SIZE = 0x1000
HEADER_SIZE = 32
BITMAP_OFFSET = 32
BITMAP_SIZE = 32
FIRST_ENTRY_OFFSET = 64
ENTRY_SIZE = 32
MAX_ENTRIES = 126

#: Chunk index used by everything that is not a blob data chunk.
CHUNK_ANY = 0xFF

#: Page header versions.
VERSION1 = 0xFF
VERSION2 = 0xFE
VERSIONS = {VERSION1: 1, VERSION2: 2}

# Page states. Each transition only clears bits, so an erased (0xFFFFFFFF) page is UNINIT.
PAGE_UNINIT = 0xFFFFFFFF
PAGE_ACTIVE = 0xFFFFFFFE
PAGE_FULL = 0xFFFFFFFC
PAGE_FREEING = 0xFFFFFFF8
PAGE_CORRUPT = 0xFFFFFFF0

PAGE_STATES = {
    PAGE_UNINIT: 'uninitialised',
    PAGE_ACTIVE: 'active',
    PAGE_FULL: 'full',
    PAGE_FREEING: 'freeing',
    PAGE_CORRUPT: 'corrupt',
}

# Entry states, two bits each, again only ever clearing bits.
ENTRY_EMPTY = 0b11
ENTRY_WRITTEN = 0b10
ENTRY_ERASED = 0b00
ENTRY_ILLEGAL = 0b01

ENTRY_STATES = {
    ENTRY_EMPTY: 'empty',
    ENTRY_WRITTEN: 'written',
    ENTRY_ERASED: 'erased',
    ENTRY_ILLEGAL: 'illegal',
}

# Item type codes, matching nvs_partition_gen's Page class.
TYPE_U8 = 0x01
TYPE_I8 = 0x11
TYPE_U16 = 0x02
TYPE_I16 = 0x12
TYPE_U32 = 0x04
TYPE_I32 = 0x14
TYPE_U64 = 0x08
TYPE_I64 = 0x18
TYPE_SZ = 0x21
TYPE_BLOB = 0x41
TYPE_BLOB_DATA = 0x42
TYPE_BLOB_IDX = 0x48

#: Type code → the name used on the command line and in CSV's ``encoding`` column.
TYPES = {
    TYPE_U8: 'u8', TYPE_I8: 'i8',
    TYPE_U16: 'u16', TYPE_I16: 'i16',
    TYPE_U32: 'u32', TYPE_I32: 'i32',
    TYPE_U64: 'u64', TYPE_I64: 'i64',
    TYPE_SZ: 'string',
    TYPE_BLOB: 'blob', TYPE_BLOB_DATA: 'blob_data', TYPE_BLOB_IDX: 'blob_idx',
}

#: The inverse, for the names a user can actually ask for.
TYPE_CODES = {name: code for code, name in TYPES.items()
              if code not in (TYPE_BLOB_DATA, TYPE_BLOB_IDX)}

#: Primitive name → (struct format, width in bytes).
PRIMITIVES = {
    'u8': ('<B', 1), 'i8': ('<b', 1),
    'u16': ('<H', 2), 'i16': ('<h', 2),
    'u32': ('<I', 4), 'i32': ('<i', 4),
    'u64': ('<Q', 8), 'i64': ('<q', 8),
}

#: Types whose payload lives in the entries after the header.
VARLEN = ('string', 'blob')

#: NVS truncates keys at 15 characters plus a NUL.
MAX_KEY_LEN = 15


class NvsError(RuntimeError):
    """An NVS image could not be parsed, edited, or fitted to a partition."""


def entry_state(bitmap: bytes, index: int) -> int:
    """Read the two-bit state of entry `index` out of a page's state bitmap."""
    bit = index * 2
    return (bitmap[bit // 8] >> (bit & 7)) & 0b11


def set_entry_state(bitmap: bytearray, index: int, state: int) -> None:
    """Write the two-bit state of entry `index` into a page's state bitmap.

    Only clears bits, matching what flash can actually do: EMPTY → WRITTEN → ERASED.
    """
    bit = index * 2
    byte, offset = bit // 8, bit & 7
    bitmap[byte] &= ~(0b11 << offset) & 0xFF
    bitmap[byte] |= (state & 0b11) << offset


def entry_crc(entry: bytes) -> int:
    """The CRC32 an entry header should carry, over bytes [0:4] + [8:32]."""
    import zlib
    return zlib.crc32(bytes(entry[0:4]) + bytes(entry[8:32]), 0xFFFFFFFF) & 0xFFFFFFFF


def data_crc(payload: bytes) -> int:
    """The CRC32 a variable-length item's payload should carry."""
    import zlib
    return zlib.crc32(bytes(payload), 0xFFFFFFFF) & 0xFFFFFFFF


def header_crc(header: bytes) -> int:
    """The CRC32 a page header should carry, over bytes [4:28]."""
    import zlib
    return zlib.crc32(bytes(header[4:28]), 0xFFFFFFFF) & 0xFFFFFFFF


def entries_for(size: int) -> int:
    """How many 32-byte entries a payload of `size` bytes occupies."""
    return (size + ENTRY_SIZE - 1) // ENTRY_SIZE


@dataclass
class RawEntry:
    """One 32-byte entry as it sits on flash, before blob chunks are reassembled."""
    page: int           # index of the page within the image
    index: int          # index of the entry within the page
    state: int
    ns_index: int
    type: int
    span: int
    chunk_index: int
    key: str
    data: bytes         # the 8-byte data field
    crc_ok: bool
    #: Payload bytes for a string / v1 blob / blob chunk, already trimmed to its length.
    payload: Optional[bytes] = None
    payload_crc_ok: Optional[bool] = None

    @property
    def type_name(self) -> str:
        return TYPES.get(self.type, f'0x{self.type:02x}')

    @property
    def offset(self) -> int:
        """Byte offset of this entry from the start of the image."""
        return self.page * PAGE_SIZE + FIRST_ENTRY_OFFSET + self.index * ENTRY_SIZE


@dataclass
class NvsPage:
    """One 4 KiB page, parsed."""
    index: int
    state: int
    seq: int
    version: int
    crc_ok: bool
    entry_states: list[int] = field(default_factory=list)
    entries: list[RawEntry] = field(default_factory=list)

    @property
    def is_uninit(self) -> bool:
        return self.state == PAGE_UNINIT

    @property
    def state_name(self) -> str:
        return PAGE_STATES.get(self.state, f'0x{self.state:08x}')

    @property
    def used_entries(self) -> int:
        """Entries that are no longer EMPTY — NVS only ever appends, so this is a high-water mark."""
        for i in range(MAX_ENTRIES - 1, -1, -1):
            if self.entry_states[i] != ENTRY_EMPTY:
                return i + 1
        return 0

    @property
    def free_entries(self) -> int:
        return MAX_ENTRIES - self.used_entries


@dataclass
class NvsEntry:
    """One logical key/value pair, with its blob chunks already stitched together."""
    namespace: str
    key: str
    type: str
    value: Any
    size: int           # payload bytes on the wire (the declared width for a primitive)
    ns_index: int
    #: The entry headers backing this item — one, plus a chunk header per v2 blob chunk.
    #: Each covers ``span`` consecutive entries starting at its own index.
    raw: list[RawEntry] = field(default_factory=list)

    @property
    def page(self) -> int:
        return self.raw[0].page if self.raw else -1

    @property
    def qualified(self) -> str:
        return f'{self.namespace}:{self.key}'

    def format_value(self, limit: int = 48) -> str:
        """Render the value for a listing, abbreviating anything long."""
        if isinstance(self.value, bytes):
            text = self.value.hex()
            return text if len(text) <= limit else f'{text[:limit]}… ({self.size} bytes)'
        text = str(self.value)
        return text if len(text) <= limit else f'{text[:limit]}…'


@dataclass
class NvsImage:
    """A parsed NVS partition image."""
    data: bytes
    pages: list[NvsPage] = field(default_factory=list)
    entries: list[NvsEntry] = field(default_factory=list)
    #: namespace index → name, from the entries with ns_index 0.
    namespaces: dict[int, str] = field(default_factory=dict)
    #: Anything that did not parse cleanly. Empty unless the image is damaged.
    errors: list[str] = field(default_factory=list)
    version: int = VERSION2

    @property
    def size(self) -> int:
        return len(self.data)

    def get(self, namespace: str, key: str) -> Optional[NvsEntry]:
        return next((e for e in self.entries
                     if e.namespace == namespace and e.key == key), None)

    def namespace_index(self, namespace: str) -> Optional[int]:
        return next((i for i, n in self.namespaces.items() if n == namespace), None)


Value = Union[int, str, bytes]


def pack_primitive(type_name: str, value: int) -> bytes:
    """Pack a primitive into the 8-byte data field, padded with erased flash."""
    fmt, width = PRIMITIVES[type_name]
    try:
        packed = struct.pack(fmt, value)
    except struct.error as e:
        raise NvsError(f"Value {value} does not fit in {type_name}: {e}") from e
    return packed + b'\xff' * (8 - width)


def unpack_primitive(type_name: str, data: bytes) -> int:
    fmt, width = PRIMITIVES[type_name]
    return struct.unpack(fmt, data[:width])[0]
