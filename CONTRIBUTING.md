# Contributing

## Requirements

- Python 3.10+
- `make` (for the binary build targets)

## Development install

Set up a virtualenv and install the package in editable mode:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

You can now run the CLI from source:

```bash
python -m idftool --help
```

## Building a standalone binary

The Makefile wraps PyInstaller. The first invocation creates the
`.venv` and installs build dependencies automatically.

```bash
make build           # PyInstaller onedir build → dist-onedir/idftool/
make build-onefile   # single-file binary       → dist/idftool
make install         # onedir → ~/.local/share/idftool, symlinked to ~/.local/bin/idftool
make uninstall
make clean           # remove venv, build/, dist/
```

## Documentation

The docs site is built with [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/)
from `docs/`:

```bash
make docs-serve      # live preview at http://127.0.0.1:8000
make docs            # build into site/, failing on broken links
```

Make sure `~/.local/bin` is on your `PATH`.

## Issues and pull requests

File issues and PRs on the
[GitHub repo](https://github.com/nebkat/idftool). Small, focused PRs
are easiest to review. Run a quick `python -m idftool --help` against
your branch before opening a PR to confirm argparse setup still parses.
