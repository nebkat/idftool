"""Changing key/value pairs in an existing NVS image.

NVS is log-structured: flash bits only go 1→0, so an update never rewrites an entry where
it sits. It appends a new entry and flips the old one's bitmap state from WRITTEN to ERASED,
and that is exactly what :func:`apply` does — the same thing the firmware would do, which
keeps everything else in the partition byte-for-byte identical and lets a device write touch
only the pages that actually changed.

When there is no room left to append, the firmware garbage-collects. Rather than reimplement
that, :func:`apply` falls back to :func:`rewrite`, which regenerates a compacted image from
the parsed contents. ``--rewrite`` asks for that path up front.

Driven by ``set-nvs`` in :mod:`idftool.commands.nvs`.
"""
import io
import struct
from dataclasses import dataclass
from typing import Optional, Union

from idftool.nvs.common import (
    BITMAP_OFFSET, BITMAP_SIZE, CHUNK_ANY, ENTRY_ERASED, ENTRY_SIZE, ENTRY_WRITTEN,
    FIRST_ENTRY_OFFSET, HEADER_SIZE, MAX_ENTRIES, MAX_KEY_LEN, PAGE_ACTIVE, PAGE_FULL,
    PAGE_SIZE, PAGE_UNINIT, PRIMITIVES, TYPE_BLOB, TYPE_BLOB_DATA, TYPE_BLOB_IDX, TYPE_SZ,
    VERSION1, VERSION2, NvsEntry, NvsError, NvsImage,
    data_crc, entries_for, entry_crc, header_crc, pack_primitive, set_entry_state,
)
from idftool.nvs.parser import parse

#: Blob chunk indices start at one of two offsets, so a half-written replacement can never be
#: confused with the copy it replaces. ESP-IDF calls these VER_0_OFFSET and VER_1_OFFSET.
VER_0_OFFSET = 0x00
VER_1_OFFSET = 0x80

#: The largest string NVS will store, both versions (a string never spans pages).
MAX_STRING_SIZE = {VERSION1: 1984, VERSION2: 4000}


class NoSpaceError(NvsError):
    """The image has no room left to append — the caller should compact instead."""


@dataclass
class Edit:
    """One requested change. `value` is None for a delete, `type` None to infer it."""
    namespace: str
    key: str
    type: Optional[str] = None
    value: Optional[Union[int, str, bytes]] = None

    @property
    def is_delete(self) -> bool:
        return self.value is None

    @property
    def qualified(self) -> str:
        return f'{self.namespace}:{self.key}'


@dataclass
class Change:
    """What actually happened to one edit, for reporting."""
    edit: Edit
    action: str          # 'set', 'added', 'deleted', 'unchanged'
    before: Optional[NvsEntry] = None
    type: Optional[str] = None


# --------------------------------------------------------------------------------------
# Encoding entries
# --------------------------------------------------------------------------------------

def _entry_header(ns_index: int, type_code: int, span: int, chunk_index: int,
                  key: str) -> bytearray:
    entry = bytearray(b'\xff' * ENTRY_SIZE)
    entry[0] = ns_index
    entry[1] = type_code
    entry[2] = span
    entry[3] = chunk_index
    entry[8:24] = b'\x00' * 16
    entry[8:8 + len(key.encode())] = key.encode()
    return entry


def _seal(entry: bytearray) -> bytes:
    struct.pack_into('<I', entry, 4, entry_crc(entry))
    return bytes(entry)


def _pad(payload: bytes) -> bytes:
    """Pad a payload out to a whole number of entries with erased flash."""
    return payload + b'\xff' * (entries_for(len(payload)) * ENTRY_SIZE - len(payload))


def encode_primitive(ns_index: int, key: str, type_name: str, value: int) -> bytes:
    from idftool.nvs.common import TYPE_CODES
    entry = _entry_header(ns_index, TYPE_CODES[type_name], 1, CHUNK_ANY, key)
    entry[24:32] = pack_primitive(type_name, value)
    return _seal(entry)


def encode_varlen(ns_index: int, key: str, type_code: int, payload: bytes,
                  chunk_index: int = CHUNK_ANY) -> bytes:
    """A string, a v1 blob, or a v2 blob chunk: a header entry followed by its payload."""
    span = 1 + entries_for(len(payload))
    entry = _entry_header(ns_index, type_code, span, chunk_index, key)
    struct.pack_into('<H', entry, 24, len(payload))
    struct.pack_into('<I', entry, 28, data_crc(payload))
    return _seal(entry) + _pad(payload)


def encode_blob_index(ns_index: int, key: str, total: int, chunk_count: int,
                      chunk_start: int) -> bytes:
    entry = _entry_header(ns_index, TYPE_BLOB_IDX, 1, CHUNK_ANY, key)
    struct.pack_into('<I', entry, 24, total)
    entry[28] = chunk_count
    entry[29] = chunk_start
    return _seal(entry)


# --------------------------------------------------------------------------------------
# Appending into an image's free space
# --------------------------------------------------------------------------------------

class _Writer:
    """Appends entries into the pages of a mutable image, the way the firmware would.

    Fills the active page, then marks it FULL and initialises the next erased page. The very
    last page is left alone: NVS needs one free page in hand to garbage-collect at runtime,
    and ``nvs_partition_gen`` reserves it for the same reason.
    """

    def __init__(self, data: bytearray, image: NvsImage):
        self.data = data
        self.version = image.version if image.version in (VERSION1, VERSION2) else VERSION2
        self.page_count = len(data) // PAGE_SIZE
        # Usable pages, keeping one in reserve for the firmware's garbage collector — except on
        # a partition under 0x3000, which has none to spare and is read-only to the firmware
        # anyway. Same rule nvs_partition_gen applies (see _generator_size).
        self.usable = self.page_count - 1 if self.page_count >= 3 else self.page_count
        self.states = [page.state for page in image.pages]
        self.used = [page.used_entries if not page.is_uninit else 0 for page in image.pages]
        seqs = [page.seq for page in image.pages if not page.is_uninit]
        self.next_seq = max(seqs) + 1 if seqs else 0

    # -- page helpers ------------------------------------------------------------------

    def _page_state(self, index: int, state: int) -> None:
        struct.pack_into('<I', self.data, index * PAGE_SIZE, state)
        self.states[index] = state

    def _init_page(self, index: int) -> None:
        """Turn an erased page into an ACTIVE one with a fresh sequence number."""
        header = bytearray(b'\xff' * HEADER_SIZE)
        struct.pack_into('<I', header, 0, PAGE_ACTIVE)
        struct.pack_into('<I', header, 4, self.next_seq)
        header[8] = self.version
        struct.pack_into('<I', header, 28, header_crc(header))
        base = index * PAGE_SIZE
        self.data[base:base + HEADER_SIZE] = header
        self.states[index] = PAGE_ACTIVE
        self.used[index] = 0
        self.next_seq += 1

    def _room(self, index: int) -> int:
        return MAX_ENTRIES - self.used[index]

    def page_with_room(self, entries: int) -> int:
        """Find (or open) a page with `entries` free entries, marking full pages FULL."""
        if entries > MAX_ENTRIES:
            raise NoSpaceError(f"an item of {entries} entries cannot fit in a "
                               f"{PAGE_SIZE:#x}-byte page")
        for index in range(self.usable):
            if self.states[index] == PAGE_ACTIVE and self._room(index) >= entries:
                return index
        # Nothing active has room. Retire the active pages and open the next erased one.
        for index in range(self.usable):
            if self.states[index] == PAGE_ACTIVE:
                self._page_state(index, PAGE_FULL)
        for index in range(self.usable):
            if self.states[index] == PAGE_UNINIT:
                self._init_page(index)
                return index
        raise NoSpaceError("no free pages left to append to")

    # -- writing -----------------------------------------------------------------------

    def append(self, index: int, encoded: bytes) -> None:
        count = len(encoded) // ENTRY_SIZE
        first = self.used[index]
        base = index * PAGE_SIZE
        start = base + FIRST_ENTRY_OFFSET + first * ENTRY_SIZE
        self.data[start:start + len(encoded)] = encoded

        bitmap = bytearray(self.data[base + BITMAP_OFFSET:base + BITMAP_OFFSET + BITMAP_SIZE])
        for n in range(count):
            set_entry_state(bitmap, first + n, ENTRY_WRITTEN)
        self.data[base + BITMAP_OFFSET:base + BITMAP_OFFSET + BITMAP_SIZE] = bitmap
        self.used[index] = first + count

    def erase(self, entry: NvsEntry) -> None:
        """Mark every entry backing `entry` — headers, payload, blob chunks — as erased."""
        for raw in entry.raw:
            base = raw.page * PAGE_SIZE
            bitmap = bytearray(
                self.data[base + BITMAP_OFFSET:base + BITMAP_OFFSET + BITMAP_SIZE])
            for n in range(raw.span):
                set_entry_state(bitmap, raw.index + n, ENTRY_ERASED)
            self.data[base + BITMAP_OFFSET:base + BITMAP_OFFSET + BITMAP_SIZE] = bitmap

    # -- items -------------------------------------------------------------------------

    def write_namespace(self, name: str, index: int) -> None:
        page = self.page_with_room(1)
        self.append(page, encode_primitive(0, name, 'u8', index))

    def write_item(self, ns_index: int, key: str, type_name: str, value,
                   previous: Optional[NvsEntry] = None) -> None:
        if type_name in PRIMITIVES:
            page = self.page_with_room(1)
            self.append(page, encode_primitive(ns_index, key, type_name, value))
        elif type_name == 'string':
            # NVS stores strings NUL-terminated, and never splits one across pages.
            payload = value.encode() + b'\x00'
            limit = MAX_STRING_SIZE[self.version]
            if len(payload) > limit:
                raise NvsError(f"string '{key}' is {len(payload)} bytes, over the "
                               f"{limit}-byte NVS limit")
            page = self.page_with_room(1 + entries_for(len(payload)))
            self.append(page, encode_varlen(ns_index, key, TYPE_SZ, payload))
        elif type_name == 'blob':
            self._write_blob(ns_index, key, value, previous)
        else:
            raise NvsError(f"Cannot write type '{type_name}'")

    def _write_blob(self, ns_index: int, key: str, payload: bytes,
                    previous: Optional[NvsEntry]) -> None:
        if self.version == VERSION1:
            limit = MAX_STRING_SIZE[VERSION1]
            if len(payload) > limit:
                raise NvsError(f"blob '{key}' is {len(payload)} bytes, over the {limit}-byte "
                               f"limit for a version 1 NVS partition")
            page = self.page_with_room(1 + entries_for(len(payload)))
            self.append(page, encode_varlen(ns_index, key, TYPE_BLOB, payload))
            return

        # Version 2 splits a blob into chunks that may live on different pages, indexed by a
        # final BLOB_IDX entry. Start the chunk numbering at whichever offset the copy being
        # replaced did not use, so the two generations can never be mistaken for each other.
        chunk_start = VER_0_OFFSET
        if previous is not None:
            old_starts = {raw.chunk_index & VER_1_OFFSET for raw in previous.raw
                          if raw.type == TYPE_BLOB_DATA}
            if VER_0_OFFSET in old_starts:
                chunk_start = VER_1_OFFSET

        remaining = payload
        chunks = 0
        while remaining or chunks == 0:
            # A chunk needs its header plus at least one data entry to be worth placing.
            page = self.page_with_room(2)
            capacity = (self._room(page) - 1) * ENTRY_SIZE
            chunk, remaining = remaining[:capacity], remaining[capacity:]
            self.append(page, encode_varlen(ns_index, key, TYPE_BLOB_DATA, chunk,
                                            chunk_start + chunks))
            chunks += 1
            if chunks > 0xFF - VER_1_OFFSET:
                raise NoSpaceError(f"blob '{key}' needs more chunks than NVS can index")

        page = self.page_with_room(1)
        self.append(page, encode_blob_index(ns_index, key, len(payload), chunks, chunk_start))


# --------------------------------------------------------------------------------------
# Applying edits
# --------------------------------------------------------------------------------------

def _resolve(image: NvsImage, edit: Edit) -> tuple[Optional[NvsEntry], str]:
    """Pair an edit with the entry it replaces, and settle on the type to write."""
    existing = image.get(edit.namespace, edit.key)
    if edit.is_delete:
        return existing, existing.type if existing else ''
    if edit.type:
        return existing, edit.type
    if existing is None:
        raise NvsError(
            f"'{edit.qualified}' is not in the image, so there is no type to infer — write it "
            f"as {edit.namespace}:{edit.key}:<type>=<value> "
            f"(types: {', '.join(list(PRIMITIVES) + ['string', 'blob'])})")
    return existing, existing.type


def _same(entry: NvsEntry, type_name: str, value) -> bool:
    return entry.type == type_name and entry.value == value


def apply(data: bytes, edits: list[Edit], *, force_rewrite: bool = False
          ) -> tuple[bytes, list[Change], list[int], bool]:
    """Apply `edits` to an NVS image.

    Returns the new image, what changed, the indices of the pages that differ from the
    original, and whether the image had to be compacted rather than appended to.
    """
    image = parse(data)
    if image.errors:
        raise NvsError("Refusing to edit a damaged NVS image:\n  " +
                       "\n  ".join(image.errors))

    for edit in edits:
        if len(edit.key.encode()) > MAX_KEY_LEN:
            raise NvsError(f"Key '{edit.key}' is longer than the {MAX_KEY_LEN}-character "
                           f"NVS limit")

    def compact():
        # _append mutates the model it walks, so a fallback always starts from a fresh parse
        # rather than whatever state the abandoned append left behind.
        pristine = parse(data)
        return (rewrite(pristine, edits), _plan(pristine, edits),
                list(range(len(data) // PAGE_SIZE)), True)

    if force_rewrite:
        return compact()

    try:
        result, changes = _append(data, image, edits)
    except NoSpaceError:
        return compact()

    dirty = [i for i in range(len(data) // PAGE_SIZE)
             if data[i * PAGE_SIZE:(i + 1) * PAGE_SIZE] != result[i * PAGE_SIZE:(i + 1) * PAGE_SIZE]]
    return result, changes, dirty, False


def _plan(image: NvsImage, edits: list[Edit]) -> list[Change]:
    """Work out what each edit will do, without touching the image."""
    changes = []
    for edit in edits:
        existing, type_name = _resolve(image, edit)
        if edit.is_delete:
            changes.append(Change(edit, 'deleted' if existing else 'unchanged', existing,
                                  type_name))
        elif existing is None:
            changes.append(Change(edit, 'added', None, type_name))
        elif _same(existing, type_name, edit.value):
            changes.append(Change(edit, 'unchanged', existing, type_name))
        else:
            changes.append(Change(edit, 'set', existing, type_name))
    return changes


def _append(data: bytes, image: NvsImage, edits: list[Edit]) -> tuple[bytes, list[Change]]:
    buffer = bytearray(data)
    writer = _Writer(buffer, image)
    namespaces = dict(image.namespaces)
    changes: list[Change] = []

    for edit in edits:
        existing, type_name = _resolve(image, edit)

        if edit.is_delete:
            if existing is None:
                changes.append(Change(edit, 'unchanged', None, type_name))
                continue
            writer.erase(existing)
            changes.append(Change(edit, 'deleted', existing, type_name))
            image.entries.remove(existing)
            continue

        if existing is not None and _same(existing, type_name, edit.value):
            # Writing an identical value would burn entries for nothing.
            changes.append(Change(edit, 'unchanged', existing, type_name))
            continue

        ns_index = image.namespace_index(edit.namespace)
        if ns_index is None:
            ns_index = max(namespaces, default=0) + 1
            if ns_index > 0xFE:
                raise NoSpaceError("no namespace indices left")
            writer.write_namespace(edit.namespace, ns_index)
            namespaces[ns_index] = edit.namespace
            image.namespaces[ns_index] = edit.namespace

        # Write the replacement before erasing what it replaces: if there turns out to be no
        # room, the original image is still intact and we fall back to compacting.
        writer.write_item(ns_index, edit.key, type_name, edit.value, previous=existing)
        if existing is not None:
            writer.erase(existing)
            image.entries.remove(existing)
        changes.append(Change(edit, 'set' if existing else 'added', existing, type_name))
        image.entries.append(NvsEntry(namespace=edit.namespace, key=edit.key, type=type_name,
                                      value=edit.value, size=0, ns_index=ns_index))

    return bytes(buffer), changes


def _generator_size(size: int) -> int:
    """The size to hand ``nvs_open`` so it emits a partition of exactly `size` bytes.

    ``nvs_partition_gen`` treats its size argument as the *writable* space and adds a page on
    top, reserved for the firmware's garbage collector — except on a partition too small to
    spare one, which it builds read-only instead. Mirrors ``nvs_partition_gen.check_size``.
    """
    if size < PAGE_SIZE:
        raise NvsError(f"An NVS partition must be at least {PAGE_SIZE:#x} bytes, not {size:#x}")
    return size if size - PAGE_SIZE < 2 * PAGE_SIZE else size - PAGE_SIZE


def rewrite(image: NvsImage, edits: list[Edit]) -> bytes:
    """Regenerate a compacted image from an image's contents plus `edits`.

    Used when there is no room to append. Goes through ``nvs_partition_gen``'s ``NVS`` class
    directly rather than its module-level ``write_entry`` helper, which injects extra
    hard-coded Wi-Fi entries for keys named ``sta.ssid``/``ap.ssid`` and friends — fine when
    generating from a hand-written CSV, wrong when reproducing an image.
    """
    from esp_idf_nvs_partition_gen import nvs_partition_gen as gen

    entries = {(e.namespace, e.key): e for e in image.entries}
    order: list[tuple[str, str]] = [(e.namespace, e.key) for e in image.entries]

    for edit in edits:
        _, type_name = _resolve(image, edit)
        key = (edit.namespace, edit.key)
        if edit.is_delete:
            entries.pop(key, None)
            if key in order:
                order.remove(key)
            continue
        if key not in entries:
            order.append(key)
        entries[key] = NvsEntry(namespace=edit.namespace, key=edit.key, type=type_name,
                                value=edit.value, size=0,
                                ns_index=image.namespace_index(edit.namespace) or 0)

    # Namespaces in their original index order, so the rebuilt image reads like the old one.
    namespaces: list[str] = []
    for index in sorted(image.namespaces):
        namespaces.append(image.namespaces[index])
    for namespace, _ in order:
        if namespace not in namespaces:
            namespaces.append(namespace)

    out = io.BytesIO()
    version = image.version if image.version in (VERSION1, VERSION2) else VERSION2
    with gen.nvs_open(out, _generator_size(image.size), version) as nvs:
        for namespace in namespaces:
            keys = [k for ns, k in order if ns == namespace]
            if not keys:
                continue
            nvs.write_namespace(namespace)
            for key in keys:
                entry = entries[(namespace, key)]
                if entry.type == 'string':
                    nvs.write_entry(key, entry.value, 'string')
                elif entry.type == 'blob':
                    nvs.write_entry(key, entry.value.hex(), 'hex2bin')
                else:
                    nvs.write_entry(key, int(entry.value), entry.type)

    result = out.getvalue()
    if len(result) > image.size:
        raise NvsError(f"Compacted image is {len(result):#x} bytes, larger than the "
                       f"{image.size:#x}-byte partition")
    return result + b'\xff' * (image.size - len(result))
