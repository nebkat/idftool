# Reference

Every command, by group. Run `idftool COMMAND --help` for all of a command's
options. The options shared by every command are under
[Global options](global-options.md) and [Write options](write-options.md).

| Command | Description |
|---------|-------------|
| **[Discovery](discovery.md)** | |
| [`devices`](discovery.md#devices) | List serial ports and their USB adapters (alias `ports`) |
| **[Firmware](firmware.md)** | |
| [`factory`](firmware.md#factory) | Flash an app to the factory partition |
| [`ota`](firmware.md#ota) | Push an app to the next OTA slot and switch to it |
| [`get-boot`](firmware.md#get-boot) | Show the currently-active OTA slot |
| [`set-boot`](firmware.md#set-boot) | Force the next boot to a specific OTA partition |
| [`clear-boot`](firmware.md#clear-boot) | Erase otadata and let the bootloader fall back |
| [`app-info`](firmware.md#app-info) | Print the app description from an application binary |
| **[Monitor](monitor.md)** | |
| [`monitor`](monitor.md#monitor) | Open [`esp-idf-monitor`](https://github.com/espressif/esp-idf-monitor) on the selected device |
| **[idf.py](idf-py.md)** | |
| [`idf.py`](idf-py.md#idfpy) | Run ESP-IDF's `idf.py`, asking which device to use |
| **[Partition I/O](partition-io.md)** | |
| [`read`](partition-io.md#read) | Read a partition (or slice) into a file |
| [`write`](partition-io.md#write) | Write one or more files to named partitions |
| [`erase`](partition-io.md#erase) | Erase a partition (or slice) |
| [`view`](partition-io.md#view) | Pretty-print a partition's contents |
| **[Images](images.md)** | |
| [`create-image`](images.md#create-image) | Merge partition binaries into a single flash image |
| [`dump-image`](images.md#dump-image) | Dump the entire flash to an image file |
| [`write-image`](images.md#write-image) | Write a full flash image to the device |
| [`print-image`](images.md#print-image) | Print partition table and app info from a flash image |
| **[Bundles](bundles.md)** | |
| [`create-bundle`](bundles.md#create-bundle) | Pack partition images into a ZIP bundle |
| [`dump-bundle`](bundles.md#dump-bundle) | Pack every partition from the device into a ZIP |
| [`write-bundle`](bundles.md#write-bundle) | Flash every binary in a bundle ZIP |
| [`print-bundle`](bundles.md#print-bundle) | Print partition table and app info from a bundle ZIP |
| **[Partition table](partition-table.md)** | |
| [`print-table`](partition-table.md#print-table) | Print a partition table from a file or the device (alias `list`) |
| [`create-table`](partition-table.md#create-table) | Convert a partition table between CSV and binary |
| [`dump-table`](partition-table.md#dump-table) | Read the partition table from the device into a file |
| [`write-table`](partition-table.md#write-table) | Flash a partition table from a CSV or binary file |
| **[NVS](nvs.md)** | |
| [`create-nvs`](nvs.md#create-nvs) | Generate an NVS partition image from a CSV file |
| [`write-nvs`](nvs.md#write-nvs) | Generate an NVS image from CSV and flash it |
| [`print-nvs`](nvs.md#print-nvs) | List the contents of an NVS partition or image |
| [`read-nvs`](nvs.md#extract-nvs-read-nvs) | Read an NVS partition from the device into a CSV |
| [`extract-nvs`](nvs.md#extract-nvs-read-nvs) | Extract an NVS image file into a CSV |
| [`get-nvs`](nvs.md#get-nvs) | Print the value of one or more keys |
| [`set-nvs`](nvs.md#set-nvs) | Set or delete keys in an NVS partition or image |
| **[Filesystems](filesystems.md)** | |
| [`create-fs`](filesystems.md#create-fs) | Build a filesystem image from a directory |
| [`write-fs`](filesystems.md#write-fs) | Build a filesystem image from a directory and flash it |
| [`read-fs`](filesystems.md#read-fs) | Read a filesystem partition and extract it to a directory |
| [`extract-fs`](filesystems.md#extract-fs) | Extract a filesystem image file to a directory |
| [`print-fs`](filesystems.md#print-fs) | List the contents of a filesystem partition or image |
| **[Misc](misc.md)** | |
| [`enter-bootloader`](misc.md#enter-bootloader) | Drop the chip into ROM bootloader mode |
