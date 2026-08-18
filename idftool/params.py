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

BASED_INT = BasedIntParamType()
BOOTLOADER_OFFSET = BootloaderOffsetParamType()
