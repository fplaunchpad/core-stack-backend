"""
Phase 1 — download GBIF occurrences for a taxon + area + year window.

Uses the GBIF **Download API** (async: request -> GBIF prepares a zip -> we fetch it), which has no
100k record cap. Results are cached on disk keyed by (taxon_key, start_year, end_year) so we never
re-download the same slice — GBIF downloads are large and slow.

Docs: https://pygbif.readthedocs.io/en/latest/modules/occurrence.html
      https://techdocs.gbif.org/en/data-use/api-downloads
"""

import os
import time
import zipfile

from pygbif import occurrences as occ

from . import config


def _cache_paths(taxon_key, start_year, end_year):
    slug = f"taxon{taxon_key}_{start_year}_{end_year}"
    base = os.path.join(config.CACHE_DIR, slug)
    return base, base + ".csv", os.path.join(base, "meta.txt")


def request_download(taxon_key, start_year, end_year, country="IN"):
    """Ask GBIF to prepare a download. Returns the download key (a citable DOI handle)."""
    predicates = [
        f"COUNTRY = {country}",
        "HAS_COORDINATE = TRUE",
        "HAS_GEOSPATIAL_ISSUE = FALSE",
        "OCCURRENCE_STATUS = PRESENT",
        f"TAXON_KEY = {taxon_key}",
        f"YEAR >= {start_year}",
        f"YEAR <= {end_year}",
        "BASIS_OF_RECORD in [{}]".format(", ".join(config.KEEP_BASIS_OF_RECORD)),
    ]
    key = occ.download(
        queries=predicates,
        format="SIMPLE_CSV",
        user=config.GBIF_USER,
        pwd=config.GBIF_PWD,
        email=config.GBIF_EMAIL,
    )
    return key[0] if isinstance(key, (list, tuple)) else key


def wait_and_fetch(download_key, dest_dir, poll_seconds=60):
    """Poll until GBIF finishes preparing the file, then download & unzip. Returns the CSV path."""
    while True:
        meta = occ.download_meta(download_key)
        st = meta["status"]  # PREPARING / RUNNING / SUCCEEDED / KILLED / CANCELLED / FAILED
        if st == "SUCCEEDED":
            break
        if st in ("KILLED", "CANCELLED", "FAILED"):
            raise RuntimeError(f"GBIF download {download_key} ended as {st}")
        time.sleep(poll_seconds)  # large downloads take minutes-hours; be polite

    occ.download_get(download_key, path=dest_dir)  # -> <dest_dir>/<key>.zip
    zip_path = os.path.join(dest_dir, f"{download_key}.zip")
    extract_dir = os.path.join(dest_dir, download_key)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract_dir)
    # SIMPLE_CSV extracts to <key>.csv (tab-separated)
    return os.path.join(extract_dir, f"{download_key}.csv")


def download_occurrences(taxon_key, start_year, end_year, country="IN"):
    """
    Cached entry point. Returns the local path to the raw occurrences CSV for this slice,
    reusing a prior download if present. Also records the DOI for citation.
    """
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    base, cached_csv, meta_path = _cache_paths(taxon_key, start_year, end_year)
    if os.path.exists(cached_csv):
        print(f"[gbif] cache hit: {cached_csv}")
        return cached_csv

    if not (config.GBIF_USER and config.GBIF_PWD and config.GBIF_EMAIL):
        raise RuntimeError(
            "GBIF_USER / GBIF_PWD / GBIF_EMAIL must be set to use the Download API."
        )

    os.makedirs(base, exist_ok=True)
    key = request_download(taxon_key, start_year, end_year, country=country)
    csv_path = wait_and_fetch(key, base)

    # Cache: move the CSV to the stable cached path and record provenance (DOI).
    os.replace(csv_path, cached_csv)
    try:
        meta = occ.download_meta(key)
        with open(meta_path, "w") as fh:
            fh.write(f"download_key={key}\ndoi={meta.get('doi')}\n")
    except Exception as exc:  # provenance is best-effort
        print(f"[gbif] could not record download meta: {exc}")
    print(f"[gbif] downloaded -> {cached_csv} (key={key})")
    return cached_csv
