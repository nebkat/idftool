"""Input files given as an http(s) URL, downloaded to a temporary file for the run."""
import atexit
import os
import shutil
import ssl
import tempfile
import urllib.parse
import urllib.request

_downloaded: dict[str, str] = {}


def is_url(value: str) -> bool:
    return value.lower().startswith(('http://', 'https://'))


def fetch(value: str) -> str:
    """`value` itself, or for an http(s) URL, a local copy of what it serves. The copy keeps
    the URL's file name and is removed when idftool exits."""
    if not is_url(value):
        return value
    if value in _downloaded:
        return _downloaded[value]
    from idftool.display import say

    name = os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(value).path)) or 'download'
    directory = tempfile.mkdtemp(prefix='idftool-')
    atexit.register(shutil.rmtree, directory, True)
    path = os.path.join(directory, name)
    say(f"Downloading {value}...")
    request = urllib.request.Request(value, headers={'User-Agent': 'idftool'})
    with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as response, \
            open(path, 'wb') as f:
        shutil.copyfileobj(response, f)
    _downloaded[value] = path
    return path


def _ssl_context() -> ssl.SSLContext:
    # certifi's roots, as a frozen build can't rely on the system's being where it was built.
    import certifi

    return ssl.create_default_context(cafile=certifi.where())
