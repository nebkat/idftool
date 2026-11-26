"""click parameter types shared by the commands."""
from typing import TYPE_CHECKING, Any, Optional

import rich_click as click
from click import Context, Parameter

from esptool import CHIP_DEFS

if TYPE_CHECKING:
    from idftool.nvs.crypto import NvsKeys

# The ParamType subclasses leave out its type arguments: click before 8.5 isn't generic.

class BasedIntParamType(click.ParamType):  # type: ignore[type-arg]
    """Integer accepting any base via a 0x/0o/0b prefix (like int(x, 0))."""
    name = "integer"

    def convert(self, value: Any, param: Optional[Parameter], ctx: Optional[Context]) -> int:
        if isinstance(value, int):
            return value
        try:
            return int(value, 0)
        except ValueError:
            self.fail(f"{value!r} is not a valid integer", param, ctx)

class BootloaderOffsetParamType(click.ParamType):  # type: ignore[type-arg]
    """A flash offset, or a chip name (e.g. esp32s3) resolved to its bootloader offset."""
    name = "offset|chip"

    def convert(self, value: Any, param: Optional[Parameter], ctx: Optional[Context]) -> int:
        if isinstance(value, int):
            return value
        try:
            return int(value, 0)
        except ValueError:
            pass
        chip = CHIP_DEFS.get(value)
        if chip is None:
            self.fail(f"Invalid bootloader offset or chip name: {value}", param, ctx)
        offset: int = chip.BOOTLOADER_FLASH_OFFSET
        return offset

class MacParamType(click.ParamType):  # type: ignore[type-arg]
    """A MAC address in any common spelling, normalised to ``aa:bb:cc:dd:ee:ff``."""
    name = "mac"

    def convert(self, value: Any, param: Optional[Parameter], ctx: Optional[Context]) -> str:
        from idftool.ports import normalize_mac

        mac = normalize_mac(value)
        if mac is None:
            self.fail(f"{value!r} is not a MAC address", param, ctx)
        return mac

class HmacKeyParamType(click.ParamType):  # type: ignore[type-arg]
    """An NVS HMAC key, as 64 hex digits or a file, turned into the XTS keys."""
    name = "key"

    def convert(self, value: Any, param: Optional[Parameter], ctx: Optional[Context]) -> "NvsKeys":
        from idftool.nvs.crypto import NvsKeys, parse_hmac_key

        if isinstance(value, NvsKeys):
            return value
        try:
            return NvsKeys.from_hmac_key(parse_hmac_key(value))
        except RuntimeError as e:
            self.fail(str(e), param, ctx)

class InputFileParamType(click.ParamType):  # type: ignore[type-arg]
    """A file to read, or an http(s) URL downloaded to one (see :func:`idftool.fetch.fetch`)."""
    name = "file|url"

    def convert(self, value: Any, param: Optional[Parameter], ctx: Optional[Context]) -> str:
        from idftool.fetch import fetch

        try:
            return fetch(value)
        except OSError as e:
            self.fail(f"Could not download {value}: {e}", param, ctx)


def fetch_pairs(ctx: Context, param: Parameter, value: tuple[str, ...]) -> tuple[str, ...]:
    """Callback for a ``PARTITION FILENAME ...`` argument: download each FILENAME that is a URL."""
    return tuple(INPUT_FILE.convert(v, param, ctx) if i % 2 else v for i, v in enumerate(value))


BASED_INT = BasedIntParamType()
INPUT_FILE = InputFileParamType()
HMAC_KEY = HmacKeyParamType()
BOOTLOADER_OFFSET = BootloaderOffsetParamType()
MAC = MacParamType()
