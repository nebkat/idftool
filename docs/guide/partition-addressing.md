# Partition addressing

Wherever a command takes a `PARTITION`, you can pass:

Name
:   A name from the partition table: `nvs`, `ota_0`, `storage`.
    `bootloader` and `partition_table` also work even when the table has no
    row for them.

Address
:   A number matching a partition's start offset exactly: `0x9000`. The same
    as naming that partition.

Offset into a partition: `name[offset]`
:   Where to start within the partition. Negative values count from the end.
    Accepted by `write` and `create-image`.

Slice of a partition: `name[start:stop]`
:   Negative values count from the end, and a `+N` stop is a length from
    `start`. Accepted by `read`, `erase`, and `view`.

| Slice | Means |
|-------|-------|
| `nvs[0:0x100]` | First 256 bytes of `nvs` |
| `storage[-0x1000:]` | Last 4 KiB of `storage` |
| `ota_0[0x1000:+0x800]` | 2 KiB starting 4 KiB into `ota_0` |

Numbers in addresses, offsets, and sizes can be decimal (`4096`) or hex
(`0x1000`).

!!! tip
    Quote slices in the shell: `'storage[-0x1000:]'`. Brackets are glob
    characters in most shells.
