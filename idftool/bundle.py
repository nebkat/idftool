"""Bundles: a ZIP whose filenames decide what is flashed, plus an optional ``manifest.json``.

========================================  ================================================
File                                      Operation
========================================  ================================================
``partition_table.csv`` / ``.bin``        Replace the partition table (first)
``bootloader.bin``                        Write the bootloader
``@factory.bin``                          ``factory``: the factory partition (or ota_0)
``@ota.bin``                              ``ota``: the next OTA slot, then boot it
``<name>.bin``                            Write to the partition called ``name``
``manifest.json``                         Name, chip, table policy, and ``ops`` run last
========================================  ================================================

Only top-level entries count; files the manifest's ops use go in a subdirectory.
"""
import json
import os.path
from dataclasses import dataclass, field
from typing import Optional
from zipfile import BadZipFile, ZipFile

from esptool.targets import CHIP_DEFS

from esp_idf_defs.partitions import PartitionTable, PartitionDefinition, APP_TYPE, DATA_TYPE, \
    SUBTYPES, TYPES

from idftool.partitions import parse_partition_table_csv, require_partitions

ROLE_PREFIX = '@'
ROLES = ('factory', 'ota')
MANIFEST = 'manifest.json'
POLICIES = ('update', 'ask', 'require')
MATCHES = ('exact', 'used')

#: Fields each manifest op requires.
OPS = {
    'write': ('partition', 'file'),
    'erase': ('partition',),
    'write-fs': ('partition', 'file'),
    'edit-fs': ('partition',),
    'set-nvs': (),
    'set-boot': ('partition',),
    'clear-boot': (),
}

#: Ops that rewrite the partition they name.
WRITING_OPS = ('write', 'erase', 'write-fs', 'edit-fs')


class BundleError(RuntimeError):
    """A bundle that cannot be read, or cannot be flashed onto this device."""


def normalise_chip(name: str) -> str:
    return name.lower().replace('-', '').replace(' ', '')


def is_ota_app(p: PartitionDefinition) -> bool:
    return p.type == APP_TYPE and SUBTYPES[APP_TYPE]['ota_0'] <= p.subtype <= SUBTYPES[APP_TYPE]['ota_15']


def _is_otadata(p: PartitionDefinition) -> bool:
    return p.type == DATA_TYPE and p.subtype == SUBTYPES[DATA_TYPE]['ota']


def _is_nvs(p: PartitionDefinition) -> bool:
    return p.type == DATA_TYPE and p.subtype == SUBTYPES[DATA_TYPE]['nvs']


def _is_factory_target(p: PartitionDefinition) -> bool:
    return p.type == APP_TYPE and p.subtype in (SUBTYPES[APP_TYPE]['factory'], SUBTYPES[APP_TYPE]['ota_0'])


def factory_target(table: PartitionTable) -> Optional[PartitionDefinition]:
    """The partition a factory flash of `table` writes: factory, else ota_0."""
    for subtype in ('factory', 'ota_0'):
        found = next(table.find_by_type(APP_TYPE, subtype), None)
        if found:
            return found
    return None


def nvs_target(table: PartitionTable, name: Optional[str]) -> Optional[PartitionDefinition]:
    if name is not None:
        return table.find_by_name(name)
    return next((p for p in table if _is_nvs(p)), None)


@dataclass
class Op:
    """One manifest op, numbered from 1 for messages."""
    index: int
    op: str
    partition: Optional[str] = None
    file: Optional[str] = None
    put: dict = field(default_factory=dict)
    delete: list = field(default_factory=list)
    set: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, index: int, data) -> 'Op':
        where = f"manifest.json: op {index}"
        if not isinstance(data, dict):
            raise BundleError(f"{where} must be an object")
        op = data.get('op')
        if op not in OPS:
            raise BundleError(f"{where}: unknown op {op!r} (expected one of {', '.join(OPS)})")
        for name in OPS[op]:
            if not isinstance(data.get(name), str) or not data[name]:
                raise BundleError(f"{where} ({op}): \"{name}\" is required")
        if 'partition' in data and not isinstance(data['partition'], str):
            raise BundleError(f"{where} ({op}): \"partition\" must be a string")
        put, delete, set_ = data.get('put', {}), data.get('delete', []), data.get('set', {})
        if not isinstance(put, dict) or not isinstance(set_, dict) or not isinstance(delete, list):
            raise BundleError(f"{where} ({op}): \"put\" and \"set\" must be objects, "
                              f"\"delete\" a list")
        if op == 'edit-fs' and not (put or delete):
            raise BundleError(f"{where} (edit-fs): needs \"put\" and/or \"delete\"")
        if op == 'set-nvs' and not (set_ or delete):
            raise BundleError(f"{where} (set-nvs): needs \"set\" and/or \"delete\"")
        return cls(index, op, data.get('partition'), data.get('file'),
                   {str(k): str(v) for k, v in put.items()}, [str(d) for d in delete],
                   dict(set_))

    @property
    def files(self) -> list[str]:
        return ([self.file] if self.file else []) + list(self.put.values())

    def describe(self) -> str:
        if self.op == 'write':
            return f"Write {self.file} to partition '{self.partition}'"
        if self.op == 'erase':
            return f"Erase partition '{self.partition}'"
        if self.op == 'write-fs':
            return f"Write filesystem image {self.file} to partition '{self.partition}'"
        if self.op == 'edit-fs':
            what = [f"put {', '.join(self.put)}" if self.put else '',
                    f"delete {', '.join(self.delete)}" if self.delete else '']
            return f"Update files in '{self.partition}': {'; '.join(w for w in what if w)}"
        if self.op == 'set-nvs':
            what = [f"{k} = {v}" for k, v in self.set.items()] + [f"delete {d}" for d in self.delete]
            where = f" ('{self.partition}')" if self.partition else ''
            return f"Update NVS{where}: {', '.join(what)}"
        if self.op == 'set-boot':
            return f"Boot from '{self.partition}'"
        return "Clear the OTA selection so the factory app boots"


@dataclass
class Manifest:
    name: Optional[str] = None
    description: Optional[str] = None
    chip: Optional[str] = None
    table: str = 'update'
    table_match: str = 'exact'
    ops: list[Op] = field(default_factory=list)

    @classmethod
    def from_json(cls, data) -> 'Manifest':
        if not isinstance(data, dict):
            raise BundleError("manifest.json must be a JSON object")
        name = data.get('name')
        if name is not None and (not isinstance(name, str) or not name):
            raise BundleError('manifest.json: "name" must be a non-empty string')
        description = data.get('description')
        if description is not None and not isinstance(description, str):
            raise BundleError('manifest.json: "description" must be a string')
        chip = data.get('chip')
        if chip is not None:
            if not isinstance(chip, str) or normalise_chip(chip) not in CHIP_DEFS:
                raise BundleError(f"manifest.json: unknown chip {chip!r}")
            chip = normalise_chip(chip)
        table = data.get('table', 'update')
        if table not in POLICIES:
            raise BundleError(f"manifest.json: \"table\" must be one of {', '.join(POLICIES)}")
        table_match = data.get('tableMatch', 'exact')
        if table_match not in MATCHES:
            raise BundleError(f"manifest.json: \"tableMatch\" must be one of {', '.join(MATCHES)}")
        ops = data.get('ops')
        if ops is not None and (not isinstance(ops, list) or not ops):
            raise BundleError('manifest.json: "ops" must be a non-empty list')
        return cls(name, description, chip, table, table_match,
                   [Op.from_json(i, op) for i, op in enumerate(ops or [], 1)])


@dataclass
class Bundle:
    source: str
    #: Every entry by its full name, for the files ops use.
    files: dict[str, bytes]
    table: Optional[PartitionTable] = None
    table_file: Optional[str] = None
    bootloader: Optional[bytes] = None
    #: 'factory' or 'ota', and its app.
    role: Optional[str] = None
    role_app: Optional[bytes] = None
    #: Named writes, by partition name.
    partitions: dict[str, bytes] = field(default_factory=dict)
    manifest: Optional[Manifest] = None

    @property
    def name(self) -> str:
        if self.manifest and self.manifest.name:
            return self.manifest.name
        return os.path.splitext(os.path.basename(self.source))[0]

    @property
    def ops(self) -> list[Op]:
        return self.manifest.ops if self.manifest else []

    @property
    def role_file(self) -> Optional[str]:
        return f"{ROLE_PREFIX}{self.role}.bin" if self.role else None

    def written_names(self) -> list[str]:
        """Partitions written or erased by name, by file or op."""
        return list(self.partitions) + [op.partition for op in self.ops if op.op in WRITING_OPS]

    def outline(self) -> list[str]:
        """What flashing does, step by step."""
        steps = []
        if self.table_file:
            steps.append(f"Write the partition table ({self.table_file}) if it differs")
        if self.bootloader is not None:
            steps.append("Write bootloader.bin")
        if self.role == 'factory':
            steps.append(f"Flash {self.role_file} to the factory partition (or ota_0) and boot it")
        elif self.role == 'ota':
            steps.append(f"Write {self.role_file} to the next OTA slot and boot it")
        steps += [f"Write {name}.bin to partition '{name}'" for name in self.partitions]
        return steps + [op.describe() for op in self.ops]


def read_bundle(path: str, partition_table_offset: int, primary_bootloader_offset: Optional[int],
                recovery_bootloader_offset: Optional[int]) -> Bundle:
    """Read a bundle by the filename convention, and check it is self-consistent."""
    if os.path.getsize(path) == 0:
        raise BundleError(f"Bundle '{path}' is empty")
    try:
        zf = ZipFile(path, 'r')
    except BadZipFile as e:
        raise BundleError(f"Bundle '{path}' is not a valid ZIP archive") from e
    with zf:
        files = {info.filename: zf.read(info) for info in zf.infolist() if not info.is_dir()}

    bundle = Bundle(path, files)
    for name, data in files.items():
        if '/' in name:
            continue  # files for the manifest's ops
        stem, ext = os.path.splitext(name)
        if name == MANIFEST:
            try:
                bundle.manifest = Manifest.from_json(json.loads(data))
            except ValueError as e:
                raise BundleError(f"manifest.json is not valid JSON: {e}") from e
        elif stem == 'partition_table' and ext in ('.csv', '.bin'):
            source = f"bundle '{path}'"
            if data[:2] == PartitionDefinition.MAGIC_BYTES:
                bundle.table = require_partitions(PartitionTable.from_binary(data), source)
            else:
                if not data.strip():
                    raise BundleError(f"Bundle '{path}' contains an empty {name}")
                bundle.table = parse_partition_table_csv(
                    data.decode('utf-8'), source, partition_table_offset,
                    primary_bootloader_offset, recovery_bootloader_offset)
            bundle.table_file = name
        elif ext != '.bin':
            continue
        elif stem == 'bootloader':
            bundle.bootloader = data
        elif stem.startswith(ROLE_PREFIX):
            role = stem[len(ROLE_PREFIX):]
            if role not in ROLES:
                raise BundleError(f"Unknown role file '{name}' (only @factory.bin and @ota.bin)")
            if bundle.role:
                raise BundleError("A bundle cannot carry both @factory.bin and @ota.bin")
            bundle.role, bundle.role_app = role, data
        else:
            bundle.partitions[stem] = data

    if not bundle.outline():
        raise BundleError(f"Bundle '{path}' has nothing to flash")
    for op in bundle.ops:
        for name in op.files:
            if name not in files:
                raise BundleError(f"manifest.json: op {op.index} ({op.op}): "
                                  f"'{name}' is not in the bundle")
    return bundle


# --- checking a bundle against a device ---------------------------------------------------

def _same(a: PartitionDefinition, b: PartitionDefinition) -> bool:
    return a == b and a.encrypted == b.encrypted and a.readonly == b.readonly


def _kind(p: PartitionDefinition) -> str:
    type_name = next((k for k, v in TYPES.items() if v == p.type), str(p.type))
    subtype = next((k for k, v in SUBTYPES.get(p.type, {}).items() if v == p.subtype), str(p.subtype))
    return f"{type_name}/{subtype}"


def _size(size: int) -> str:
    return f"{size // 1024}K" if size % 1024 == 0 else f"{size:#x}"


@dataclass
class Difference:
    """A partition that differs between the bundle's table and the device's, by name."""
    name: str
    expected: Optional[PartitionDefinition] = None
    actual: Optional[PartitionDefinition] = None

    def __str__(self) -> str:
        e, a = self.expected, self.actual
        if a is None:
            return f"{self.name}: new at {e.offset:#x} ({_size(e.size)})"
        if e is None:
            return f"{self.name}: removed (was at {a.offset:#x}, {_size(a.size)})"
        what = []
        if a.offset != e.offset:
            what.append(f"moved {a.offset:#x} → {e.offset:#x}")
        if a.size != e.size:
            what.append(f"resized {_size(a.size)} → {_size(e.size)}")
        if (a.type, a.subtype) != (e.type, e.subtype):
            what.append(f"{_kind(a)} → {_kind(e)}")
        if (a.encrypted, a.readonly) != (e.encrypted, e.readonly):
            what.append(f"flags {':'.join(a.get_flags_list()) or 'none'} → "
                        f"{':'.join(e.get_flags_list()) or 'none'}")
        return f"{self.name}: {', '.join(what)}"


def compare_tables(expected: PartitionTable, actual: Optional[PartitionTable]) -> list[Difference]:
    """How `actual` differs from `expected`; a missing `actual` differs everywhere."""
    actual = actual or PartitionTable()
    differences = []
    for e in expected:
        a = actual.find_by_name(e.name)
        if a is None or not _same(a, e):
            differences.append(Difference(e.name, e, a))
    differences += [Difference(a.name, actual=a) for a in actual if expected.find_by_name(a.name) is None]
    return differences


@dataclass
class BundleCheck:
    """What flashing a bundle will meet on a device, worked out before anything is written."""
    device_table: Optional[PartitionTable]
    table: Optional[PartitionTable]
    policy: str = 'update'
    match: str = 'exact'
    differences: list[Difference] = field(default_factory=list)
    #: Partitions the bundle uses, by name.
    used: set[str] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)

    @property
    def table_changes(self) -> bool:
        return self.table is not None and bool(self.differences)

    @property
    def can_keep_layout(self) -> bool:
        """The device's differing table may stay: allowed, and nothing the bundle uses differs."""
        return (self.table_changes and self.match == 'used' and self.device_table is not None
                and not any(d.name in self.used for d in self.differences))

    @property
    def blocker(self) -> Optional[str]:
        if self.table_changes and self.policy == 'require' and not self.can_keep_layout:
            if self.device_table is None:
                return "This bundle needs a device that already has its partition layout, and this one has none"
            if self.match == 'used':
                return "This device's partition layout differs where this bundle writes"
            return "This device's partition layout is different from the one this bundle requires"
        if self.problems:
            return '; '.join(self.problems)
        return None


def check_bundle(bundle: Bundle, device_table: Optional[PartitionTable],
                 virtual_names=()) -> BundleCheck:
    """Check `bundle` against the device's table. Names resolve against the bundle's table if
    it carries one, else the device's; `virtual_names` (the bootloader, the table) always do."""
    table = bundle.table
    current = table if table is not None else device_table
    used: set[str] = set()
    problems: list[str] = []

    def use_where(test):
        # Picked by kind, not name: an OTA flash may pick a slot either table has.
        for t in (table, device_table):
            used.update(p.name for p in t or () if test(p))

    def need(name, what):
        if name in virtual_names:
            return
        if current is None:
            problems.append(f"{what}: the device has no partition table")
        elif current.find_by_name(name) is None:
            problems.append(f"{what}: '{name}' is not a partition on this device")
        used.add(name)

    def need_where(test, kind, what):
        if current is None:
            problems.append(f"{what}: the device has no partition table")
        elif not any(test(p) for p in current):
            problems.append(f"{what}: no {kind} partition on this device")

    if bundle.role == 'factory':
        need_where(_is_factory_target, 'factory or ota_0', bundle.role_file)
        use_where(lambda p: _is_factory_target(p) or _is_otadata(p))
    elif bundle.role == 'ota':
        need_where(is_ota_app, 'OTA app', bundle.role_file)
        use_where(lambda p: is_ota_app(p) or _is_otadata(p))
    for name in bundle.partitions:
        need(name, f"{name}.bin")
    for op in bundle.ops:
        what = f"op {op.index} ({op.op})"
        if op.op == 'clear-boot':
            need_where(_is_otadata, 'otadata', what)
            use_where(_is_otadata)
        elif op.op == 'set-nvs' and op.partition is None:
            need_where(_is_nvs, 'NVS', what)
            if current is not None and (p := nvs_target(current, None)):
                used.add(p.name)
        else:
            need(op.partition, what)
            if op.op == 'set-boot':
                use_where(_is_otadata)

    if current is not None:
        problems += role_conflicts(bundle, current)

    return BundleCheck(
        device_table, table,
        bundle.manifest.table if bundle.manifest else 'update',
        bundle.manifest.table_match if bundle.manifest else 'exact',
        compare_tables(table, device_table) if table is not None else [],
        used, problems)


def role_conflicts(bundle: Bundle, table: PartitionTable) -> list[str]:
    """Named writes or erases of a partition the role file may pick."""
    if not bundle.role:
        return []
    factory = factory_target(table)
    problems = []
    for name in dict.fromkeys(bundle.written_names()):
        p = table.find_by_name(name)
        if p is None:
            continue
        if bundle.role == 'factory' and factory is not None and name == factory.name:
            problems.append(f"'{name}' is written by name and by {bundle.role_file}")
        if bundle.role == 'ota' and is_ota_app(p):
            problems.append(f"'{name}' is written by name while {bundle.role_file} may pick it")
    return problems
