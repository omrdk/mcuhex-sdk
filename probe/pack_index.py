"""Make the CMSIS pack index on disk match what the vendor index says exists.

cmsis_pack_manager downloads pack descriptors in Rust and then writes
``index.json`` from whatever landed. On Windows its downloader drops every
descriptor keil.com redirects to Azure blob storage -- all of ST's, among
others -- without raising, so the index it writes is missing thousands of
parts while looking healthy: non-empty, freshly written, no error. A server
that trusts a non-empty index then tells the user their part does not exist.

Keil's ``index.pidx`` lists every pack with the URL it is served from, and
Python's own HTTP stack follows those redirects fine (verified on the same
machine the Rust downloader failed on). So the check is: parse the pidx,
compare against the files in the cache directory, fetch what is missing with
``urllib``, and let cmsis_pack_manager rebuild its index from the now complete
set of files. Nothing here parses a descriptor; that stays with the library.

Only packs served from the pidx's own host count. Every file the downloader
dropped came from there; the rest of the pidx points at vendors' own servers,
and some of those entries are dead upstream (404, 403, a host that never
answers) on every platform. Counting them would call a healthy index
incomplete forever and retry dead addresses at every start.

File layout follows cmsis_pack_manager: a descriptor is
``<data_path>/<Vendor>.<Name>.<Version>.pdsc`` and a pack is
``<data_path>/<Vendor>/<Name>/<Version>.pack``. The wire names follow the
CMSIS-Pack spec: ``<url><Vendor>.<Name>.pdsc`` and
``<url><Vendor>.<Name>.<Version>.pack``.
"""
import logging
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional

LOG = logging.getLogger("pack-index")

PIDX_URL = "https://www.keil.com/pack/index.pidx"

# One pack descriptor is ~100 KB; a pack can be hundreds of MB.
FETCH_TIMEOUT_S = 60


@dataclass(frozen=True)
class PackRef:
    """One pack as the vendor index names it."""

    vendor: str
    name: str
    version: str
    url: str

    @classmethod
    def from_device(cls, index_entry: dict) -> "PackRef":
        """From a device entry of cmsis_pack_manager's ``index.json``."""
        p = index_entry["from_pack"]
        return cls(p["vendor"], p["pack"], p["version"], p["url"])

    @property
    def descriptor_filename(self) -> str:
        return f"{self.vendor}.{self.name}.{self.version}.pdsc"

    @property
    def descriptor_url(self) -> str:
        return f"{_slashed(self.url)}{self.vendor}.{self.name}.pdsc"

    def pack_path(self, data_path: str) -> str:
        return os.path.join(data_path, self.vendor, self.name, f"{self.version}.pack")

    @property
    def pack_url(self) -> str:
        return f"{_slashed(self.url)}{self.vendor}.{self.name}.{self.version}.pack"


@dataclass(frozen=True)
class IndexReport:
    expected: int
    missing: int
    fetched: int

    @property
    def still_missing(self) -> int:
        return self.missing - self.fetched

    @property
    def complete(self) -> bool:
        return self.still_missing == 0


def expected_descriptors(pidx_xml: bytes) -> List[PackRef]:
    """The packs the vendor index serves from its own host, deprecated ones
    included: those still have a descriptor on disk when the library
    downloads everything. Empty when the document is not a pack index.
    """
    try:
        root = ET.fromstring(pidx_xml)
    except ET.ParseError as e:
        LOG.warning(f"Pack index is not well-formed XML: {e}")
        return []
    own = root.findtext("url")
    if not own:
        LOG.warning("Pack index names no host of its own")
        return []
    own_host = _host(own)
    refs = []
    for pdsc in root.iter("pdsc"):
        try:
            ref = PackRef(pdsc.attrib["vendor"], pdsc.attrib["name"],
                          pdsc.attrib["version"], pdsc.attrib["url"])
        except KeyError as e:
            LOG.debug(f"Skipping pidx entry without {e}: {pdsc.attrib}")
            continue
        if _host(ref.url) == own_host:
            refs.append(ref)
    return refs


def missing_descriptors(refs: Iterable[PackRef], data_path: str) -> List[PackRef]:
    return [r for r in refs if not os.path.exists(os.path.join(data_path, r.descriptor_filename))]


def fetch_descriptor(ref: PackRef, data_path: str) -> bool:
    """Download one descriptor into the cache; False on any failure, logged."""
    return _fetch(ref.descriptor_url, os.path.join(data_path, ref.descriptor_filename))


def complete_index(cache, on_progress: Optional[Callable[[int, int], None]] = None) -> IndexReport:
    """Fetch the descriptors the library's downloader left out and rebuild.

    ``on_progress(done, total)`` is called before each fetch so a caller can
    show how far along a first run is. ``cache_descriptors`` is only called
    when at least one file was fetched: the library re-parses every descriptor
    on disk and rewrites the index, and when nothing changed that work would
    change nothing.
    """
    data_path = cache.data_path
    os.makedirs(data_path, exist_ok=True)

    pidx = _read(PIDX_URL)
    if pidx is None:
        return IndexReport(expected=0, missing=0, fetched=0)
    refs = expected_descriptors(pidx)
    missing = missing_descriptors(refs, data_path)
    if not missing:
        return IndexReport(expected=len(refs), missing=0, fetched=0)

    LOG.info(f"Pack index is missing {len(missing)} of {len(refs)} descriptors, fetching")
    fetched = 0
    for i, ref in enumerate(missing):
        if on_progress:
            on_progress(i, len(missing))
        fetched += fetch_descriptor(ref, data_path)
    if fetched:
        cache.cache_descriptors()
    report = IndexReport(expected=len(refs), missing=len(missing), fetched=fetched)
    if not report.complete:
        LOG.warning(f"Pack index still missing {report.still_missing} descriptors")
    return report


def ensure_pack_file(cache, ref: PackRef) -> bool:
    """Whether the pack file is on disk, downloading it ourselves if it is not.

    The same downloader that drops descriptors serves the packs, and pyOCD
    decides a pack is installed by the presence of this file alone.
    """
    path = ref.pack_path(cache.data_path)
    if os.path.exists(path):
        return True
    return _fetch(ref.pack_url, path)


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def _slashed(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def _read(url: str):
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as resp:
            return resp.read()
    except Exception as e:
        LOG.warning(f"Could not fetch {url}: {e}")
        return None


def _fetch(url: str, dest: str) -> bool:
    """Download to `dest` through a temp name so a cut connection never leaves
    a partial file that the library would parse as a real one."""
    data = _read(url)
    if data is None:
        return False
    tmp = dest + ".part"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
    except OSError as e:
        LOG.warning(f"Could not write {dest}: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    return True
