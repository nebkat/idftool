"""``python -m idftool`` / the ``idftool`` console script.

The CLI itself is assembled elsewhere: :mod:`idftool.cli` defines the command group and
global options, and :mod:`idftool.commands` registers the commands on it. This module is
just the entry point.
"""
import io
import sys

import rich_click as click

from idftool.traceback import install as install_excepthook, print_exception

from idftool.cli import cli
import idftool.commands  # noqa: F401  (registers every command on `cli`)


def _main() -> None:
    # A pipe or redirected file on Windows gets the ANSI code page (cp1252), which can't encode
    # `→`, `—` or the U+FFFD `view` prints for erased flash.
    # TODO: remove once requires-python is >=3.15, where UTF-8 mode is the default (PEP 686).
    for stream in (sys.stdout, sys.stderr):
        if (isinstance(stream, io.TextIOWrapper) and not stream.isatty()
                and stream.encoding.lower().replace('-', '') != 'utf8'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    install_excepthook()
    try:
        cli.main(args=sys.argv[1:], standalone_mode=False)
    except click.Abort:
        sys.exit(130)
    except click.ClickException as e:
        e.show()
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        sys.exit(130)
    except SystemExit:
        # A command may sys.exit() from inside a library (e.g. the NVS partition generator on a bad
        # CSV); let it carry its own exit code out rather than boxing it as a traceback.
        raise
    except BaseException as e:
        print_exception(e)
        sys.exit(1)


if __name__ == "__main__":
    _main()
