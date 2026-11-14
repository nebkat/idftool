"""Serial ports across a network (``rfc2217://``), tuned for its round trip.

esptool is written for a local port, where a round trip costs nothing. Across a network it
costs tens to hundreds of milliseconds, and two habits of esptool and pyserial turn that into
minutes:

- esptool waits 0.1 s for each sync reply and flushes its input before every retry, so a
  reply that takes longer than that is always thrown away and it never connects.
- esptool sets the port's read timeout around every command, and pyserial's RFC 2217 client
  renegotiates every port setting whenever any of them is set, the read timeout included
  (which is the client's alone), waiting for the server to confirm each. That is four
  round trips per command on top of the command's own.

:func:`prepare` fixes both for this process before a network port is opened.
"""
from typing import Any

import serial
import serial.rfc2217

#: Port URL schemes that reach the device across a network.
NETWORK_SCHEMES = ("rfc2217://",)

#: esptool's sync timeout on a network port: longer than any round trip it should survive.
NETWORK_SYNC_TIMEOUT = 1.0

#: The package pyserial takes this process's ``rfc2217://`` ports from, ahead of its own.
URL_HANDLERS = "idftool.urlhandler"


def is_network_port(port: object) -> bool:
    return isinstance(port, str) and port.startswith(NETWORK_SCHEMES)


def prepare(port: object) -> None:
    """Make this process's esptool fit for `port`, before it is opened; nothing for a local
    port. Lasts for the process: esptool's timeouts are module-wide."""
    if not is_network_port(port):
        return
    import esptool.loader

    esptool.loader.SYNC_TIMEOUT = max(esptool.loader.SYNC_TIMEOUT, NETWORK_SYNC_TIMEOUT)
    if URL_HANDLERS not in serial.protocol_handler_packages:
        serial.protocol_handler_packages.insert(0, URL_HANDLERS)


class Rfc2217Serial(serial.rfc2217.Serial):
    """pyserial's RFC 2217 client, renegotiating the port's settings only when one of them
    changes rather than whenever any is set."""

    negotiated: tuple[Any, ...] | None = None

    def _settings(self) -> tuple[Any, ...]:
        return (self._baudrate, self._bytesize, self._parity, self._stopbits,  # type: ignore[attr-defined]
                self._rtscts, self._xonxoff)  # type: ignore[attr-defined]

    def _reconfigure_port(self) -> None:
        if self.negotiated == self._settings():
            return
        super()._reconfigure_port()  # type: ignore[misc]
        self.negotiated = self._settings()

    def close(self) -> None:
        self.negotiated = None
        super().close()
