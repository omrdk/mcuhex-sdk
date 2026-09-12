"""Behavioral tests for completing the CMSIS pack index from the vendor index."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import probe.pack_index as pack_index
from probe.pack_index import (
    PIDX_URL,
    PackRef,
    complete_index,
    ensure_pack_file,
    expected_descriptors,
    missing_descriptors,
)

# Five lines of Keil's real index.pidx (2026-09-11): three packs served from
# the index's own host (one without a trailing slash) and two from vendors'
# own servers, one of them deprecated.
PIDX = b"""<?xml version="1.0" encoding="UTF-8" ?>
<index schemaVersion="1.1.0" xs:noNamespaceSchemaLocation="PackIndex.xsd" xmlns:xs="http://www.w3.org/2001/XMLSchema-instance">
<vendor>Keil</vendor>
<url>https://www.keil.com/pack/</url>
<timestamp>2026-09-11T04:04:24.2812915+00:00</timestamp>
<pindex>
  <pdsc url="https://www.silabs.com/documents/public/cmsis-packs/" vendor="SiliconLabs" name="BGM21_DFP" version="5.8.10" deprecated="2019-11-12" replacement="SiliconLabs.GeckoPlatform_BGM21_DFP" />
  <pdsc url="https://www.keil.com/pack/" vendor="Keil" name="STM32G0xx_DFP" version="2.1.0" />
  <pdsc url="https://www.keil.com/pack/" vendor="Keil" name="STM32F1xx_DFP" version="2.4.1" />
  <pdsc url="http://www.advsolned.com/armpack/" vendor="ASN" name="Filter_Designer" version="1.0.3" />
  <pdsc url="https://www.keil.com/pack" vendor="Keil" name="STM32F2xx_DFP" version="3.1.0" />
</pindex>
</index>
"""

G0 = PackRef("Keil", "STM32G0xx_DFP", "2.1.0", "https://www.keil.com/pack/")


class FakeCache:
    def __init__(self, data_path):
        self.data_path = str(data_path)
        self.rebuilds = 0

    def cache_descriptors(self):
        self.rebuilds += 1


class FakeWeb:
    """Answers urlopen from a dict; a URL not in it fails like a dropped fetch."""

    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def __call__(self, url, timeout):
        self.requested.append(url)
        if url not in self.pages:
            raise OSError(f"no route to {url}")
        return _Response(self.pages[url])


class _Response:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def web(monkeypatch):
    def install(pages):
        fake = FakeWeb(pages)
        monkeypatch.setattr(pack_index.urllib.request, "urlopen", fake)
        return fake
    return install


def descriptor_of(ref, data_path):
    return Path(data_path) / ref.descriptor_filename


def test_only_packs_served_from_the_index_host_are_expected():
    refs = expected_descriptors(PIDX)

    assert [r.name for r in refs] == ["STM32G0xx_DFP", "STM32F1xx_DFP", "STM32F2xx_DFP"]


def test_a_deprecated_pack_on_the_index_host_still_counts():
    pidx = PIDX.replace(b'name="STM32F1xx_DFP" version="2.4.1"',
                        b'name="STM32F1xx_DFP" version="2.4.1" deprecated="2025-01-01" replacement="Keil.STM32F1xx_DFP"')

    assert len(expected_descriptors(pidx)) == 3


def test_an_index_without_a_host_of_its_own_yields_nothing():
    assert expected_descriptors(PIDX.replace(b"<url>https://www.keil.com/pack/</url>", b"")) == []


def test_a_document_that_is_not_a_pack_index_yields_nothing():
    assert expected_descriptors(b"<html>maintenance</html>") == []
    assert expected_descriptors(b"not xml at all <") == []


def test_names_follow_the_library_on_disk_and_the_spec_on_the_wire(tmp_path):
    assert G0.descriptor_filename == "Keil.STM32G0xx_DFP.2.1.0.pdsc"
    assert G0.descriptor_url == "https://www.keil.com/pack/Keil.STM32G0xx_DFP.pdsc"
    assert G0.pack_url == "https://www.keil.com/pack/Keil.STM32G0xx_DFP.2.1.0.pack"
    assert G0.pack_path(str(tmp_path)) == str(tmp_path / "Keil" / "STM32G0xx_DFP" / "2.1.0.pack")


def test_a_url_without_a_trailing_slash_still_joins_cleanly():
    f2 = [r for r in expected_descriptors(PIDX) if r.name == "STM32F2xx_DFP"][0]

    assert f2.descriptor_url == "https://www.keil.com/pack/Keil.STM32F2xx_DFP.pdsc"


def test_a_pack_ref_is_read_from_the_index_entry_of_a_device():
    entry = {"name": "STM32G070RBTx", "from_pack": {
        "vendor": "Keil", "pack": "STM32G0xx_DFP", "version": "2.1.0", "url": "https://www.keil.com/pack/"}}

    assert PackRef.from_device(entry) == G0


def test_only_descriptors_absent_from_disk_are_missing(tmp_path):
    refs = expected_descriptors(PIDX)
    descriptor_of(G0, tmp_path).write_bytes(b"<package/>")

    missing = missing_descriptors(refs, str(tmp_path))

    assert [r.name for r in missing] == ["STM32F1xx_DFP", "STM32F2xx_DFP"]


def test_a_complete_cache_is_left_alone(tmp_path, web):
    fake = web({PIDX_URL: PIDX})
    for ref in expected_descriptors(PIDX):
        descriptor_of(ref, tmp_path).write_bytes(b"<package/>")
    cache = FakeCache(tmp_path)

    report = complete_index(cache)

    assert (report.expected, report.missing, report.fetched) == (3, 0, 0)
    assert report.complete
    assert cache.rebuilds == 0
    assert fake.requested == [PIDX_URL]


def test_missing_descriptors_are_fetched_and_the_index_rebuilt(tmp_path, web):
    refs = expected_descriptors(PIDX)
    web({PIDX_URL: PIDX, **{r.descriptor_url: b"<package>%s</package>" % r.name.encode() for r in refs}})
    cache = FakeCache(tmp_path)

    report = complete_index(cache)

    assert (report.expected, report.missing, report.fetched) == (3, 3, 3)
    assert report.complete
    assert cache.rebuilds == 1
    assert descriptor_of(G0, tmp_path).read_bytes() == b"<package>STM32G0xx_DFP</package>"


def test_progress_is_reported_before_each_fetch(tmp_path, web):
    refs = expected_descriptors(PIDX)
    web({PIDX_URL: PIDX, **{r.descriptor_url: b"<package/>" for r in refs}})
    seen = []

    complete_index(FakeCache(tmp_path), on_progress=lambda done, total: seen.append((done, total)))

    assert seen == [(0, 3), (1, 3), (2, 3)]


def test_a_descriptor_that_will_not_download_is_counted_not_raised(tmp_path, web):
    refs = expected_descriptors(PIDX)
    reachable = [r for r in refs if r != G0]
    web({PIDX_URL: PIDX, **{r.descriptor_url: b"<package/>" for r in reachable}})
    cache = FakeCache(tmp_path)

    report = complete_index(cache)

    assert (report.missing, report.fetched, report.still_missing) == (3, 2, 1)
    assert not report.complete
    assert cache.rebuilds == 1
    assert not descriptor_of(G0, tmp_path).exists()


def test_an_unreachable_vendor_index_changes_nothing(tmp_path, web):
    web({})
    cache = FakeCache(tmp_path)

    report = complete_index(cache)

    assert (report.expected, report.missing, report.fetched) == (0, 0, 0)
    assert cache.rebuilds == 0


def test_a_missing_cache_directory_is_created(tmp_path, web):
    web({PIDX_URL: PIDX})
    cache = FakeCache(tmp_path / "not" / "yet")

    complete_index(cache)

    assert Path(cache.data_path).is_dir()


def test_a_cut_download_leaves_no_partial_descriptor(tmp_path, web, monkeypatch):
    web({PIDX_URL: PIDX, G0.descriptor_url: b"<package/>"})
    cache = FakeCache(tmp_path)
    real_open = open

    def failing_open(path, mode="r", *a, **k):
        if str(path).endswith(".part"):
            raise OSError("disk full")
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr("builtins.open", failing_open)

    report = complete_index(cache)

    assert report.fetched == 0
    assert list(tmp_path.iterdir()) == []


def test_a_pack_already_on_disk_is_not_fetched_again(tmp_path, web):
    fake = web({})
    cache = FakeCache(tmp_path)
    path = Path(G0.pack_path(cache.data_path))
    path.parent.mkdir(parents=True)
    path.write_bytes(b"zip")

    assert ensure_pack_file(cache, G0) is True
    assert fake.requested == []


def test_a_pack_the_library_did_not_download_is_fetched_into_its_place(tmp_path, web):
    web({G0.pack_url: b"zip"})
    cache = FakeCache(tmp_path)

    assert ensure_pack_file(cache, G0) is True
    assert Path(G0.pack_path(cache.data_path)).read_bytes() == b"zip"


def test_a_pack_that_will_not_download_is_reported_as_absent(tmp_path, web):
    web({})
    cache = FakeCache(tmp_path)

    assert ensure_pack_file(cache, G0) is False
    assert not Path(G0.pack_path(cache.data_path)).exists()
