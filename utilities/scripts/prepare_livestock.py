"""Prepare village-level livestock counts aligned to LGD village IDs.

The 20th Livestock Census workbook in ``data/livestock`` contains four sheets:
rural male, rural female, urban male, and urban female. LGD village alignment is
only meaningful for the rural village sheets, so this script builds a village
level output from the two rural sheets and resolves each source village against
the LGD ``gp_mapping.01Apr2026.csv`` file.

Matching is deliberately staged:

1. bulk indexed exact joins in SQLite;
2. broader exact joins when the village name is unique inside a safer parent
   scope;
3. fuzzy scoring only for small candidate sets selected by indexed signatures.

That keeps the expensive similarity metrics from ``admin_resolve.py`` off the
full cross product while still reusing the repo's canonical normalization and
place-name scoring logic.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from typing import Iterator, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utilities.scripts.admin_resolve import (  # noqa: E402
    clean_text,
    compact_match_text,
    consonant_signature,
    normalize_match_text,
    score_candidate,
    soundex_code,
)


DEFAULT_LIVESTOCK_DIR = REPO_ROOT / "data" / "livestock"
DEFAULT_WORKBOOK = DEFAULT_LIVESTOCK_DIR / "VillageAndWardLevelDataMale-Female.xlsx"
DEFAULT_GP_MAPPING = DEFAULT_LIVESTOCK_DIR / "gp_mapping.01Apr2026.csv"
DEFAULT_OUTPUT_DIR = DEFAULT_LIVESTOCK_DIR / "processed"
DEFAULT_CACHE_DB = DEFAULT_OUTPUT_DIR / "livestock_prepare.sqlite3"

RURAL_SHEETS = {
    "Rural Male Population": "male",
    "Rural Female Population": "female",
}
XLSX_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XLSX_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


@dataclass(frozen=True)
class SheetInfo:
    name: str
    path: str


@dataclass(frozen=True)
class FuzzyCandidate:
    village_code: int
    village_name: str
    district_name: str
    subdistrict_name: str
    village_score: float
    score: float


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalize_source_text(value: object) -> str:
    return normalize_match_text(clean_text(value) or "")


def normalize_relaxed_village(value: object) -> str:
    """Normalize names with census-style adornments removed.

    The livestock workbook has names such as ``Diglipur (Rv)`` and roman-numeral
    variants. This relaxed key is used only as a high-confidence exact join,
    never as the sole fuzzy score.
    """

    text = clean_text(value) or ""
    text = re.sub(r"\((?:rv|rural|revenue village)\)", " ", text, flags=re.I)
    text = re.sub(r"\b(?:rv|rural|revenue village)\b", " ", text, flags=re.I)
    normalized = normalize_match_text(text)
    if not normalized:
        return ""

    roman = {
        "i": "1",
        "ii": "2",
        "iii": "3",
        "iv": "4",
        "v": "5",
        "vi": "6",
        "vii": "7",
        "viii": "8",
        "ix": "9",
        "x": "10",
        "xi": "11",
        "xii": "12",
        "xiii": "13",
        "xiv": "14",
        "xv": "15",
        "xvi": "16",
        "xvii": "17",
        "xviii": "18",
        "xix": "19",
        "xx": "20",
    }
    tokens = [roman.get(token, token) for token in normalized.split()]
    normalized = " ".join(tokens)
    normalized = re.sub(r"\bpart\s+([0-9]+)\b", r"\1", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def parse_int(value: object) -> int | None:
    text = clean_text(value)
    if text is None:
        return None
    if text.endswith(".0") and re.fullmatch(r"-?\d+\.0", text):
        text = text[:-2]
    if not re.fullmatch(r"-?\d+", text):
        return None
    return int(text)


def parse_count(value: object) -> int:
    parsed = parse_int(value)
    return parsed if parsed is not None else 0


def xlsx_column_index(cell_ref: str) -> int:
    index = 0
    for char in cell_ref:
        if char.isalpha():
            index = (index * 26) + ord(char.upper()) - 64
    return index - 1


def load_shared_strings(workbook: Path) -> list[str]:
    shared_strings: list[str] = []
    with zipfile.ZipFile(workbook) as archive:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return shared_strings
        for _event, element in ET.iterparse(
            archive.open("xl/sharedStrings.xml"),
            events=("end",),
        ):
            if element.tag != XLSX_MAIN_NS + "si":
                continue
            shared_strings.append(
                "".join((text.text or "") for text in element.iter(XLSX_MAIN_NS + "t"))
            )
            element.clear()
    return shared_strings


def workbook_sheets(workbook: Path) -> dict[str, SheetInfo]:
    with zipfile.ZipFile(workbook) as archive:
        workbook_root = ET.parse(archive.open("xl/workbook.xml")).getroot()
        rels_root = ET.parse(archive.open("xl/_rels/workbook.xml.rels")).getroot()
        rel_targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels_root}

        sheets: dict[str, SheetInfo] = {}
        for sheet in workbook_root.findall(XLSX_MAIN_NS + "sheets/" + XLSX_MAIN_NS + "sheet"):
            name = sheet.attrib["name"]
            target = rel_targets[sheet.attrib[XLSX_REL_NS + "id"]]
            path = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
            sheets[name] = SheetInfo(name=name, path=path)
        return sheets


def iter_sheet_rows(
    workbook: Path,
    sheet: SheetInfo,
    shared_strings: Sequence[str],
    *,
    start_row: int = 4,
) -> Iterator[dict[str, object]]:
    with zipfile.ZipFile(workbook) as archive:
        for _event, element in ET.iterparse(archive.open(sheet.path), events=("end",)):
            if element.tag != XLSX_MAIN_NS + "row":
                continue

            row_number = int(element.attrib.get("r", "0"))
            if row_number < start_row:
                element.clear()
                continue

            values = [""] * 9
            for cell in element.findall(XLSX_MAIN_NS + "c"):
                column_index = xlsx_column_index(cell.attrib.get("r", ""))
                if column_index < 0 or column_index >= len(values):
                    continue
                value_element = cell.find(XLSX_MAIN_NS + "v")
                if value_element is None or value_element.text is None:
                    continue
                if cell.attrib.get("t") == "s":
                    values[column_index] = shared_strings[int(value_element.text)]
                else:
                    values[column_index] = value_element.text

            element.clear()
            if not any(clean_text(value) for value in values[:4]):
                continue

            yield {
                "source_row": row_number,
                "state_name": values[0],
                "district_name": values[1],
                "block_name": values[2],
                "village_name": values[3],
                "cattle": parse_count(values[4]),
                "buffalo": parse_count(values[5]),
                "sheep": parse_count(values[6]),
                "goat": parse_count(values[7]),
                "pig": parse_count(values[8]),
            }


def recreate_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA temp_store=MEMORY;
        PRAGMA cache_size=-200000;

        DROP TABLE IF EXISTS gp_villages;
        DROP TABLE IF EXISTS gp_lookup_full;
        DROP TABLE IF EXISTS gp_lookup_full_relaxed;
        DROP TABLE IF EXISTS gp_lookup_state_district_village;
        DROP TABLE IF EXISTS gp_lookup_state_district_village_relaxed;
        DROP TABLE IF EXISTS gp_lookup_state_village;
        DROP TABLE IF EXISTS gp_lookup_state_village_relaxed;
        DROP TABLE IF EXISTS livestock_raw;
        DROP TABLE IF EXISTS source_entities;
        DROP TABLE IF EXISTS entity_matches;

        CREATE TABLE gp_villages (
            gp_id INTEGER PRIMARY KEY,
            source_serial_no INTEGER,
            state_code INTEGER,
            state_name TEXT,
            state_norm TEXT NOT NULL,
            district_code INTEGER,
            district_name TEXT,
            district_norm TEXT NOT NULL,
            subdistrict_code INTEGER,
            subdistrict_name TEXT,
            subdistrict_norm TEXT NOT NULL,
            village_code INTEGER NOT NULL,
            village_census2011_code TEXT,
            village_name TEXT,
            village_norm TEXT NOT NULL,
            village_relaxed_norm TEXT NOT NULL,
            village_compact TEXT NOT NULL,
            village_prefix3 TEXT NOT NULL,
            village_prefix4 TEXT NOT NULL,
            village_soundex TEXT NOT NULL,
            village_consonant TEXT NOT NULL,
            local_body_code INTEGER,
            local_body_name TEXT
        );

        CREATE TABLE livestock_raw (
            raw_id INTEGER PRIMARY KEY,
            sheet_name TEXT NOT NULL,
            sex TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            state_name TEXT NOT NULL,
            district_name TEXT NOT NULL,
            block_name TEXT NOT NULL,
            village_name TEXT NOT NULL,
            state_norm TEXT NOT NULL,
            district_norm TEXT NOT NULL,
            block_norm TEXT NOT NULL,
            village_norm TEXT NOT NULL,
            village_relaxed_norm TEXT NOT NULL,
            village_compact TEXT NOT NULL,
            village_prefix3 TEXT NOT NULL,
            village_prefix4 TEXT NOT NULL,
            village_soundex TEXT NOT NULL,
            village_consonant TEXT NOT NULL,
            cattle INTEGER NOT NULL,
            buffalo INTEGER NOT NULL,
            sheep INTEGER NOT NULL,
            goat INTEGER NOT NULL,
            pig INTEGER NOT NULL
        );

        CREATE TABLE source_entities (
            entity_id INTEGER PRIMARY KEY,
            state_name TEXT NOT NULL,
            district_name TEXT NOT NULL,
            block_name TEXT NOT NULL,
            village_name TEXT NOT NULL,
            state_norm TEXT NOT NULL,
            district_norm TEXT NOT NULL,
            block_norm TEXT NOT NULL,
            village_norm TEXT NOT NULL,
            village_relaxed_norm TEXT NOT NULL,
            village_compact TEXT NOT NULL,
            village_prefix3 TEXT NOT NULL,
            village_prefix4 TEXT NOT NULL,
            village_soundex TEXT NOT NULL,
            village_consonant TEXT NOT NULL,
            cattle_male INTEGER NOT NULL,
            buffalo_male INTEGER NOT NULL,
            sheep_male INTEGER NOT NULL,
            goat_male INTEGER NOT NULL,
            pig_male INTEGER NOT NULL,
            cattle_female INTEGER NOT NULL,
            buffalo_female INTEGER NOT NULL,
            sheep_female INTEGER NOT NULL,
            goat_female INTEGER NOT NULL,
            pig_female INTEGER NOT NULL,
            source_row_male INTEGER,
            source_row_female INTEGER
        );

        CREATE TABLE entity_matches (
            entity_id INTEGER PRIMARY KEY,
            gp_id INTEGER NOT NULL,
            village_code INTEGER NOT NULL,
            match_method TEXT NOT NULL,
            match_score REAL NOT NULL,
            match_margin REAL NOT NULL,
            candidate_count INTEGER NOT NULL
        );
        """
    )


def gp_row_to_record(row: dict[str, str]) -> tuple[object, ...]:
    village_name = clean_text(row.get("Village Name (In English)")) or ""
    village_norm = normalize_source_text(village_name)
    return (
        parse_int(row.get("S.No.")),
        parse_int(row.get("State Code")),
        clean_text(row.get("State Name")) or "",
        normalize_source_text(row.get("State Name")),
        parse_int(row.get("District Code")),
        clean_text(row.get("District Name (In English)")) or "",
        normalize_source_text(row.get("District Name (In English)")),
        parse_int(row.get("Subdistrict Code")),
        clean_text(row.get("Subdistrict Name (In English)")) or "",
        normalize_source_text(row.get("Subdistrict Name (In English)")),
        parse_int(row.get("Village Code")),
        clean_text(row.get("Village Census 2011 Code")) or "",
        village_name,
        village_norm,
        normalize_relaxed_village(village_name),
        compact_match_text(village_name),
        compact_match_text(village_name)[:3],
        compact_match_text(village_name)[:4],
        soundex_code(village_name),
        consonant_signature(village_name),
        parse_int(row.get("Local Body Code")),
        clean_text(row.get("Local Body Name (In English)")) or "",
    )


def load_gp_mapping(connection: sqlite3.Connection, gp_mapping: Path) -> int:
    insert_sql = """
        INSERT INTO gp_villages (
            source_serial_no, state_code, state_name, state_norm, district_code,
            district_name, district_norm, subdistrict_code, subdistrict_name,
            subdistrict_norm, village_code, village_census2011_code, village_name,
            village_norm, village_relaxed_norm, village_compact, village_prefix3,
            village_prefix4, village_soundex, village_consonant, local_body_code,
            local_body_name
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    rows = 0
    with gp_mapping.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        batch: list[tuple[object, ...]] = []
        for row in reader:
            record = gp_row_to_record(row)
            if record[10] is None:
                continue
            batch.append(record)
            rows += 1
            if len(batch) >= 20000:
                connection.executemany(insert_sql, batch)
                batch.clear()
        if batch:
            connection.executemany(insert_sql, batch)
    connection.commit()
    return rows


def source_row_to_record(sheet_name: str, sex: str, row: dict[str, object]) -> tuple[object, ...]:
    village_name = clean_text(row["village_name"]) or ""
    return (
        sheet_name,
        sex,
        row["source_row"],
        clean_text(row["state_name"]) or "",
        clean_text(row["district_name"]) or "",
        clean_text(row["block_name"]) or "",
        village_name,
        normalize_source_text(row["state_name"]),
        normalize_source_text(row["district_name"]),
        normalize_source_text(row["block_name"]),
        normalize_source_text(village_name),
        normalize_relaxed_village(village_name),
        compact_match_text(village_name),
        compact_match_text(village_name)[:3],
        compact_match_text(village_name)[:4],
        soundex_code(village_name),
        consonant_signature(village_name),
        row["cattle"],
        row["buffalo"],
        row["sheep"],
        row["goat"],
        row["pig"],
    )


def load_livestock_workbook(connection: sqlite3.Connection, workbook: Path) -> dict[str, int]:
    sheets = workbook_sheets(workbook)
    missing = sorted(set(RURAL_SHEETS) - set(sheets))
    if missing:
        raise ValueError(f"Missing expected rural workbook sheets: {missing}")

    shared_strings = load_shared_strings(workbook)
    insert_sql = """
        INSERT INTO livestock_raw (
            sheet_name, sex, source_row, state_name, district_name, block_name,
            village_name, state_norm, district_norm, block_norm, village_norm,
            village_relaxed_norm, village_compact, village_prefix3, village_prefix4,
            village_soundex, village_consonant, cattle, buffalo, sheep, goat, pig
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    counts: dict[str, int] = {}
    for sheet_name, sex in RURAL_SHEETS.items():
        batch: list[tuple[object, ...]] = []
        rows = 0
        for row in iter_sheet_rows(workbook, sheets[sheet_name], shared_strings):
            batch.append(source_row_to_record(sheet_name, sex, row))
            rows += 1
            if len(batch) >= 20000:
                connection.executemany(insert_sql, batch)
                batch.clear()
        if batch:
            connection.executemany(insert_sql, batch)
        counts[sheet_name] = rows
        connection.commit()
    return counts


def create_indexes(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE INDEX idx_gp_full ON gp_villages
            (state_norm, district_norm, subdistrict_norm, village_norm);
        CREATE INDEX idx_gp_full_relaxed ON gp_villages
            (state_norm, district_norm, subdistrict_norm, village_relaxed_norm);
        CREATE INDEX idx_gp_state_district_village ON gp_villages
            (state_norm, district_norm, village_norm);
        CREATE INDEX idx_gp_state_district_village_relaxed ON gp_villages
            (state_norm, district_norm, village_relaxed_norm);
        CREATE INDEX idx_gp_state_village ON gp_villages (state_norm, village_norm);
        CREATE INDEX idx_gp_state_village_relaxed ON gp_villages
            (state_norm, village_relaxed_norm);
        CREATE INDEX idx_gp_parent_signatures ON gp_villages
            (state_norm, district_norm, subdistrict_norm, village_prefix4, village_soundex);
        CREATE INDEX idx_gp_district_signatures ON gp_villages
            (state_norm, district_norm, village_prefix3, village_soundex);
        CREATE INDEX idx_gp_state_signatures ON gp_villages
            (state_norm, village_prefix4, village_soundex);

        CREATE INDEX idx_raw_source_key ON livestock_raw
            (state_norm, district_norm, block_norm, village_norm, sex);
        """
    )


def build_source_entities(connection: sqlite3.Connection) -> int:
    connection.executescript(
        """
        INSERT INTO source_entities (
            state_name, district_name, block_name, village_name,
            state_norm, district_norm, block_norm, village_norm,
            village_relaxed_norm, village_compact, village_prefix3, village_prefix4,
            village_soundex, village_consonant,
            cattle_male, buffalo_male, sheep_male, goat_male, pig_male,
            cattle_female, buffalo_female, sheep_female, goat_female, pig_female,
            source_row_male, source_row_female
        )
        SELECT
            MIN(state_name), MIN(district_name), MIN(block_name), MIN(village_name),
            state_norm, district_norm, block_norm, village_norm,
            village_relaxed_norm, village_compact, village_prefix3, village_prefix4,
            village_soundex, village_consonant,
            SUM(CASE WHEN sex = 'male' THEN cattle ELSE 0 END),
            SUM(CASE WHEN sex = 'male' THEN buffalo ELSE 0 END),
            SUM(CASE WHEN sex = 'male' THEN sheep ELSE 0 END),
            SUM(CASE WHEN sex = 'male' THEN goat ELSE 0 END),
            SUM(CASE WHEN sex = 'male' THEN pig ELSE 0 END),
            SUM(CASE WHEN sex = 'female' THEN cattle ELSE 0 END),
            SUM(CASE WHEN sex = 'female' THEN buffalo ELSE 0 END),
            SUM(CASE WHEN sex = 'female' THEN sheep ELSE 0 END),
            SUM(CASE WHEN sex = 'female' THEN goat ELSE 0 END),
            SUM(CASE WHEN sex = 'female' THEN pig ELSE 0 END),
            MIN(CASE WHEN sex = 'male' THEN source_row END),
            MIN(CASE WHEN sex = 'female' THEN source_row END)
        FROM livestock_raw
        GROUP BY state_norm, district_norm, block_norm, village_norm;

        CREATE INDEX idx_source_unmatched ON source_entities
            (state_norm, district_norm, block_norm, village_norm);
        CREATE INDEX idx_source_unmatched_relaxed ON source_entities
            (state_norm, district_norm, block_norm, village_relaxed_norm);
        """
    )
    return int(connection.execute("SELECT COUNT(*) FROM source_entities").fetchone()[0])


UniqueMapValue = tuple[int, int] | None


def register_unique_key(
    mapping: dict[tuple[str, ...], UniqueMapValue],
    key: tuple[str, ...],
    *,
    gp_id: int,
    village_code: int,
) -> None:
    if any(part == "" for part in key):
        return
    existing = mapping.get(key)
    if existing is None and key in mapping:
        return
    if existing is None:
        mapping[key] = (gp_id, village_code)
        return
    existing_gp_id, existing_village_code = existing
    if existing_village_code != village_code:
        mapping[key] = None
        return
    if gp_id < existing_gp_id:
        mapping[key] = (gp_id, village_code)


def build_exact_unique_maps(
    connection: sqlite3.Connection,
) -> dict[str, dict[tuple[str, ...], UniqueMapValue]]:
    maps: dict[str, dict[tuple[str, ...], UniqueMapValue]] = {
        "exact_state_district_subdistrict_village": {},
        "exact_relaxed_state_district_subdistrict_village": {},
        "exact_state_district_village_unique": {},
        "exact_relaxed_state_district_village_unique": {},
        "exact_state_village_unique": {},
        "exact_relaxed_state_village_unique": {},
    }
    rows = connection.execute(
        """
        SELECT
            gp_id, village_code, state_norm, district_norm, subdistrict_norm,
            village_norm, village_relaxed_norm
        FROM gp_villages
        """
    )
    for row in rows:
        gp_id = int(row[0])
        village_code = int(row[1])
        state_norm = str(row[2])
        district_norm = str(row[3])
        subdistrict_norm = str(row[4])
        village_norm = str(row[5])
        village_relaxed_norm = str(row[6])
        register_unique_key(
            maps["exact_state_district_subdistrict_village"],
            (state_norm, district_norm, subdistrict_norm, village_norm),
            gp_id=gp_id,
            village_code=village_code,
        )
        register_unique_key(
            maps["exact_relaxed_state_district_subdistrict_village"],
            (state_norm, district_norm, subdistrict_norm, village_relaxed_norm),
            gp_id=gp_id,
            village_code=village_code,
        )
        register_unique_key(
            maps["exact_state_district_village_unique"],
            (state_norm, district_norm, village_norm),
            gp_id=gp_id,
            village_code=village_code,
        )
        register_unique_key(
            maps["exact_relaxed_state_district_village_unique"],
            (state_norm, district_norm, village_relaxed_norm),
            gp_id=gp_id,
            village_code=village_code,
        )
        register_unique_key(
            maps["exact_state_village_unique"],
            (state_norm, village_norm),
            gp_id=gp_id,
            village_code=village_code,
        )
        register_unique_key(
            maps["exact_relaxed_state_village_unique"],
            (state_norm, village_relaxed_norm),
            gp_id=gp_id,
            village_code=village_code,
        )
    return maps


def run_exact_matching(connection: sqlite3.Connection) -> dict[str, int]:
    stages = [
        (
            "exact_state_district_subdistrict_village",
            1.0,
            ("state_norm", "district_norm", "block_norm", "village_norm"),
        ),
        (
            "exact_relaxed_state_district_subdistrict_village",
            0.99,
            ("state_norm", "district_norm", "block_norm", "village_relaxed_norm"),
        ),
        (
            "exact_state_district_village_unique",
            0.97,
            ("state_norm", "district_norm", "village_norm"),
        ),
        (
            "exact_relaxed_state_district_village_unique",
            0.96,
            ("state_norm", "district_norm", "village_relaxed_norm"),
        ),
        (
            "exact_state_village_unique",
            0.94,
            ("state_norm", "village_norm"),
        ),
        (
            "exact_relaxed_state_village_unique",
            0.93,
            ("state_norm", "village_relaxed_norm"),
        ),
    ]
    unique_maps = build_exact_unique_maps(connection)
    counts: dict[str, int] = {method: 0 for method, _score, _columns in stages}
    batch: list[tuple[object, ...]] = []
    source_columns = [
        "entity_id",
        "state_norm",
        "district_norm",
        "block_norm",
        "village_norm",
        "village_relaxed_norm",
    ]
    query = f"SELECT {', '.join(source_columns)} FROM source_entities ORDER BY entity_id"
    for row in connection.execute(query):
        source = dict(zip(source_columns, row))
        entity_id = int(source["entity_id"])
        for method, score, columns in stages:
            lookup = unique_maps[method].get(tuple(str(source[column]) for column in columns))
            if lookup is None:
                continue
            gp_id, village_code = lookup
            batch.append((entity_id, gp_id, village_code, method, score, score, 1))
            counts[method] += 1
            break
        if len(batch) >= 20000:
            connection.executemany(
                """
                INSERT OR IGNORE INTO entity_matches (
                    entity_id, gp_id, village_code, match_method, match_score,
                    match_margin, candidate_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                batch,
            )
            connection.commit()
            batch.clear()
    if batch:
        connection.executemany(
            """
            INSERT OR IGNORE INTO entity_matches (
                entity_id, gp_id, village_code, match_method, match_score,
                match_margin, candidate_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        connection.commit()
    return counts


CandidateRecord = dict[str, object]
FuzzyIndexes = dict[str, dict[tuple[str, ...], list[CandidateRecord]]]


def add_candidate_index(
    index: dict[tuple[str, ...], list[CandidateRecord]],
    key_parts: tuple[str, ...],
    candidate: CandidateRecord,
) -> None:
    if any(part == "" for part in key_parts):
        return
    index.setdefault(key_parts, []).append(candidate)


def build_fuzzy_candidate_indexes(
    connection: sqlite3.Connection,
    *,
    include_state: bool,
) -> FuzzyIndexes:
    indexes: FuzzyIndexes = {
        "parent": {},
        "district": {},
        "state": {},
    }
    rows = connection.execute(
        """
        SELECT
            gp_id, village_code, state_norm, district_norm, subdistrict_norm,
            village_name, district_name, subdistrict_name, village_relaxed_norm,
            village_prefix3, village_prefix4, village_soundex, village_consonant
        FROM gp_villages
        """
    )
    for row in rows:
        candidate: CandidateRecord = {
            "gp_id": int(row[0]),
            "village_code": int(row[1]),
            "state_norm": str(row[2]),
            "district_norm": str(row[3]),
            "subdistrict_norm": str(row[4]),
            "village_name": str(row[5]),
            "district_name": str(row[6]),
            "subdistrict_name": str(row[7]),
            "village_relaxed_norm": str(row[8]),
        }
        state_norm = str(row[2])
        district_norm = str(row[3])
        subdistrict_norm = str(row[4])
        signatures = {str(row[9]), str(row[10]), str(row[11]), str(row[12])}
        signatures.discard("")
        for signature in signatures:
            add_candidate_index(
                indexes["parent"],
                (state_norm, district_norm, subdistrict_norm, signature),
                candidate,
            )
            add_candidate_index(
                indexes["district"],
                (state_norm, district_norm, signature),
                candidate,
            )
            if include_state:
                add_candidate_index(indexes["state"], (state_norm, signature), candidate)
    return indexes


def indexed_candidates(
    indexes: FuzzyIndexes,
    source: sqlite3.Row,
    *,
    scope: str,
    limit: int,
) -> list[CandidateRecord]:
    if scope == "parent":
        prefix = (source["state_norm"], source["district_norm"], source["block_norm"])
    elif scope == "district":
        prefix = (source["state_norm"], source["district_norm"])
    elif scope == "state":
        prefix = (source["state_norm"],)
    else:
        raise ValueError(f"Unsupported candidate scope: {scope}")

    seen: set[int] = set()
    candidates: list[CandidateRecord] = []
    signatures = {
        source["village_prefix4"],
        source["village_prefix3"],
        source["village_soundex"],
        source["village_consonant"],
    }
    signatures.discard("")
    for signature in signatures:
        for candidate in indexes[scope].get((*prefix, signature), ()):
            gp_id = int(candidate["gp_id"])
            if gp_id in seen:
                continue
            seen.add(gp_id)
            candidates.append(candidate)
            if len(candidates) > limit:
                return candidates
    return candidates


def score_fuzzy_candidate(source: sqlite3.Row, candidate: CandidateRecord, *, scope: str) -> FuzzyCandidate:
    village_score = score_candidate(source["village_name"], candidate["village_name"]).score
    if source["village_relaxed_norm"] and source["village_relaxed_norm"] == candidate["village_relaxed_norm"]:
        village_score = max(village_score, 0.98)

    if scope == "parent":
        total = village_score
    elif scope == "district":
        subdistrict_score = score_candidate(source["block_name"], candidate["subdistrict_name"]).score
        total = (0.82 * village_score) + (0.18 * subdistrict_score)
    else:
        district_score = score_candidate(source["district_name"], candidate["district_name"]).score
        subdistrict_score = score_candidate(source["block_name"], candidate["subdistrict_name"]).score
        total = (0.72 * village_score) + (0.18 * district_score) + (0.10 * subdistrict_score)

    return FuzzyCandidate(
        village_code=int(candidate["village_code"]),
        village_name=str(candidate["village_name"]),
        district_name=str(candidate["district_name"]),
        subdistrict_name=str(candidate["subdistrict_name"]),
        village_score=village_score,
        score=min(1.0, total),
    )


def simple_text_score(left: str, right: str) -> float:
    left_norm = normalize_match_text(left)
    right_norm = normalize_match_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0

    left_compact = left_norm.replace(" ", "")
    right_compact = right_norm.replace(" ", "")
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    compact_sequence = SequenceMatcher(None, left_compact, right_compact).ratio()
    substring = 1.0 if left_compact in right_compact or right_compact in left_compact else 0.0
    prefix = 1.0 if left_compact[:4] == right_compact[:4] and left_compact[:4] else 0.0
    return min(
        1.0,
        (0.58 * max(sequence, compact_sequence))
        + (0.22 * substring)
        + (0.20 * prefix),
    )


def cheap_fuzzy_score(source: sqlite3.Row, candidate: sqlite3.Row, *, scope: str) -> tuple[float, float]:
    village_score = simple_text_score(source["village_name"], candidate["village_name"])
    if source["village_relaxed_norm"] and source["village_relaxed_norm"] == candidate["village_relaxed_norm"]:
        village_score = max(village_score, 0.98)
    if scope == "parent":
        return village_score, village_score
    if scope == "district":
        subdistrict_score = simple_text_score(source["block_name"], candidate["subdistrict_name"])
        return (0.82 * village_score) + (0.18 * subdistrict_score), village_score
    district_score = simple_text_score(source["district_name"], candidate["district_name"])
    subdistrict_score = simple_text_score(source["block_name"], candidate["subdistrict_name"])
    return (0.72 * village_score) + (0.18 * district_score) + (0.10 * subdistrict_score), village_score


def choose_fuzzy_match(
    source: sqlite3.Row,
    candidates: Sequence[CandidateRecord],
    *,
    scope: str,
    auto_accept_score: float,
    min_margin: float,
    min_village_score: float,
) -> tuple[CandidateRecord, FuzzyCandidate, float] | None:
    cheap_scored = []
    for candidate in candidates:
        cheap_score, cheap_village_score = cheap_fuzzy_score(source, candidate, scope=scope)
        if (
            cheap_score >= auto_accept_score - 0.10
            and cheap_village_score >= min_village_score - 0.12
        ):
            cheap_scored.append((candidate, cheap_score, cheap_village_score))
    cheap_scored.sort(key=lambda item: (-item[1], -item[2], item[0]["village_code"]))
    scored = [
        (candidate, score_fuzzy_candidate(source, candidate, scope=scope))
        for candidate, _cheap_score, _cheap_village_score in cheap_scored[:5]
    ]
    scored.sort(
        key=lambda item: (
            -item[1].score,
            -item[1].village_score,
            item[1].district_name,
            item[1].subdistrict_name,
            item[1].village_name,
            item[1].village_code,
        )
    )
    if not scored:
        return None
    margin = scored[0][1].score - scored[1][1].score if len(scored) > 1 else scored[0][1].score
    best_row, best = scored[0]
    if (
        best.score >= auto_accept_score
        and best.village_score >= min_village_score
        and margin >= min_margin
    ):
        return best_row, best, margin
    return None


def run_fuzzy_matching(
    connection: sqlite3.Connection,
    *,
    max_candidates: int,
    auto_accept_score: float,
    min_margin: float,
    min_village_score: float,
    enable_state_fuzzy: bool,
) -> dict[str, int]:
    connection.row_factory = sqlite3.Row
    counts = {"fuzzy_parent": 0, "fuzzy_district": 0, "fuzzy_state": 0}
    scopes: tuple[tuple[str, str], ...] = (
        ("parent", "fuzzy_state_district_subdistrict_village"),
        ("district", "fuzzy_state_district_village"),
    )
    if enable_state_fuzzy:
        scopes = (*scopes, ("state", "fuzzy_state_village"))
    indexes = build_fuzzy_candidate_indexes(connection, include_state=enable_state_fuzzy)
    for scope, method in scopes:
        unmatched = connection.execute(
            """
            SELECT s.*
            FROM source_entities s
            LEFT JOIN entity_matches m ON m.entity_id = s.entity_id
            WHERE m.entity_id IS NULL
            ORDER BY s.entity_id
            """
        ).fetchall()
        batch: list[tuple[object, ...]] = []
        for source in unmatched:
            candidates = indexed_candidates(indexes, source, scope=scope, limit=max_candidates)
            if not candidates or len(candidates) > max_candidates:
                continue
            choice = choose_fuzzy_match(
                source,
                candidates,
                scope=scope,
                auto_accept_score=auto_accept_score,
                min_margin=min_margin,
                min_village_score=min_village_score,
            )
            if choice is None:
                continue
            candidate_row, best, margin = choice
            batch.append(
                (
                    source["entity_id"],
                    candidate_row["gp_id"],
                    candidate_row["village_code"],
                    method,
                    best.score,
                    margin,
                    len(candidates),
                )
            )
            if len(batch) >= 5000:
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO entity_matches (
                        entity_id, gp_id, village_code, match_method, match_score,
                        match_margin, candidate_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    batch,
                )
                connection.commit()
                counts[f"fuzzy_{scope}"] += len(batch)
                batch.clear()
        if batch:
            connection.executemany(
                """
                INSERT OR IGNORE INTO entity_matches (
                    entity_id, gp_id, village_code, match_method, match_score,
                    match_margin, candidate_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                batch,
            )
            connection.commit()
            counts[f"fuzzy_{scope}"] += len(batch)
    return counts


def write_csv(connection: sqlite3.Connection, sql: str, path: Path) -> int:
    cursor = connection.execute(sql)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([description[0] for description in cursor.description])
        rows = 0
        while True:
            chunk = cursor.fetchmany(20000)
            if not chunk:
                break
            writer.writerows(chunk)
            rows += len(chunk)
    return rows


def output_select_sql(*, matched_only: bool) -> str:
    where = "WHERE m.entity_id IS NOT NULL" if matched_only else ""
    return f"""
        SELECT
            g.state_code AS lgd_state_code,
            COALESCE(g.state_name, s.state_name) AS lgd_state_name,
            g.district_code AS lgd_district_code,
            COALESCE(g.district_name, s.district_name) AS lgd_district_name,
            g.subdistrict_code AS lgd_subdistrict_code,
            COALESCE(g.subdistrict_name, s.block_name) AS lgd_subdistrict_name,
            g.village_code AS lgd_village_code,
            g.village_census2011_code AS village_census2011_code,
            COALESCE(g.village_name, s.village_name) AS lgd_village_name,
            s.state_name AS source_state_name,
            s.district_name AS source_district_name,
            s.block_name AS source_block_name,
            s.village_name AS source_village_name,
            s.cattle_male,
            s.cattle_female,
            s.cattle_male + s.cattle_female AS cattle_total,
            s.buffalo_male,
            s.buffalo_female,
            s.buffalo_male + s.buffalo_female AS buffalo_total,
            s.sheep_male,
            s.sheep_female,
            s.sheep_male + s.sheep_female AS sheep_total,
            s.goat_male,
            s.goat_female,
            s.goat_male + s.goat_female AS goat_total,
            s.pig_male,
            s.pig_female,
            s.pig_male + s.pig_female AS pig_total,
            COALESCE(m.match_method, 'unmatched') AS match_method,
            COALESCE(m.match_score, 0.0) AS match_score,
            COALESCE(m.match_margin, 0.0) AS match_margin,
            COALESCE(m.candidate_count, 0) AS match_candidate_count,
            s.source_row_male,
            s.source_row_female
        FROM source_entities s
        LEFT JOIN entity_matches m ON m.entity_id = s.entity_id
        LEFT JOIN gp_villages g ON g.gp_id = m.gp_id
        {where}
        ORDER BY s.state_name, s.district_name, s.block_name, s.village_name
    """


def write_outputs(connection: sqlite3.Connection, output_dir: Path) -> dict[str, object]:
    matched_path = output_dir / "livestock_village_lgd_aligned.csv"
    all_path = output_dir / "livestock_village_lgd_alignment_all.csv"
    unmatched_path = output_dir / "livestock_village_lgd_unmatched.csv"

    matched_rows = write_csv(connection, output_select_sql(matched_only=True), matched_path)
    all_rows = write_csv(connection, output_select_sql(matched_only=False), all_path)
    unmatched_rows = write_csv(
        connection,
        """
        SELECT
            s.state_name AS source_state_name,
            s.district_name AS source_district_name,
            s.block_name AS source_block_name,
            s.village_name AS source_village_name,
            s.cattle_male,
            s.cattle_female,
            s.buffalo_male,
            s.buffalo_female,
            s.sheep_male,
            s.sheep_female,
            s.goat_male,
            s.goat_female,
            s.pig_male,
            s.pig_female,
            s.source_row_male,
            s.source_row_female
        FROM source_entities s
        LEFT JOIN entity_matches m ON m.entity_id = s.entity_id
        WHERE m.entity_id IS NULL
        ORDER BY s.state_name, s.district_name, s.block_name, s.village_name
        """,
        unmatched_path,
    )
    return {
        "matched_csv": str(matched_path.relative_to(REPO_ROOT)),
        "all_alignment_csv": str(all_path.relative_to(REPO_ROOT)),
        "unmatched_csv": str(unmatched_path.relative_to(REPO_ROOT)),
        "matched_rows": matched_rows,
        "all_rows": all_rows,
        "unmatched_rows": unmatched_rows,
    }


def build_summary(
    connection: sqlite3.Connection,
    *,
    started_at: str,
    elapsed_seconds: float,
    gp_rows: int,
    sheet_rows: dict[str, int],
    source_entities: int,
    exact_counts: dict[str, int],
    fuzzy_counts: dict[str, int],
    outputs: dict[str, object],
    args: argparse.Namespace,
) -> dict[str, object]:
    matched_entities = int(connection.execute("SELECT COUNT(*) FROM entity_matches").fetchone()[0])
    method_counts = {
        method: count
        for method, count in connection.execute(
            """
            SELECT match_method, COUNT(*)
            FROM entity_matches
            GROUP BY match_method
            ORDER BY match_method
            """
        )
    }
    return {
        "started_at": started_at,
        "finished_at": utc_now(),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "inputs": {
            "workbook": str(args.workbook),
            "gp_mapping": str(args.gp_mapping),
        },
        "parameters": {
            "max_candidates": args.max_candidates,
            "auto_accept_score": args.auto_accept_score,
            "min_margin": args.min_margin,
            "min_village_score": args.min_village_score,
            "enable_state_fuzzy": args.enable_state_fuzzy,
            "keep_cache": args.keep_cache,
        },
        "rows": {
            "gp_mapping": gp_rows,
            "rural_sheet_rows": sheet_rows,
            "source_entities": source_entities,
            "matched_entities": matched_entities,
            "unmatched_entities": source_entities - matched_entities,
            "match_rate": round(matched_entities / source_entities, 6) if source_entities else 0.0,
        },
        "exact_stage_counts": exact_counts,
        "fuzzy_stage_counts": fuzzy_counts,
        "method_counts": method_counts,
        "outputs": outputs,
        "notes": [
            "Only rural village sheets are aligned to LGD village IDs.",
            "Urban ward sheets are excluded from the village-level output.",
            "Fuzzy scoring is applied only after indexed candidate narrowing.",
        ],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare village-level livestock data aligned to LGD village IDs.",
    )
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--gp-mapping", type=Path, default=DEFAULT_GP_MAPPING)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-db", type=Path, default=DEFAULT_CACHE_DB)
    parser.add_argument("--max-candidates", type=int, default=80)
    parser.add_argument("--auto-accept-score", type=float, default=0.88)
    parser.add_argument("--min-margin", type=float, default=0.035)
    parser.add_argument("--min-village-score", type=float, default=0.82)
    parser.add_argument(
        "--enable-state-fuzzy",
        action="store_true",
        help="Also try broad state-level fuzzy matching after safer parent and district scopes.",
    )
    parser.add_argument(
        "--keep-cache",
        action="store_true",
        help="Keep the intermediate SQLite cache after writing CSV outputs.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    started_at = utc_now()
    start = time.perf_counter()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.cache_db.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(args.cache_db) as connection:
        recreate_database(connection)
        gp_rows = load_gp_mapping(connection, args.gp_mapping)
        sheet_rows = load_livestock_workbook(connection, args.workbook)
        create_indexes(connection)
        source_entities = build_source_entities(connection)
        exact_counts = run_exact_matching(connection)
        fuzzy_counts = run_fuzzy_matching(
            connection,
            max_candidates=args.max_candidates,
            auto_accept_score=args.auto_accept_score,
            min_margin=args.min_margin,
            min_village_score=args.min_village_score,
            enable_state_fuzzy=args.enable_state_fuzzy,
        )
        outputs = write_outputs(connection, args.output_dir)
        summary = build_summary(
            connection,
            started_at=started_at,
            elapsed_seconds=time.perf_counter() - start,
            gp_rows=gp_rows,
            sheet_rows=sheet_rows,
            source_entities=source_entities,
            exact_counts=exact_counts,
            fuzzy_counts=fuzzy_counts,
            outputs=outputs,
            args=args,
        )

    summary_path = args.output_dir / "livestock_village_lgd_alignment_summary.json"
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    if not args.keep_cache:
        args.cache_db.unlink(missing_ok=True)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
