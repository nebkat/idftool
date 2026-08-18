"""SPIFFS images for ESP-IDF's ``spiffs`` partitions.

Writing is delegated to :mod:`idftool.fs.spiffsgen`, vendored from ESP-IDF, so images
match what ``spiffs_create_partition_image()`` produces. Reading has no counterpart
upstream — ESP-IDF ships a generator but no parser — so the reader here walks the
on-flash structures directly.

A SPIFFS volume is a run of blocks, each starting with an object lookup table holding
one object id per page in the block. Pages are either an *index* page (object id with
the top bit set), whose span 0 carries the file's name and size, or a data page. Files
are reassembled by collecting a file's data pages and ordering them by span index,
which works regardless of where the device scattered them.
"""
import struct
from typing import Optional

from idftool.fs import spiffsgen
from idftool.fs.common import FsEntry, FsError, SourceEntry, Volume

NAME = 'spiffs'

# CONFIG_SPIFFS_PAGE_SIZE / CONFIG_SPIFFS_OBJ_NAME_LEN / CONFIG_SPIFFS_META_LENGTH defaults;
# the block size always follows the flash sector size.
PAGE_SIZE = 256
BLOCK_SIZE = 4096
OBJ_NAME_LEN = 32
META_LEN = 4

OBJ_ID_LEN = 2
SPAN_IX_LEN = 2
PAGE_IX_LEN = 2
BLOCK_IX_LEN = 2

# Page header flags are active low: a cleared bit means the property holds.
FLAG_USED = 0x01
FLAG_FINAL = 0x02
FLAG_INDEX = 0x04
FLAG_DELETED = 0x80

OBJ_ID_FREE = 0xFFFF
OBJ_ID_INDEX_FLAG = 0x8000

# Page sizes to probe when identifying an image whose configuration we weren't told.
CANDIDATE_PAGE_SIZES = (256, 128, 512, 1024, 2048)


def _config(page_size: int, block_size: int, obj_name_len: int, meta_len: int,
            use_magic: bool, use_magic_len: bool) -> spiffsgen.SpiffsBuildConfig:
    return spiffsgen.SpiffsBuildConfig(
        page_size=page_size, page_ix_len=PAGE_IX_LEN,
        block_size=block_size, block_ix_len=BLOCK_IX_LEN,
        meta_len=meta_len, obj_name_len=obj_name_len,
        obj_id_len=OBJ_ID_LEN, span_ix_len=SPAN_IX_LEN,
        packed=True, aligned=True, endianness='little',
        use_magic=use_magic, use_magic_len=use_magic_len,
        aligned_obj_ix_tables=False,
    )


def create(sources: list[SourceEntry], size: int, *, page_size: int = PAGE_SIZE,
           block_size: int = BLOCK_SIZE, obj_name_len: int = OBJ_NAME_LEN,
           meta_len: int = META_LEN, use_magic: bool = True, use_magic_len: bool = True,
           **_) -> bytes:
    """Build a SPIFFS image of exactly `size` bytes holding `sources`."""
    if size % block_size != 0:
        raise FsError(f"Filesystem size {size:#x} is not a multiple of the block size {block_size:#x}")

    config = _config(page_size, block_size, obj_name_len, meta_len, use_magic, use_magic_len)
    fs = spiffsgen.SpiffsFS(size, config)
    for source in sources:
        # SPIFFS is flat: directories are not stored, they only survive as part of a name.
        if source.is_dir:
            continue
        name = '/' + source.path
        if len(name) > obj_name_len:
            raise FsError(
                f"Name '{name}' is {len(name)} characters, over the SPIFFS limit of {obj_name_len} "
                f"(SPIFFS is flat, so the whole path counts); raise --spiffs-obj-name-len to match "
                f"CONFIG_SPIFFS_OBJ_NAME_LEN on the device")
        try:
            fs.create_file(name, str(source.host_path))
        except spiffsgen.SpiffsFullError as e:
            raise FsError(f"Sources do not fit in a {size:#x}-byte SPIFFS image") from e
        except RuntimeError as e:
            raise FsError(f"Failed to add '{source.path}' to the SPIFFS image: {e}") from e
    return fs.to_binary()


def _magic(page_size: int, blocks: int, block: int, use_magic_len: bool) -> int:
    """Mirror the SPIFFS_MAGIC macro from spiffs_nucleus.h."""
    magic = 0x20140529 ^ page_size
    if use_magic_len:
        magic ^= blocks - block
    return magic & 0xFFFF  # stored as a spiffs_obj_id, i.e. truncated to OBJ_ID_LEN bytes


class SpiffsVolume(Volume):
    def __init__(self, image: bytes, page_size: int = PAGE_SIZE, block_size: int = BLOCK_SIZE,
                 obj_name_len: int = OBJ_NAME_LEN, meta_len: int = META_LEN):
        if block_size % page_size or len(image) % block_size:
            raise FsError(f"SPIFFS image size {len(image):#x} is not a whole number of "
                          f"{block_size:#x}-byte blocks of {page_size:#x}-byte pages")
        self.image = image
        self.page_size = page_size
        self.block_size = block_size
        self.obj_name_len = obj_name_len
        self.meta_len = meta_len

        self.pages_per_block = block_size // page_size
        self.lookup_pages = max(1, self.pages_per_block * OBJ_ID_LEN // page_size)
        self.data_header_len = OBJ_ID_LEN + SPAN_IX_LEN + 1
        # The index page header is padded out to a 4-byte boundary before the size/type/name.
        self.index_header_len = self.data_header_len + -self.data_header_len % 4

        self._files: dict[int, dict] = {}
        self._scan()
        if not self._files:
            # An empty volume is legitimate, so only reject images with no SPIFFS structure at all.
            if not detect(image, page_size=page_size, block_size=block_size):
                raise FsError("Not a readable SPIFFS image")

    def _page(self, index: int) -> bytes:
        return self.image[index * self.page_size:(index + 1) * self.page_size]

    def _scan(self):
        """Index every live page by walking each block's object lookup table."""
        for block in range(len(self.image) // self.block_size):
            lookup_start = block * self.block_size
            for slot in range(self.pages_per_block - self.lookup_pages):
                offset = lookup_start + slot * OBJ_ID_LEN
                obj_id = struct.unpack_from('<H', self.image, offset)[0]
                if obj_id == OBJ_ID_FREE:
                    continue

                page_index = block * self.pages_per_block + self.lookup_pages + slot
                header = self._page(page_index)[:self.data_header_len]
                if len(header) < self.data_header_len:
                    continue
                page_obj_id, span_ix, flags = struct.unpack('<HHB', header)
                # The lookup table and the page header must agree; where they don't, the slot
                # holds something else — most often the volume magic in the last lookup page.
                if page_obj_id != obj_id:
                    continue
                if flags & FLAG_USED or not flags & FLAG_DELETED:
                    continue

                file = self._files.setdefault(obj_id & ~OBJ_ID_INDEX_FLAG,
                                              {'name': None, 'size': 0, 'pages': {}})
                if obj_id & OBJ_ID_INDEX_FLAG:
                    if span_ix == 0 and not flags & FLAG_INDEX:
                        file['size'], file['name'] = self._index_header(page_index)
                else:
                    file['pages'][span_ix] = page_index

    def _index_header(self, page_index: int) -> tuple[int, str]:
        page = self._page(page_index)
        size, _obj_type = struct.unpack_from('<IB', page, self.index_header_len)
        start = self.index_header_len + 5
        name = page[start:start + self.obj_name_len].split(b'\x00', 1)[0]
        return size, name.decode('utf-8', errors='replace')

    def entries(self) -> list[FsEntry]:
        found = []
        for obj_id, file in self._files.items():
            if not file['name']:
                continue  # data pages whose index header was lost — not a readable file
            found.append(FsEntry(path=file['name'].lstrip('/'), is_dir=False,
                                 size=file['size'], handle=obj_id))
        return sorted(found, key=lambda e: e.path)

    def read(self, entry: FsEntry) -> bytes:
        file = self._files[entry.handle]
        content_len = self.page_size - self.data_header_len
        data = bytearray()
        for span_ix in sorted(file['pages']):
            if span_ix * content_len != len(data):
                raise FsError(f"File '{entry.path}' is missing data page {len(data) // content_len}")
            data += self._page(file['pages'][span_ix])[self.data_header_len:]
        if len(data) < file['size']:
            raise FsError(f"File '{entry.path}' is truncated: {len(data)} of {file['size']} bytes found")
        return bytes(data[:file['size']])


def mount(image: bytes, *, page_size: int = PAGE_SIZE, block_size: int = BLOCK_SIZE,
          obj_name_len: int = OBJ_NAME_LEN, meta_len: int = META_LEN, **_) -> SpiffsVolume:
    return SpiffsVolume(image, page_size, block_size, obj_name_len, meta_len)


def detect(image: bytes, *, page_size: Optional[int] = None, block_size: int = BLOCK_SIZE,
           **_) -> bool:
    """Look for the SPIFFS volume magic in the object lookup table of every block.

    ESP-IDF enables ``CONFIG_SPIFFS_USE_MAGIC`` by default, which stamps a value derived
    from the page size (and optionally the block's distance from the end) into the last
    lookup page of each block. Images built with magic disabled aren't identifiable this
    way and have to be named explicitly.
    """
    if len(image) < block_size or len(image) % block_size:
        return False
    blocks = len(image) // block_size

    for candidate in ([page_size] if page_size else CANDIDATE_PAGE_SIZES):
        if block_size % candidate:
            continue
        pages_per_block = block_size // candidate
        lookup_pages = max(1, pages_per_block * OBJ_ID_LEN // candidate)
        # magicfy() puts the magic in the second-to-last slot of the block's last lookup page.
        slot = lookup_pages * (candidate // OBJ_ID_LEN) - 2
        if slot < 0:
            continue
        for use_magic_len in (True, False):
            expected = [_magic(candidate, blocks, b, use_magic_len) for b in range(blocks)]
            found = [struct.unpack_from('<H', image, b * block_size + slot * OBJ_ID_LEN)[0]
                     for b in range(blocks)]
            if found == expected:
                return True
    return False


def describe(size: int, *, page_size: int = PAGE_SIZE, block_size: int = BLOCK_SIZE,
             obj_name_len: int = OBJ_NAME_LEN, **_) -> str:
    return (f"SPIFFS, {size // block_size} blocks of {block_size:#x} bytes, "
            f"{page_size:#x}-byte pages, name limit {obj_name_len}")
