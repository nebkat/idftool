"""click parameter types shared by the commands."""
import rich_click as click

from esptool import CHIP_DEFS

class BasedIntParamType(click.ParamType):
    """Integer accepting any base via a 0x/0o/0b prefix (like int(x, 0))."""
    name = "integer"

    def convert(self, value, param, ctx):
        if isinstance(value, int):
            return value
        try:
            return int(value, 0)
        except ValueError:
            self.fail(f"{value!r} is not a valid integer", param, ctx)

class BootloaderOffsetParamType(click.ParamType):
    """A flash offset, or a chip name (e.g. esp32s3) resolved to its bootloader offset."""
    name = "offset|chip"

    def convert(self, value, param, ctx):
        if isinstance(value, int):
            return value
        try:
            return int(value, 0)
        except ValueError:
            pass
        chip = CHIP_DEFS.get(value)
        if chip is None:
            self.fail(f"Invalid bootloader offset or chip name: {value}", param, ctx)
        return chip.BOOTLOADER_FLASH_OFFSET

class MacParamType(click.ParamType):
    """A MAC address in any common spelling, normalised to ``aa:bb:cc:dd:ee:ff``."""
    name = "mac"

    def convert(self, value, param, ctx):
        from idftool.ports import normalize_mac

        mac = normalize_mac(value)
        if mac is None:
            self.fail(f"{value!r} is not a MAC address", param, ctx)
        return mac

class HmacKeyParamType(click.ParamType):
    """An NVS HMAC key, as 64 hex digits or a file, turned into the XTS keys."""
    name = "key"

    def convert(self, value, param, ctx):
        from idftool.nvs.crypto import NvsKeys, parse_hmac_key

        if isinstance(value, NvsKeys):
            return value
        try:
            return NvsKeys.from_hmac_key(parse_hmac_key(value))
        except RuntimeError as e:
            self.fail(str(e), param, ctx)

BASED_INT = BasedIntParamType()
HMAC_KEY = HmacKeyParamType()
BOOTLOADER_OFFSET = BootloaderOffsetParamType()
MAC = MacParamType()
