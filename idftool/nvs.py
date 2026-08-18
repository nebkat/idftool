"""NVS partition images.

Detecting a pre-built NVS binary, fitting one to a partition, and generating one from
CSV via ``esp-idf-nvs-partition-gen``. The commands live in :mod:`idftool.commands.nvs`."""
import argparse
import os.path
import struct
import zlib

NVS_PAGE_SIZE = 0x1000  # NVS partitions are laid out as 4 KiB pages


def looks_like_nvs_binary(data: bytes) -> bool:
    """Return True if `data` parses as an NVS partition image.

    An NVS partition is a sequence of 4 KiB pages. A page is either entirely
    erased (all 0xFF) or carries a 32-byte header whose last 4 bytes are the
    CRC32 of header bytes [4:28] (seeded with 0xFFFFFFFF), with a version byte
    of 0xFF (V1) or 0xFE (V2). The header CRC is a strong signature: matching
    it on a non-erased page means the file really is an NVS image, not CSV.
    """
    if not data or len(data) % NVS_PAGE_SIZE != 0:
        return False

    blank_page = b'\xff' * NVS_PAGE_SIZE
    non_blank_pages = 0
    for offset in range(0, len(data), NVS_PAGE_SIZE):
        page = data[offset:offset + NVS_PAGE_SIZE]
        if page == blank_page:
            continue
        header = page[:32]
        version = header[8]
        if version not in (0xFF, 0xFE):
            return False
        stored_crc = struct.unpack('<I', header[28:32])[0]
        calc_crc = zlib.crc32(header[4:28], 0xFFFFFFFF) & 0xFFFFFFFF
        if stored_crc != calc_crc:
            return False
        non_blank_pages += 1

    # A valid NVS image has at least one initialised (non-blank) page.
    return non_blank_pages > 0


def fit_nvs_binary(data: bytes, size: int) -> bytes:
    """Validate a pre-built NVS binary against a partition size and pad it out.

    The image must fit the partition; if it is smaller it is padded with 0xFF
    (erased flash) so the whole partition is written cleanly.
    """
    if len(data) > size:
        raise RuntimeError(
            f"NVS binary size {len(data):#x} exceeds partition size {size:#x}")
    if len(data) < size:
        data = data + b'\xff' * (size - len(data))
    return data


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
