"""An idftool plugin: names each probed board by its package and revision, read from eFuse."""


def identify(esp):
    # e.g. "ESP32-S3 (QFN56) (revision v0.2)". Return None to leave a board to other
    # plugins, or to idftool's own chip name.
    return esp.get_chip_description()
