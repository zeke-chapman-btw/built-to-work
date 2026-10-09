"""Safe, local-only collection and archival of SimU XML scorecards.

The simulator's output directory is read-only from this module's perspective.
Each stable file version is archived byte-for-byte and indexed in SQLite before
the watcher acknowledges it. The raw bytes are kept in SQLite until the atomic
archive write is confirmed, allowing recovery after an interrupted write.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_XML_BYTES = 20 * 1024 * 1024
TICKET_RE = re.compile(r"tel:([0-9]{10})\Z")


class XMLResultError(Exception):
    """An expected scorecard parsing, storage, or archive error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _text(element: ET.Element | None) -> str:
    return "" if element is None or element.text is None else element.text.strip()


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_archive_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._")
    return (cleaned or "scorecard.xml")[:180]


@dataclass(frozen=True)
class FileSignature:
    path: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def from_path(cls, path: Path, stat=None) -> "FileSignature":
        stat = stat or path.stat()
        return cls(str(path.resolve()), int(getattr(stat, "st_dev", 0)),
                   int(getattr(stat, "st_ino", 0)), int(stat.st_size),
                   int(getattr(stat, "st_mtime_ns", stat.st_mtime * 1_000_000_000)),
                   int(getattr(stat, "st_birthtime_ns",
                               getattr(stat, "st_ctime_ns", stat.st_ctime * 1_000_000_000))))

    def key(self) -> tuple:
        # Windows file IDs may exceed SQLite's signed 64-bit INTEGER range.
        return (self.path, str(self.device), str(self.inode), self.size, self.mtime_ns, self.ctime_ns)


@dataclass(frozen=True)
class ParsedScorecard:
    exercise: str | None
    exercise_status: str
    ticket: str | None
    ticket_status: str
    failure_status: str
    failure_reason: str
    header: dict[str, str]
    measurements: list[dict[str, str]]
    validation_status: str
    error: str | None = None


def identify_exercise(filename: str) -> tuple[str | None, str]:
    lowered = Path(filename).name.casefold()
    # The provided simulator filenames are the available exercise metadata.
    if "dig footings" in lowered:
        return "Dig Footings", "identified_from_filename"
    if "truck loading" in lowered:
        return "Truck Loading", "identified_from_filename"
    return None, "uncertain"


def parse_scorecard(raw_xml: bytes, filename: str) -> ParsedScorecard:
    """Parse a scorecard without resolving entities or fetching resources."""
    if len(raw_xml) > MAX_XML_BYTES:
        raise XMLResultError(f"XML exceeds the {MAX_XML_BYTES} byte safety limit")
    # stdlib ElementTree does not fetch external resources, and rejecting DTDs
    # also prevents entity-expansion payloads from being accepted.
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw_xml, flags=re.IGNORECASE):
        raise XMLResultError("DOCTYPE and entity declarations are not allowed")
    try:
        root = ET.fromstring(raw_xml)
    except (ET.ParseError, ValueError) as exc:
        raise XMLResultError(f"XML is incomplete or malformed: {exc}") from exc
    if _tag(root) != "ScoreCard":
        raise XMLResultError(f"Unexpected XML root element: {_tag(root)}")

    header_element = next((node for node in list(root) if _tag(node) == "Header"), None)
    detail_element = next((node for node in list(root) if _tag(node) == "Detail"), None)
    if header_element is None or detail_element is None:
        raise XMLResultError("ScoreCard must contain Header and Detail elements")

    header: dict[str, str] = {}
    for child in list(header_element):
        key = _tag(child)
        value = _text(child)
        if child.attrib:
            value = _safe_json({"text": value, "attributes": child.attrib})
        # Preserve repeated/unknown header data instead of silently discarding it.
        if key in header:
            prior = header[key]
            header[key] = prior + "\n" + value
        else:
            header[key] = value

    measurements: list[dict[str, str]] = []
    for field in list(detail_element):
        if _tag(field) != "Field":
            measurements.append({"_element": _tag(field), "_text": _text(field)})
            continue
        values: dict[str, str] = {}
        if field.attrib:
            values["_FieldAttributes"] = _safe_json(field.attrib)
        for child in list(field):
            key = _tag(child)
            value = _text(child)
            if child.attrib:
                value = _safe_json({"text": value, "attributes": child.attrib})
            if key in values:
                values[key] += "\n" + value
            else:
                values[key] = value
        measurements.append(values)

    root_extras = [child for child in list(root) if child is not header_element and child is not detail_element]
    if root_extras:
        header["_UnknownRootElementsXML"] = "\n".join(
            ET.tostring(child, encoding="unicode") for child in root_extras)

    login_code = header.get("LoginCode", "")
    match = TICKET_RE.fullmatch(login_code)
    ticket = match.group(1) if match else None
    ticket_status = "valid" if ticket is not None else ("missing" if not login_code else "invalid")
    exercise, exercise_status = identify_exercise(filename)
    failure_value = header.get("Failure", "").strip().casefold()
    if failure_value in {"true", "1", "yes"}:
        failure_status = "Failed"
    elif failure_value in {"false", "0", "no"}:
        failure_status = "No failure reported; completion unknown"
    else:
        failure_status = "Unknown"
    failure_reason = header.get("FailureReason", "")
    review = (ticket is None or exercise is None or failure_status == "Unknown")
    return ParsedScorecard(exercise, exercise_status, ticket, ticket_status,
                           failure_status, failure_reason, header, measurements,
                           "needs_review" if review else "parsed")


class XMLResultRepository:
    """SQLite index plus byte-preserving, atomic local XML archive."""
    def __init__(self, database_path: Path, archive_directory: Path):
        self.database_path = Path(database_path).expanduser().resolve()
        self.archive_directory = Path(archive_directory).expanduser().resolve()
        self.archive_xml_directory = self.archive_directory / "originals"
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.archive_xml_directory.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self.recover_pending_archives()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS results (
                result_id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                source_filename TEXT NOT NULL,
                source_device TEXT NOT NULL,
                source_inode TEXT NOT NULL,
                source_size INTEGER NOT NULL,
                source_mtime_ns INTEGER NOT NULL,
                source_ctime_ns INTEGER NOT NULL,
                source_created_at TEXT,
                source_modified_at TEXT,
                imported_at TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                archive_path TEXT NOT NULL,
                archive_status TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                exercise TEXT,
                exercise_status TEXT NOT NULL,
                ticket TEXT,
                ticket_status TEXT NOT NULL,
                failure_status TEXT NOT NULL,
                failure_reason TEXT NOT NULL,
                header_json TEXT NOT NULL,
                measurements_json TEXT NOT NULL,
                scoring_formula_version TEXT,
                error TEXT,
                raw_xml BLOB NOT NULL,
                UNIQUE(source_path, source_device, source_inode, source_size,
                       source_mtime_ns, source_ctime_ns, sha256)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS results_imported_idx ON results(imported_at DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS results_review_idx ON results(validation_status)")
            db.execute("CREATE TABLE IF NOT EXISTS monitor_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    @staticmethod
    def _timestamp(ns: int) -> str:
        try:
            return datetime.fromtimestamp(ns / 1_000_000_000, timezone.utc).isoformat(timespec="seconds")
        except (OSError, OverflowError, ValueError):
            return ""

    def has_signature(self, signature: FileSignature, sha256: str | None = None) -> bool:
        with self._connect() as db:
            query = """SELECT 1 FROM results WHERE source_path=? AND source_device=? AND source_inode=?
                       AND source_size=? AND source_mtime_ns=? AND source_ctime_ns=?"""
            params = list(signature.key())
            if sha256 is not None:
                query += " AND sha256=?"
                params.append(sha256)
            return db.execute(query, params).fetchone() is not None

    def import_bytes(self, signature: FileSignature, raw_xml: bytes) -> dict:
        digest = hashlib.sha256(raw_xml).hexdigest()
        if self.has_signature(signature, digest):
            existing = self.get_by_signature(signature, digest)
            return {"duplicate": True, "record": existing}
        result_id = str(uuid.uuid4())
        filename = Path(signature.path).name
        archive_path = self.archive_xml_directory / f"{result_id}_{_safe_archive_name(filename)}"
        try:
            parsed = parse_scorecard(raw_xml, filename)
            parse_error = None
        except XMLResultError as exc:
            parsed = ParsedScorecard(None, "uncertain", None, "invalid", "Unknown", "", {}, [], "invalid_xml", str(exc))
            parse_error = str(exc)
        imported_at = _now()
        archive_status = "pending"
        with self._connect() as db:
            db.execute("""INSERT INTO results (
                result_id, source_path, source_filename, source_device, source_inode,
                source_size, source_mtime_ns, source_ctime_ns, source_created_at,
                source_modified_at, imported_at, sha256, archive_path, archive_status,
                validation_status, exercise, exercise_status, ticket, ticket_status,
                failure_status, failure_reason, header_json, measurements_json,
                scoring_formula_version, error, raw_xml
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (result_id, signature.path, filename, str(signature.device), str(signature.inode),
                 signature.size, signature.mtime_ns, signature.ctime_ns,
                 self._timestamp(signature.ctime_ns), self._timestamp(signature.mtime_ns),
                 imported_at, digest, str(archive_path), archive_status,
                 parsed.validation_status, parsed.exercise, parsed.exercise_status,
                 parsed.ticket, parsed.ticket_status, parsed.failure_status,
                 parsed.failure_reason, _safe_json(parsed.header), _safe_json(parsed.measurements),
                 None, parse_error, sqlite3.Binary(raw_xml)))
        self._finish_archive(result_id, archive_path, raw_xml, digest)
        return {"duplicate": False, "record": self.get(result_id)}

    def _finish_archive(self, result_id: str, archive_path: Path, raw_xml: bytes, digest: str):
        if archive_path.exists():
            existing_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            if existing_digest != digest:
                raise XMLResultError(f"Archive collision or corruption at {archive_path}")
        else:
            self._atomic_write(archive_path, raw_xml)
        with self._connect() as db:
            db.execute("UPDATE results SET archive_status='archived', raw_xml=? WHERE result_id=?",
                       (sqlite3.Binary(b""), result_id))

    @staticmethod
    def _atomic_write(path: Path, data: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(mode="wb", prefix=f".{path.name}.", suffix=".tmp",
                                            dir=path.parent, delete=False)
        temp_path = Path(handle.name)
        try:
            with handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            if os.name != "nt":
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def recover_pending_archives(self) -> int:
        with self._connect() as db:
            rows = db.execute("SELECT result_id, archive_path, sha256, raw_xml FROM results WHERE archive_status='pending'").fetchall()
        recovered = 0
        for row in rows:
            raw = bytes(row["raw_xml"])
            self._finish_archive(row["result_id"], Path(row["archive_path"]), raw, row["sha256"])
            recovered += 1
        return recovered

    def get(self, result_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM results WHERE result_id=?", (result_id,)).fetchone()
        return self._row_dict(row) if row else None

    def get_by_signature(self, signature: FileSignature, sha256: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("""SELECT * FROM results WHERE source_path=? AND source_device=? AND source_inode=?
                AND source_size=? AND source_mtime_ns=? AND source_ctime_ns=? AND sha256=?""",
                (*signature.key(), sha256)).fetchone()
        return self._row_dict(row) if row else None

    @staticmethod
    def _row_dict(row) -> dict:
        item = dict(row)
        item["header"] = json.loads(item.pop("header_json"))
        item["measurements"] = json.loads(item.pop("measurements_json"))
        # The temporary BLOB is only retained until archive confirmation.
        item.pop("raw_xml", None)
        item["overall_score"] = "Pending Formula"
        return item

    def recent(self, limit: int = 200) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM results ORDER BY imported_at DESC LIMIT ?", (int(limit),)).fetchall()
        return [self._row_dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._connect() as db:
            row = db.execute("""SELECT COUNT(*) AS total,
                SUM(CASE WHEN archive_status='archived' THEN 1 ELSE 0 END) AS archived,
                SUM(CASE WHEN validation_status!='parsed' OR archive_status!='archived' THEN 1 ELSE 0 END) AS review
                FROM results""").fetchone()
        return {key: int(row[key] or 0) for key in ("total", "archived", "review")}

    def last_imported_at(self) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT imported_at FROM results WHERE archive_status='archived' ORDER BY imported_at DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def record_successful_scan(self, scanned_at: str):
        with self._connect() as db:
            db.execute("INSERT INTO monitor_metadata(key,value) VALUES('last_successful_scan',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (scanned_at,))

    def last_successful_scan(self) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM monitor_metadata WHERE key='last_successful_scan'").fetchone()
        return row[0] if row else None


@dataclass
class _ObservedFile:
    signature: FileSignature
    stable_scans: int
    first_seen: float
    failures: int = 0


class XMLDirectoryScanner:
    """Poll a directory, waiting for stable files and never changing originals."""
    def __init__(self, source_directory: Path, repository: XMLResultRepository, *,
                 stability_scans: int = 2, incomplete_grace_seconds: float = 8.0,
                 clock=time.monotonic, read_bytes=None):
        self.source_directory = Path(source_directory).expanduser()
        self.repository = repository
        self.stability_scans = max(2, int(stability_scans))
        self.incomplete_grace_seconds = max(0.0, float(incomplete_grace_seconds))
        self.clock = clock
        self.read_bytes = read_bytes or (lambda path: path.read_bytes())
        self._observed: dict[str, _ObservedFile] = {}
        self._lock = threading.Lock()

    def scan_once(self) -> dict:
        scanned_at = _now()
        self.repository.recover_pending_archives()
        imported, errors = [], []
        try:
            entries = list(self.source_directory.iterdir())
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
            return {"scanned_at": scanned_at, "directory_exists": False,
                    "error": f"XML source unavailable: {exc}", "files_seen": 0,
                    "imported": [], "errors": [str(exc)]}
        active_paths = set()
        for path in entries:
            if not path.is_file() or path.suffix.casefold() != ".xml":
                continue
            try:
                signature = FileSignature.from_path(path)
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            active_paths.add(signature.path)
            now = self.clock()
            observed = self._observed.get(signature.path)
            if observed is None or observed.signature != signature:
                observed = _ObservedFile(signature, 1, now)
                self._observed[signature.path] = observed
                continue
            observed.stable_scans += 1
            if observed.stable_scans < self.stability_scans:
                continue
            try:
                raw = self.read_bytes(path)
                after = FileSignature.from_path(path)
            except (PermissionError, OSError) as exc:
                observed.failures += 1
                errors.append(f"{path.name}: waiting for readable file ({exc})")
                continue
            if after != signature or len(raw) != signature.size:
                self._observed[signature.path] = _ObservedFile(after, 1, now)
                continue
            digest = hashlib.sha256(raw).hexdigest()
            if self.repository.has_signature(signature, digest):
                self._observed.pop(signature.path, None)
                continue
            try:
                parse_scorecard(raw, path.name)
            except XMLResultError as exc:
                observed.failures += 1
                if self.clock() - observed.first_seen < self.incomplete_grace_seconds:
                    continue
                # Archive permanent malformed input too; this records the failure
                # and keeps one bad file from stalling later scorecards.
            try:
                result = self.repository.import_bytes(signature, raw)
                if not result["duplicate"]:
                    imported.append(result["record"])
                self._observed.pop(signature.path, None)
            except (OSError, sqlite3.Error, XMLResultError) as exc:
                errors.append(f"{path.name}: archive/index failed ({exc})")
        for stale_path in tuple(self._observed):
            if stale_path not in active_paths:
                self._observed.pop(stale_path, None)
        self.repository.record_successful_scan(scanned_at)
        return {"scanned_at": scanned_at, "directory_exists": True, "error": None,
                "files_seen": len(active_paths), "imported": imported, "errors": errors}


class XMLMonitorWorker(threading.Thread):
    """GUI worker that continuously scans without blocking Tk's event loop."""
    def __init__(self, source_directory: Path, repository: XMLResultRepository,
                 events, *, poll_interval: float = 1.0, scanner_factory=XMLDirectoryScanner):
        super().__init__(name="BTW-XML-Monitor", daemon=False)
        self.source_directory = Path(source_directory)
        self.repository, self.events = repository, events
        self.poll_interval = max(0.25, float(poll_interval))
        self.scanner_factory = scanner_factory
        self.stop_event = threading.Event()
        self.scan_request = threading.Event()

    def stop(self):
        self.stop_event.set()
        self.scan_request.set()

    def request_scan(self):
        self.scan_request.set()

    def emit(self, kind: str, **data):
        self.events.put((kind, data))

    def run(self):
        try:
            scanner = self.scanner_factory(self.source_directory, self.repository)
            self.emit("xml_monitor_started", source=str(self.source_directory))
            while not self.stop_event.is_set():
                result = scanner.scan_once()
                self.emit("xml_scan", **result)
                for record in result["imported"]:
                    self.emit("xml_result", record=record)
                self.scan_request.clear()
                self.scan_request.wait(self.poll_interval)
        except Exception as exc:
            self.emit("xml_monitor_error", error=f"XML monitor stopped after error: {exc}")
        finally:
            self.emit("xml_monitor_stopped")
