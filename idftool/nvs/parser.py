"""Reading an NVS partition image back into key/value pairs.

``esp-idf-nvs-partition-gen`` only writes — its ``NVS``/``Page`` classes build pages from
scratch and append to them, and there is no way to load an existing image. Everything that
reads an NVS partition — ``print-nvs``, ``extract-nvs``, ``get-nvs``, and the read half of
``set-nvs`` — goes through :func:`parse` here instead. The layout it walks is documented in
:mod:`idftool.nvs.common`.

The parse is deliberately forgiving: a page or an entry that fails its CRC is recorded in
``NvsImage.errors`` and skipped rather than aborting, because an image read off a live device
can legitimately contain a half-written entry — the firmware ignores those too. Pass
``strict=True`` to turn them into errors.
"""
import struct

from idftool.nvs.common import (
    BITMAP_OFFSET, BITMAP_SIZE, ENTRY_ERASED, ENTRY_SIZE, ENTRY_STATES,
    ENTRY_WRITTEN, FIRST_ENTRY_OFFSET, HEADER_SIZE, MAX_ENTRIES, PAGE_SIZE, PAGE_STATES,
    PAGE_UNINIT, PRIMITIVES, TYPE_BLOB, TYPE_BLOB_DATA, TYPE_BLOB_IDX, TYPE_SZ, TYPES,
    VERSIONS, NvsEntry, NvsError, NvsImage, NvsPage, RawEntry,
    data_crc, entry_crc, entry_state, header_crc, unpack_primitive,
)


def _decode_key(raw: bytes) -> str:
    """Decode a 16-byte NUL-padded key field."""
    return raw.split(b'\x00', 1)[0].decode('utf-8', errors='replace')


def _parse_page(image: bytes, index: int, errors: list[str], strict: bool) -> NvsPage:
    base = index * PAGE_SIZE
    raw = image[base:base + PAGE_SIZE]
    header = raw[:HEADER_SIZE]

    state, seq = struct.unpack('<II', header[:8])
    version = header[8]
    stored_crc = struct.unpack('<I', header[28:32])[0]

    if state == PAGE_UNINIT:
        # An erased page. Nothing else in it is meaningful, and its sequence number is 0xFFFFFFFF
        # rather than a real one, so don't let it sort with the written pages.
        return NvsPage(index=index, state=state, seq=seq, version=version, crc_ok=True,
                       entry_states=[0b11] * MAX_ENTRIES)

    page = NvsPage(index=index, state=state, seq=seq, version=version,
                   crc_ok=stored_crc == header_crc(header))
    if not page.crc_ok:
        _fail(errors, strict, f"page {index}: header CRC mismatch "
                              f"(stored {stored_crc:#010x}, computed {header_crc(header):#010x})")
        return page
    if state not in PAGE_STATES:
        _fail(errors, strict, f"page {index}: unknown page state {state:#010x}")
    if version not in VERSIONS:
        _fail(errors, strict, f"page {index}: unknown version byte {version:#04x}")
        return page

    bitmap = raw[BITMAP_OFFSET:BITMAP_OFFSET + BITMAP_SIZE]
    page.entry_states = [entry_state(bitmap, i) for i in range(MAX_ENTRIES)]

    def entry_bytes(i: int) -> bytes:
        start = FIRST_ENTRY_OFFSET + i * ENTRY_SIZE
        return raw[start:start + ENTRY_SIZE]

    i = 0
    while i < MAX_ENTRIES:
        # Only WRITTEN entries are item headers. An erased item leaves its payload entries
        # marked erased too, so stepping one at a time never mistakes payload for a header.
        if page.entry_states[i] != ENTRY_WRITTEN:
            i += 1
            continue

        data = entry_bytes(i)
        ns_index, item_type, span, chunk_index = data[0], data[1], data[2], data[3]
        stored = struct.unpack('<I', data[4:8])[0]
        entry = RawEntry(
            page=index, index=i, state=ENTRY_WRITTEN, ns_index=ns_index, type=item_type,
            span=span, chunk_index=chunk_index, key=_decode_key(data[8:24]), data=data[24:32],
            crc_ok=stored == entry_crc(data),
        )

        if not entry.crc_ok:
            _fail(errors, strict, f"page {index} entry {i}: header CRC mismatch")
            i += 1
            continue
        if item_type not in TYPES:
            _fail(errors, strict, f"page {index} entry {i} ('{entry.key}'): "
                                  f"unknown type {item_type:#04x}")
            i += 1
            continue
        if span < 1 or i + span > MAX_ENTRIES:
            _fail(errors, strict, f"page {index} entry {i} ('{entry.key}'): "
                                  f"span {span} runs past the end of the page")
            i += 1
            continue

        if item_type in (TYPE_SZ, TYPE_BLOB, TYPE_BLOB_DATA):
            length = struct.unpack('<H', entry.data[0:2])[0]
            expected = struct.unpack('<I', entry.data[4:8])[0]
            payload = raw[FIRST_ENTRY_OFFSET + (i + 1) * ENTRY_SIZE:
                          FIRST_ENTRY_OFFSET + (i + span) * ENTRY_SIZE][:length]
            if len(payload) != length:
                _fail(errors, strict, f"page {index} entry {i} ('{entry.key}'): declared length "
                                      f"{length} exceeds its span of {span - 1} data entries")
                i += span
                continue
            entry.payload = payload
            entry.payload_crc_ok = data_crc(payload) == expected
            if not entry.payload_crc_ok:
                _fail(errors, strict, f"page {index} entry {i} ('{entry.key}'): data CRC mismatch")

        page.entries.append(entry)
        i += span

    return page


def _fail(errors: list[str], strict: bool, message: str) -> None:
    if strict:
        raise NvsError(message)
    errors.append(message)


def _reassemble(pages: list[NvsPage], errors: list[str], strict: bool) -> tuple[dict[int, str],
                                                                               list[NvsEntry]]:
    """Turn the raw entries of every page into namespaces and logical key/value pairs."""
    # Pages in the order the firmware wrote them, so a later duplicate of a key wins.
    ordered = sorted((p for p in pages if not p.is_uninit and p.crc_ok), key=lambda p: p.seq)
    raw_entries = [e for page in ordered for e in page.entries]

    # Namespace table first: every other entry's ns_index refers into it.
    namespaces: dict[int, str] = {}
    for entry in raw_entries:
        if entry.ns_index == 0 and entry.type in PRIMITIVES_BY_CODE:
            index = unpack_primitive(TYPES[entry.type], entry.data)
            if index in namespaces and namespaces[index] != entry.key:
                _fail(errors, strict, f"namespace index {index} is claimed by both "
                                      f"'{namespaces[index]}' and '{entry.key}'")
            namespaces[index] = entry.key

    # Blob chunks, keyed the way a BLOB_IDX addresses them.
    chunks: dict[tuple[int, str, int], RawEntry] = {}
    for entry in raw_entries:
        if entry.type == TYPE_BLOB_DATA:
            chunks[(entry.ns_index, entry.key, entry.chunk_index)] = entry

    entries: list[NvsEntry] = []
    for entry in raw_entries:
        if entry.ns_index == 0:
            continue  # the namespace table itself
        if entry.type == TYPE_BLOB_DATA:
            continue  # accounted for by its BLOB_IDX
        namespace = namespaces.get(entry.ns_index)
        if namespace is None:
            _fail(errors, strict, f"page {entry.page} entry {entry.index} ('{entry.key}'): "
                                  f"namespace index {entry.ns_index} is not in the namespace table")
            namespace = f'<{entry.ns_index}>'

        if entry.type == TYPE_BLOB_IDX:
            item = _join_blob(entry, namespace, chunks, errors, strict)
            if item is None:
                continue
        elif entry.type in (TYPE_SZ, TYPE_BLOB):
            payload = entry.payload or b''
            if entry.type == TYPE_SZ:
                # NVS stores strings with their terminating NUL; the CSV value does not have one.
                value = payload.rstrip(b'\x00').decode('utf-8', errors='replace')
            else:
                value = bytes(payload)
            item = NvsEntry(namespace=namespace, key=entry.key,
                            type=TYPES[entry.type], value=value, size=len(payload),
                            ns_index=entry.ns_index, raw=[entry])
        else:
            type_name = TYPES[entry.type]
            item = NvsEntry(namespace=namespace, key=entry.key, type=type_name,
                            value=unpack_primitive(type_name, entry.data),
                            size=PRIMITIVES[type_name][1], ns_index=entry.ns_index, raw=[entry])

        # A key written twice without the old copy being erased shouldn't happen, but if it
        # does the newest page wins — matching the firmware's own read order.
        existing = next((i for i, e in enumerate(entries)
                         if e.ns_index == item.ns_index and e.key == item.key), None)
        if existing is not None:
            entries[existing] = item
        else:
            entries.append(item)

    return namespaces, entries


def _join_blob(index_entry: RawEntry, namespace: str, chunks: dict, errors: list[str],
               strict: bool):
    """Stitch a v2 blob back together from the chunks its index entry points at."""
    total = struct.unpack('<I', index_entry.data[0:4])[0]
    chunk_count = index_entry.data[4]
    chunk_start = index_entry.data[5]

    payload = bytearray()
    raw = [index_entry]
    for n in range(chunk_count):
        chunk = chunks.get((index_entry.ns_index, index_entry.key, chunk_start + n))
        if chunk is None:
            _fail(errors, strict, f"blob '{namespace}:{index_entry.key}': chunk "
                                  f"{chunk_start + n} of {chunk_count} is missing")
            return None
        payload += chunk.payload or b''
        raw.append(chunk)

    if len(payload) != total:
        _fail(errors, strict, f"blob '{namespace}:{index_entry.key}': chunks total "
                              f"{len(payload)} bytes, index says {total}")
        return None

    return NvsEntry(namespace=namespace, key=index_entry.key, type='blob', value=bytes(payload),
                    size=total, ns_index=index_entry.ns_index, raw=raw)


#: Primitive type codes, for spotting namespace entries (which are always u8).
PRIMITIVES_BY_CODE = {code: name for code, name in TYPES.items() if name in PRIMITIVES}


def parse(image: bytes, *, strict: bool = False) -> NvsImage:
    """Parse an NVS partition image into its pages, namespaces, and key/value pairs.

    Damage is collected into ``NvsImage.errors`` and skipped unless `strict` is set.
    """
    if not image:
        raise NvsError("NVS image is empty")
    if len(image) % PAGE_SIZE != 0:
        raise NvsError(f"NVS image size {len(image):#x} is not a multiple of the "
                       f"{PAGE_SIZE:#x}-byte page size")

    errors: list[str] = []
    pages = [_parse_page(image, i, errors, strict) for i in range(len(image) // PAGE_SIZE)]
    if all(page.is_uninit for page in pages):
        # A blank partition is a valid NVS partition with nothing in it, not an error.
        return NvsImage(data=image, pages=pages, errors=errors)

    namespaces, entries = _reassemble(pages, errors, strict)
    version = next((p.version for p in pages if not p.is_uninit and p.crc_ok), 0xFE)
    return NvsImage(data=image, pages=pages, entries=entries, namespaces=namespaces,
                    errors=errors, version=version)


def format_entries(entries: list[NvsEntry]) -> str:
    """Render key/value pairs as a table, in the style of the partition table listing."""
    if not entries:
        return "(empty)"

    rows = [(e.namespace, e.key, e.type, e.format_value()) for e in
            sorted(entries, key=lambda e: (e.namespace, e.key))]
    headings = ('Namespace', 'Key', 'Type', 'Value')
    widths = [max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(headings)]

    def line(cells):
        return '| ' + ' | '.join(c.ljust(w) for c, w in zip(cells, widths)) + ' |'

    out = [line(headings), '|' + '|'.join('-' * (w + 2) for w in widths) + '|']
    out += [line(r) for r in rows]

    count = len(entries)
    total = sum(e.size for e in entries)
    namespaces = len({e.namespace for e in entries})
    out.append(f"{count} entr{'y' if count == 1 else 'ies'} in {namespaces} "
               f"namespace{'' if namespaces == 1 else 's'}, {total} bytes of data")
    return '\n'.join(out)


def format_pages(image: NvsImage) -> str:
    """Render the page map — states, sequence numbers, and how full each page is."""
    rows = []
    for page in image.pages:
        if page.is_uninit:
            rows.append((str(page.index), '-', 'uninitialised', '', ''))
            continue
        used = page.used_entries
        written = sum(1 for s in page.entry_states if s == ENTRY_WRITTEN)
        erased = sum(1 for s in page.entry_states if s == ENTRY_ERASED)
        rows.append((str(page.index), str(page.seq), page.state_name,
                     f'{used}/{MAX_ENTRIES}', f'{written} written, {erased} erased'))

    headings = ('Page', 'Seq', 'State', 'Used', 'Entries')
    widths = [max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(headings)]

    def line(cells):
        return '| ' + ' | '.join(c.ljust(w) for c, w in zip(cells, widths)) + ' |'

    out = [line(headings), '|' + '|'.join('-' * (w + 2) for w in widths) + '|']
    out += [line(r) for r in rows]
    return '\n'.join(out)


def describe_state(state: int) -> str:
    return ENTRY_STATES.get(state, f'{state:#04b}')
