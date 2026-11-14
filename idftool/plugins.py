"""Plugins: other installed packages naming devices their own way.

A package registers a function under the ``idftool.identify`` entry point group. Whenever
idftool connects to a board to probe it, each registered function is called with the
connected ``ESPLoader``, and the first answer that isn't None names the device."""
import threading
from importlib.metadata import entry_points
from typing import Any, Callable, Union

GROUP = "idftool.identify"

_lock = threading.Lock()
#: ``(name, function or load error)``.
_Identifier = tuple[str, Union[Callable[[Any], object], Exception]]

_loaded: list[_Identifier] | None = None


def _identifiers() -> list[_Identifier]:
    """``(name, function or load error)`` for each registered plugin, loaded once."""
    global _loaded
    with _lock:
        if _loaded is None:
            _loaded = []
            for entry in sorted(entry_points(group=GROUP), key=lambda e: e.name):
                try:
                    _loaded.append((entry.name, entry.load()))
                except Exception as error:  # noqa: BLE001
                    _loaded.append((entry.name, error))
        return _loaded


def identify(esp: Any) -> object:
    """What the plugins call the device on `esp`, or None. Raises if none answered and one
    failed, so the failure shows next to the device."""
    failure: RuntimeError | None = None
    for name, function in _identifiers():
        if isinstance(function, Exception):
            failure = failure or RuntimeError(f"plugin {name}: {function}")
            continue
        try:
            answer = function(esp)
        except Exception as error:  # noqa: BLE001
            failure = failure or RuntimeError(f"plugin {name}: {error or type(error).__name__}")
            continue
        if answer is not None:
            return answer
    if failure is not None:
        raise failure
    return None
