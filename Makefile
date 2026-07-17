.PHONY: build build-onefile test test-offline test-device install install-onefile uninstall clean

VENV         := .venv
PYINSTALLER  := $(VENV)/bin/pyinstaller
PREFIX       := $(HOME)/.local
BINDIR       := $(PREFIX)/bin
SHAREDIR     := $(PREFIX)/share/idftool

# Device test config: `make test-device PORT=/dev/cu.usbmodem101 CHIP=esp32s3`
PORT         :=
CHIP         := esp32s3

build: $(VENV)
	$(PYINSTALLER) --noconfirm --distpath ./dist-onedir --workpath ./build-onedir idftool-onedir.spec

build-onefile: $(VENV)
	$(PYINSTALLER) --noconfirm --distpath ./dist --workpath ./build idftool.spec

test: build
	./dist-onedir/idftool/idftool --help >/dev/null

# Offline test suite (no hardware).
test-offline: $(VENV)
	$(VENV)/bin/pip install -q -e ".[test]"
	$(VENV)/bin/pytest test/test_offline.py

# Real-device test suite. Requires a connected board and built fixtures
# (test/project/build_fixtures.sh). WILL ERASE THE DEVICE'S FLASH.
test-device: $(VENV)
	@test -n "$(PORT)" || { echo "Usage: make test-device PORT=/dev/... [CHIP=$(CHIP)]"; exit 2; }
	$(VENV)/bin/pip install -q -e ".[test]"
	$(VENV)/bin/pytest test/test_device.py --port $(PORT) --chip $(CHIP)

install: build
	mkdir -p $(BINDIR)
	rm -rf $(SHAREDIR)
	mkdir -p $(dir $(SHAREDIR))
	cp -R dist-onedir/idftool $(SHAREDIR)
	ln -sf $(SHAREDIR)/idftool $(BINDIR)/idftool

install-onefile: build-onefile
	mkdir -p $(BINDIR)
	cp dist/idftool $(BINDIR)/idftool

uninstall:
	rm -f $(BINDIR)/idftool
	rm -rf $(SHAREDIR)

clean:
	rm -rf build build-onedir dist dist-onedir $(VENV)

$(VENV):
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e . pyinstaller
