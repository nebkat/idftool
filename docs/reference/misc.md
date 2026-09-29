# Misc

## `enter-bootloader`

Wait for a port to appear, reset the chip into the ROM bootloader
([download mode](https://docs.espressif.com/projects/esptool/en/latest/esp32/advanced-topics/boot-mode-selection.html)), and exit, leaving it parked for another tool. The port is polled every
50 ms, and errors from a device node that isn't ready yet are retried.

```bash
idftool -p /dev/cu.usbmodem1101 enter-bootloader
idftool -m 9c:13:9e:1b:d4:6c enter-bootloader
```

Needs `-p`, `-m`, or `--usb-serial`. With `-m` or `--usb-serial`, it waits for
a port with that USB serial number to appear, so `-m` only works for boards on
their built-in USB-Serial/JTAG port.
