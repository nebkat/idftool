"""Encrypted NVS with HMAC key protection (``CONFIG_NVS_SEC_KEY_PROTECT_USING_HMAC``).

The encryption and tweak keys are HMAC-SHA256 of fixed seeds under the eFuse HMAC key. Each
written entry is its own XTS-AES-256 data unit, tweaked by its offset in the partition. Page
headers and state bitmaps stay plaintext, and every CRC is over plaintext, so the parser and
editor work on the decrypted image unchanged.
"""
import hmac
import os.path
import re
import struct
from dataclasses import dataclass
from hashlib import sha256

from idftool.nvs.common import (
    ENTRY_EMPTY, ENTRY_SIZE, ENTRY_WRITTEN, BITMAP_OFFSET, BITMAP_SIZE, FIRST_ENTRY_OFFSET,
    MAX_ENTRIES, PAGE_SIZE, NvsError, entry_crc, entry_state,
)

EKEY_SEED = b'\x5a\x5a\xbe\xae' * 8
TKEY_SEED = b'\xa5\xa5\xde\xce' * 8


@dataclass(frozen=True)
class NvsKeys:
    """The XTS encryption and tweak keys."""
    eky: bytes
    tky: bytes

    @classmethod
    def from_hmac_key(cls, key: bytes) -> 'NvsKeys':
        return cls(hmac.new(key, EKEY_SEED, sha256).digest(),
                   hmac.new(key, TKEY_SEED, sha256).digest())


def parse_hmac_key(value: str) -> bytes:
    """A 32-byte HMAC key: 64 hex digits (spaces, colons and a 0x prefix ignored), or a file
    holding the raw 32 bytes or the hex digits."""
    if os.path.isfile(value):
        with open(value, 'rb') as f:
            data = f.read()
        if len(data) == 32:
            return data
        try:
            value = data.decode('ascii')
        except UnicodeDecodeError:
            raise NvsError(f"HMAC key file '{value}' holds neither 32 bytes nor hex digits")
    digits = re.sub(r'[\s:]', '', value)
    if digits[:2].lower() == '0x':
        digits = digits[2:]
    if not re.fullmatch(r'[0-9a-fA-F]{64}', digits):
        raise NvsError("An HMAC key is 64 hex digits (32 bytes), or a file holding them")
    return bytes.fromhex(digits)


def _entries(data: bytes):
    """Offsets of every entry whose state is not EMPTY, and its state."""
    for page in range(0, len(data) - len(data) % PAGE_SIZE, PAGE_SIZE):
        bitmap = data[page + BITMAP_OFFSET:page + BITMAP_OFFSET + BITMAP_SIZE]
        for index in range(MAX_ENTRIES):
            state = entry_state(bitmap, index)
            if state != ENTRY_EMPTY:
                yield page + FIRST_ENTRY_OFFSET + index * ENTRY_SIZE, state


def _transform(data: bytes, keys: NvsKeys, encrypt: bool) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    out = bytearray(data)
    for offset, _ in _entries(data):
        tweak = struct.pack('<I', offset) + bytes(12)
        cipher = Cipher(algorithms.AES(keys.eky + keys.tky), modes.XTS(tweak))
        op = cipher.encryptor() if encrypt else cipher.decryptor()
        out[offset:offset + ENTRY_SIZE] = op.update(data[offset:offset + ENTRY_SIZE]) + op.finalize()
    return bytes(out)


def _headers(data: bytes) -> tuple[int, int]:
    """(written entries, of which pass the entry-header CRC)."""
    written = valid = 0
    for offset, state in _entries(data):
        if state != ENTRY_WRITTEN:
            continue
        written += 1
        entry = data[offset:offset + ENTRY_SIZE]
        if struct.unpack_from('<I', entry, 4)[0] == entry_crc(entry):
            valid += 1
    return written, valid


def looks_encrypted(data: bytes) -> bool:
    """Written entries, none of them a readable entry header."""
    written, valid = _headers(data)
    return written > 0 and valid == 0


def decrypt(data: bytes, keys: NvsKeys) -> bytes:
    """Decrypt an image, refusing one that isn't encrypted or a key that doesn't fit."""
    written, valid = _headers(data)
    if written and valid:
        raise NvsError("The NVS image is not encrypted; leave out --hmac-key")
    plain = _transform(data, keys, encrypt=False)
    if written and not _headers(plain)[1]:
        raise NvsError("The HMAC key does not decrypt this NVS image")
    return plain


def encrypt(data: bytes, keys: NvsKeys) -> bytes:
    return _transform(data, keys, encrypt=True)
