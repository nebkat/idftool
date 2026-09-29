# Installation

=== "pipx (recommended)"

    [pipx](https://pipx.pypa.io) installs idftool into its own environment and
    puts it on your `PATH`:

    ```bash
    pipx install idftool
    ```

    Install pipx first if you don't have it:

    | Platform | Command |
    |----------|---------|
    | macOS | `brew install pipx` |
    | Windows | `winget install python.pipx` |
    | Linux / other | `pip install pipx` |

=== "Binary"

    Without Python, download a pre-built binary from the
    [Releases](https://github.com/nebkat/idftool/releases) page.

    !!! tip
        Prefer the `-dir` archive over the single-file download. It starts
        instantly, where the single file unpacks itself on every run.

=== "From source"

    ```bash
    git clone https://github.com/nebkat/idftool
    cd idftool
    make install
    ```

    This builds a standalone binary into `~/.local/share/idftool` and links it
    from `~/.local/bin/idftool`. See [Contributing](contributing.md).

Check it works:

```bash
idftool --help
```
