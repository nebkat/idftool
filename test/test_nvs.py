"""NVS image parsing and editing — library-level tests, no device and no subprocess.

The load-bearing ones are :func:`test_encoder_matches_generator` and
:func:`test_encoder_matches_generator_with_blobs`: idftool writes NVS entries with its own
encoder (``esp-idf-nvs-partition-gen`` can only build an image from scratch, never append to
one), so these pin that encoder to Espressif's byte for byte.
"""
import argparse
import random

import pytest

from idftool.nvs import NvsError, format_entries, format_pages, parse, to_csv
from idftool.nvs.common import ENTRY_ERASED, ENTRY_WRITTEN, PAGE_SIZE
from idftool.nvs.edit import Edit, apply

SIZE = 0x6000


def generate(tmp_path, rows, size=SIZE, version=2):
    """Build a reference image with Espressif's own generator."""
    from esp_idf_nvs_partition_gen import nvs_partition_gen as gen

    csv = tmp_path / "in.csv"
    csv.write_text("key,type,encoding,value\n" + "\n".join(rows) + "\n")
    gen.generate(argparse.Namespace(input=str(csv), output="ref.bin", outdir=str(tmp_path),
                                    size=f"{size:#x}", version=version))
    return (tmp_path / "ref.bin").read_bytes()


SAMPLE_ROWS = [
    "storage,namespace,,",
    "device_id,data,u32,12345",
    "device_name,data,string,idftool-test",
    "counter,data,u16,7",
]


@pytest.fixture
def sample(tmp_path):
    return generate(tmp_path, SAMPLE_ROWS)


# -- parsing ---------------------------------------------------------------------------

def test_parse_reads_entries(sample):
    image = parse(sample)
    assert image.errors == []
    assert image.namespaces == {1: "storage"}
    assert image.get("storage", "device_id").value == 12345
    assert image.get("storage", "device_name").value == "idftool-test"
    assert image.get("storage", "counter").value == 7


def test_parse_rejects_unusable_input():
    with pytest.raises(NvsError, match="empty"):
        parse(b"")
    with pytest.raises(NvsError, match="multiple of"):
        parse(b"\xff" * 100)


def test_parse_blank_partition_is_valid():
    image = parse(b"\xff" * SIZE)
    assert image.entries == [] and image.errors == []
    assert format_entries(image.entries) == "(empty)"


def test_parse_every_primitive_width(tmp_path):
    rows = ["ns,namespace,,",
            "a,data,u8,255", "b,data,i8,-128",
            "c,data,u16,65535", "d,data,i16,-32768",
            "e,data,u32,4294967295", "f,data,i32,-2147483648",
            "g,data,u64,18446744073709551615", "h,data,i64,-9223372036854775808"]
    image = parse(generate(tmp_path, rows))
    assert image.errors == []
    assert [e.value for e in sorted(image.entries, key=lambda e: e.key)] == [
        255, -128, 65535, -32768, 4294967295, -2147483648,
        18446744073709551615, -9223372036854775808]


def test_parse_multipage_blob(tmp_path):
    payload = bytes(random.Random(1).randrange(256) for _ in range(9000))
    image = parse(generate(tmp_path, ["ns,namespace,,", f"big,data,hex2bin,{payload.hex()}"],
                           size=0x10000))
    entry = image.get("ns", "big")
    assert image.errors == []
    assert entry.value == payload and entry.size == 9000
    # Split across chunks on more than one page — the case the reassembly exists for.
    assert len({raw.page for raw in entry.raw}) > 1


def test_parse_version_1_image(tmp_path):
    payload = bytes(range(200))
    image = parse(generate(tmp_path, ["ns,namespace,,", "s,data,string,v1 string",
                                      f"b,data,hex2bin,{payload.hex()}"], version=1))
    assert image.errors == [] and image.version == 0xFF
    assert image.get("ns", "s").value == "v1 string"
    assert image.get("ns", "b").value == payload


def test_parse_reports_damage_but_keeps_going(sample):
    damaged = bytearray(sample)
    damaged[5] ^= 0xFF  # page 0's sequence number, so its header CRC no longer matches
    image = parse(bytes(damaged))
    assert any("header CRC" in e for e in image.errors)
    with pytest.raises(NvsError, match="header CRC"):
        parse(bytes(damaged), strict=True)


def test_format_pages_covers_every_page(sample):
    lines = format_pages(parse(sample)).splitlines()
    assert len(lines) == 2 + SIZE // PAGE_SIZE  # heading, rule, one row per page
    assert "active" in lines[2] and "uninitialised" in lines[3]


def test_to_csv_round_trips(tmp_path, sample):
    csv = to_csv(parse(sample).entries)
    rebuilt = tmp_path / "again.csv"
    rebuilt.write_text(csv)

    from esp_idf_nvs_partition_gen import nvs_partition_gen as gen
    gen.generate(argparse.Namespace(input=str(rebuilt), output="again.bin",
                                    outdir=str(tmp_path), size=f"{SIZE:#x}", version=2))
    again = parse((tmp_path / "again.bin").read_bytes())
    assert {(e.namespace, e.key, e.type, e.value) for e in again.entries} == \
           {(e.namespace, e.key, e.type, e.value) for e in parse(sample).entries}


def test_to_csv_quotes_awkward_values(tmp_path):
    image = parse(generate(tmp_path, ["ns,namespace,,", 'k,data,string,"a,b"']))
    assert image.get("ns", "k").value == "a,b"
    csv = to_csv(image.entries)
    assert '"a,b"' in csv


# -- the encoder, against Espressif's -----------------------------------------------------

def test_encoder_matches_generator(tmp_path, sample):
    """Building the sample content into a blank partition must reproduce the reference byte
    for byte — page headers, entry CRCs, state bitmap and all."""
    built, _, _, _ = apply(b"\xff" * SIZE, [
        Edit("storage", "device_id", "u32", 12345),
        Edit("storage", "device_name", "string", "idftool-test"),
        Edit("storage", "counter", "u16", 7),
    ])
    assert built == sample


def test_encoder_matches_generator_with_blobs(tmp_path):
    """The same, for the blob chunking that spans pages."""
    rng = random.Random(1)
    big = bytes(rng.randrange(256) for _ in range(9000))
    small = bytes(range(64))
    rows = ["alpha,namespace,,",
            "a_u8,data,u8,255", "a_i64,data,i64,-9223372036854775808",
            "a_str,data,string,hello world",
            f"a_big,data,hex2bin,{big.hex()}", f"a_small,data,hex2bin,{small.hex()}",
            "beta,namespace,,", "b_str,data,string," + "x" * 1500, "b_num,data,i32,-42"]
    reference = generate(tmp_path, rows, size=0x10000)

    built, _, _, _ = apply(b"\xff" * 0x10000, [
        Edit("alpha", "a_u8", "u8", 255),
        Edit("alpha", "a_i64", "i64", -9223372036854775808),
        Edit("alpha", "a_str", "string", "hello world"),
        Edit("alpha", "a_big", "blob", big),
        Edit("alpha", "a_small", "blob", small),
        Edit("beta", "b_str", "string", "x" * 1500),
        Edit("beta", "b_num", "i32", -42),
    ])
    assert built == reference


# -- editing ---------------------------------------------------------------------------

def test_edit_appends_in_place(sample):
    result, changes, dirty, compacted = apply(sample, [Edit("storage", "counter", "u16", 42)])
    assert not compacted and dirty == [0] and len(result) == len(sample)
    assert [c.action for c in changes] == ["set"]
    assert parse(result).get("storage", "counter").value == 42
    # Everything the edit didn't touch is untouched, down to the byte.
    assert result[PAGE_SIZE:] == sample[PAGE_SIZE:]


def test_edit_erases_the_entry_it_replaces(sample):
    result, _, _, _ = apply(sample, [Edit("storage", "counter", "u16", 42)])
    page = parse(result).pages[0]
    assert page.entry_states.count(ENTRY_ERASED) == 1
    assert page.entry_states.count(ENTRY_WRITTEN) == 5
    assert len(parse(result).entries) == 3  # still three logical keys


def test_edit_infers_type_from_the_existing_entry(sample):
    result, changes, _, _ = apply(sample, [Edit("storage", "device_name", None, "renamed")])
    assert changes[0].type == "string"
    assert parse(result).get("storage", "device_name").value == "renamed"


def test_edit_needs_a_type_for_a_new_key(sample):
    with pytest.raises(NvsError, match="type"):
        apply(sample, [Edit("storage", "brand_new", None, "x")])


def test_edit_adds_a_namespace(sample):
    result, changes, _, _ = apply(sample, [Edit("other", "serial", "string", "SN-1")])
    image = parse(result)
    assert changes[0].action == "added"
    assert image.get("other", "serial").value == "SN-1"
    assert sorted(image.namespaces.values()) == ["other", "storage"]


def test_edit_delete_removes_the_key(sample):
    result, changes, _, _ = apply(sample, [Edit("storage", "device_id")])
    assert changes[0].action == "deleted"
    image = parse(result)
    assert image.get("storage", "device_id") is None
    assert {e.key for e in image.entries} == {"device_name", "counter"}


def test_edit_deleting_a_missing_key_is_a_no_op(sample):
    result, changes, dirty, _ = apply(sample, [Edit("storage", "nope")])
    assert changes[0].action == "unchanged" and dirty == [] and result == sample


def test_edit_writing_the_same_value_changes_nothing(sample):
    """An identical write would burn flash entries for no reason, so it is skipped."""
    result, changes, dirty, _ = apply(sample, [Edit("storage", "counter", "u16", 7)])
    assert changes[0].action == "unchanged" and dirty == [] and result == sample


def test_edit_rejects_an_overlong_key(sample):
    with pytest.raises(NvsError, match="longer than"):
        apply(sample, [Edit("storage", "x" * 16, "u8", 1)])


def test_edit_refuses_a_damaged_image(sample):
    damaged = bytearray(sample)
    damaged[5] ^= 0xFF
    with pytest.raises(NvsError, match="damaged"):
        apply(bytes(damaged), [Edit("storage", "counter", "u16", 1)])


def test_edit_replaces_a_blob_and_alternates_chunk_versions():
    rng = random.Random(7)
    first = bytes(rng.randrange(256) for _ in range(5000))
    second = bytes(rng.randrange(256) for _ in range(7000))

    data, _, _, _ = apply(b"\xff" * 0x10000, [Edit("ns", "blob", "blob", first),
                                              Edit("ns", "keep", "u32", 1)])
    starts = {r.chunk_index & 0x80 for r in parse(data).get("ns", "blob").raw[1:]}
    assert starts == {0x00}

    data, _, _, _ = apply(data, [Edit("ns", "blob", None, second)])
    image = parse(data)
    assert image.errors == [] and image.get("ns", "blob").value == second
    # The replacement numbers its chunks from the other offset, so a half-written update can
    # never be mistaken for the copy it replaces.
    assert {r.chunk_index & 0x80 for r in image.get("ns", "blob").raw[1:]} == {0x80}
    assert image.get("ns", "keep").value == 1


def test_edit_deleting_a_blob_erases_every_chunk():
    payload = bytes(random.Random(3).randrange(256) for _ in range(9000))
    data, _, _, _ = apply(b"\xff" * 0x10000, [Edit("ns", "blob", "blob", payload)])
    data, _, _, _ = apply(data, [Edit("ns", "blob")])
    image = parse(data)
    assert image.entries == [] and image.errors == []
    # Only the namespace entry survives; every chunk header and payload entry is erased.
    written = sum(state == ENTRY_WRITTEN
                  for page in image.pages if not page.is_uninit
                  for state in page.entry_states)
    assert written == 1


def test_edit_compacts_when_it_runs_out_of_room():
    """Churning one key fills the pages; the editor then rebuilds a compacted image."""
    data, _, _, _ = apply(b"\xff" * 0x4000, [Edit("ns", "k", "u32", 0),
                                             Edit("ns", "pad", "string", "p" * 900)])
    for i in range(1, 500):
        data, _, _, compacted = apply(data, [Edit("ns", "k", None, i)])
        if compacted:
            break
    else:
        pytest.fail("never ran out of room")

    image = parse(data)
    assert len(data) == 0x4000 and image.errors == []
    assert image.get("ns", "k").value == i
    assert image.get("ns", "pad").value == "p" * 900


def test_edit_keeps_working_after_a_compaction():
    data, _, _, _ = apply(b"\xff" * 0x4000, [Edit("ns", "k", "u32", 0),
                                             Edit("ns", "pad", "string", "p" * 900)])
    for i in range(1, 1200):
        data, _, _, _ = apply(data, [Edit("ns", "k", None, i)])
    image = parse(data)
    assert image.errors == [] and len(data) == 0x4000
    assert image.get("ns", "k").value == 1199
    assert image.get("ns", "pad").value == "p" * 900


def test_edit_rewrite_compacts_on_demand(sample):
    """--rewrite takes the compaction path even when there is room to append."""
    churned = sample
    for i in range(20):
        churned, _, _, _ = apply(churned, [Edit("storage", "counter", "u16", i)])
    assert parse(churned).pages[0].entry_states.count(ENTRY_ERASED) == 20

    result, _, _, compacted = apply(churned, [Edit("storage", "counter", "u16", 99)],
                                    force_rewrite=True)
    image = parse(result)
    assert compacted and len(result) == len(sample)
    assert image.pages[0].entry_states.count(ENTRY_ERASED) == 0
    assert image.get("storage", "counter").value == 99
    assert image.get("storage", "device_name").value == "idftool-test"


def test_edit_reserves_the_last_page_for_garbage_collection():
    """NVS needs a spare page to compact at runtime; the editor never fills the last one."""
    data = b"\xff" * 0x4000
    for i in range(400):
        data, _, _, _ = apply(data, [Edit("ns", f"k{i % 40}", "u32", i)])
    assert parse(data).pages[-1].is_uninit
