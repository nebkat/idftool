import os
import sys
import traceback as _tb
from types import TracebackType
from typing import Optional


def _term_width(default: int = 80) -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return default


def _box(lines: list[str], title: str, width: int) -> list[str]:
    inner = max(width - 4, 4)

    title_str = f"─ {title} "
    if len(title_str) + 3 <= width:
        top = '╭' + title_str + '─' * (width - 2 - len(title_str)) + '╮'
    else:
        top = '╭' + '─' * (width - 2) + '╮'

    body = []
    for line in lines:
        line = line.expandtabs(4)
        if len(line) > inner:
            line = line[:inner - 1] + '…'
        body.append('│ ' + line.ljust(inner) + ' │')

    bottom = '╰' + '─' * (width - 2) + '╯'

    return [top] + body + [bottom]


def _walk_chain(exc: BaseException) -> list[tuple[BaseException, Optional[str]]]:
    """Walk __cause__ / __context__ chain, return (exception, separator_after) oldest-first."""
    chain: list[tuple[BaseException, Optional[str]]] = []
    e: Optional[BaseException] = exc
    # First build a list of (exc, link_type_to_previous) from newest to oldest
    newest_to_oldest: list[tuple[BaseException, Optional[str]]] = []
    while e is not None:
        if e.__cause__ is not None:
            link = "cause"
            prev: Optional[BaseException] = e.__cause__
        elif not e.__suppress_context__ and e.__context__ is not None:
            link = "context"
            prev = e.__context__
        else:
            link = None
            prev = None
        newest_to_oldest.append((e, link))
        e = prev

    # Reverse to oldest-first. The link recorded with each exception describes
    # how the NEXT (newer) exception relates to this one — i.e. the separator
    # we should print AFTER this exception.
    oldest_to_newest = list(reversed(newest_to_oldest))
    result: list[tuple[BaseException, Optional[str]]] = []
    for i, (exc_i, _) in enumerate(oldest_to_newest):
        if i + 1 < len(oldest_to_newest):
            next_link = oldest_to_newest[i + 1][1]
            if next_link == "cause":
                sep: Optional[str] = "The above exception was the direct cause of the following exception:"
            else:
                sep = "During handling of the above exception, another exception occurred:"
        else:
            sep = None
        result.append((exc_i, sep))
    return result


def _format_one(exc: BaseException, *, color: bool, width: int) -> list[str]:
    frames = _tb.extract_tb(exc.__traceback__)
    frame_lines = [f"in {f.name}:{f.lineno}  ({f.filename})" for f in frames]
    if not frame_lines:
        frame_lines = ["<no traceback available>"]

    box_lines = _box(frame_lines, "Traceback (most recent call last)", width)
    err_line = f"{type(exc).__name__}: {exc}"

    if color:
        box_lines = [f"\033[2m{l}\033[0m" for l in box_lines]
        err_line = f"\033[1;31m{err_line}\033[0m"

    return box_lines + [err_line]


def print_exception(exc: BaseException) -> None:
    """Print an exception (and its chain) in rich-style boxed format to stderr."""
    color = sys.stderr.isatty()
    width = min(_term_width(), 120)

    chain = _walk_chain(exc)
    for i, (e, sep) in enumerate(chain):
        if i > 0:
            print(file=sys.stderr)
        for line in _format_one(e, color=color, width=width):
            print(line, file=sys.stderr)
        if sep is not None:
            print(file=sys.stderr)
            print(sep, file=sys.stderr)


def _excepthook(exc_type: type, exc_value: BaseException, exc_tb: Optional[TracebackType]) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    print_exception(exc_value)


def install() -> None:
    """Replace sys.excepthook with a rich-style boxed formatter."""
    sys.excepthook = _excepthook
