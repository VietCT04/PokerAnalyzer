"""Run the local PokerAnalyzer dashboard and receive captures from Chrome."""

import argparse
import csv
import io
import json
import re
import sqlite3
import threading
import time
import webbrowser
from collections import Counter
from contextlib import closing
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from zipfile import BadZipFile

from hand_history import HEADER, hands_in_zip, normalize_id
from poker_analyzer import STATS_COLUMNS, connect, coverage_summary, import_csv, import_zip, statistics


PORT = 8766
ASSETS = Path(__file__).with_name("dashboard")
MAX_BODY = 64 * 1024 * 1024
WATCH_SECONDS = 180
IMPORT_ERRORS = (OSError, ValueError, sqlite3.Error, BadZipFile, csv.Error, RuntimeError)


def dashboard_data(db):
    conflicts = db.execute("""
        SELECT COUNT(*) FROM mapping_candidates c
        WHERE NOT EXISTS (SELECT 1 FROM identity_mappings m
                          WHERE (m.session_id, m.token, m.nickname) = (c.session_id, c.token, c.nickname))
    """).fetchone()[0]
    conflicts += db.execute("""
        SELECT COUNT(*) FROM (
            SELECT h.numeric_id FROM hands h WHERE h.numeric_id IN (SELECT hand_id FROM winner_observations)
            GROUP BY h.numeric_id HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    return {**coverage_summary(db), "conflicts": conflicts,
            "players": [dict(zip(STATS_COLUMNS, row)) for row in statistics(db)]}


def import_files(db_path, files):
    counts = Counter()
    with closing(connect(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        for path in files:
            if path.suffix.lower() == ".csv":
                counts["observations"] += import_csv(db, path)
            else:
                counts.update(import_zip(db, path))
    return dict(counts)


def validate_capture(data):
    rows = data.get("records") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows or len(rows) > 50000:
        raise ValueError("Expected a nonempty batch of captured hand IDs and winners.")
    records = set()
    for row in rows:
        if (not isinstance(row, list) or len(row) != 2 or
                not all(isinstance(value, str) and 0 < len(value) <= 256 for value in row)):
            raise ValueError("Invalid captured winner row.")
        hand, nickname = row
        if not nickname.strip() or re.search(r"[\x00-\x1f\x7f]", nickname):
            raise ValueError("Invalid captured nickname.")
        records.add((normalize_id(hand), nickname.strip()))
    return sorted(records)


def zip_matches(path, expected):
    for _, lines in hands_in_zip(path):
        if lines and normalize_id(HEADER.fullmatch(lines[0])[1]) in expected:
            return True
    return False


def zip_signatures(directory):
    signatures = {}
    for path in directory.iterdir():
        if path.suffix.lower() != ".zip" or path.is_symlink():
            continue
        try:
            if path.is_file():
                stat = path.stat()
                signatures[path] = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            continue
    return signatures


class ImportJob:
    def __init__(self, db_path, downloads):
        self.db_path = db_path
        self.downloads = downloads
        self.lock = threading.Lock()
        self.state = {"id": 0, "status": "idle"}

    def snapshot(self):
        with self.lock:
            return self.state.copy()

    def start(self, records, partial=False):
        with self.lock:
            if self.state["status"] in ("waiting", "importing"):
                raise ValueError("An export is already pending. Wait for it or stop waiting in the dashboard.")
            before = zip_signatures(self.downloads)
            job_id = self.state["id"] + 1
            self.state = {"id": job_id, "status": "waiting", "observations": len(records), "partial": partial}
        threading.Thread(target=self.watch, args=(job_id, records, before), daemon=True).start()
        return self.snapshot()

    def transition(self, job_id, previous, status, **details):
        with self.lock:
            if self.state["id"] != job_id or self.state["status"] != previous:
                return False
            self.state = {**self.state, "status": status, **details}
            return True

    def watch(self, job_id, records, before):
        expected = {"HD" + hand[2:] if hand.startswith("VH") else hand for hand, _ in records}
        checked = set()
        deadline = time.monotonic() + WATCH_SECONDS
        try:
            while time.monotonic() < deadline:
                state = self.snapshot()
                if state["id"] != job_id or state["status"] != "waiting":
                    return
                for path, signature in zip_signatures(self.downloads).items():
                    if before.get(path) == signature or (path, signature) in checked:
                        continue
                    try:
                        matches = zip_matches(path, expected)
                    except (OSError, BadZipFile):
                        continue  # Chrome may still be writing or renaming the file.
                    except (ValueError, RuntimeError):
                        matches = False
                    checked.add((path, signature))
                    if not matches:
                        continue
                    if not self.transition(job_id, "waiting", "importing", file=path.name):
                        return
                    with TemporaryDirectory(dir=self.db_path.parent) as folder:
                        capture = Path(folder) / "pokercraft-capture.csv"
                        with capture.open("w", encoding="utf-8", newline="") as stream:
                            writer = csv.writer(stream)
                            writer.writerow(("hand_id", "winner_nickname"))
                            writer.writerows(records)
                        counts = import_files(self.db_path, [capture, path])
                    self.transition(job_id, "importing", "complete", counts=counts)
                    return
                time.sleep(0.5)
            self.transition(job_id, "waiting", "timeout")
        except IMPORT_ERRORS as exc:
            if not self.transition(job_id, "importing", "error", message=str(exc)):
                self.transition(job_id, "waiting", "error", message=str(exc))


def uploaded_files(body, content_type, directory):
    if not content_type.startswith("multipart/form-data;"):
        raise ValueError("Select at least one ZIP or CSV file.")
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + body
    )
    files = []
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") != "files":
            continue
        name = PureWindowsPath(part.get_filename() or "").name
        if Path(name).suffix.lower() not in {".zip", ".csv"}:
            raise ValueError("Choose only ZIP and CSV files.")
        folder = directory / str(len(files))
        folder.mkdir()
        path = folder / name
        path.write_bytes(part.get_payload(decode=True))
        files.append(path)
    if not files:
        raise ValueError("Select at least one ZIP or CSV file.")
    return files


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, data, content_type, status=HTTPStatus.OK, attachment=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, value, status=HTTPStatus.OK):
        self.respond(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def trusted_host(self):
        return self.headers.get("Host") == self.server.host

    def do_GET(self):
        if not self.trusted_host():
            self.send_json({"error": "Use the local dashboard address."}, HTTPStatus.FORBIDDEN)
            return
        route = urlsplit(self.path).path
        static = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
        }
        try:
            if route in static:
                filename, mime = static[route]
                self.respond((ASSETS / filename).read_bytes(), mime + "; charset=utf-8")
            elif route == "/api/job":
                self.send_json(self.server.job.snapshot())
            elif route in ("/api/dashboard", "/stats.csv"):
                with closing(connect(self.server.job.db_path)) as db:
                    db.execute("BEGIN")
                    if route == "/api/dashboard":
                        self.send_json(dashboard_data(db))
                    else:
                        stream = io.StringIO(newline="")
                        writer = csv.writer(stream)
                        writer.writerow(STATS_COLUMNS)
                        writer.writerows(statistics(db))
                        self.respond(stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8",
                                     attachment="player-stats.csv")
            else:
                self.send_json({"error": "Page not found."}, HTTPStatus.NOT_FOUND)
        except IMPORT_ERRORS as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        origin = self.headers.get("Origin", "")
        if (not self.trusted_host() or self.headers.get("X-PokerAnalyzer") != "1" or
                (origin and origin != self.server.url and not re.fullmatch(r"chrome-extension://[a-p]{32}", origin))):
            self.send_json({"error": "Use the dashboard or the PokerCraft extension."}, HTTPStatus.FORBIDDEN)
            return
        route = urlsplit(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= MAX_BODY:
                raise ValueError("Select files totaling less than 64 MiB per import.")
            body = self.rfile.read(length)
            job = self.server.job
            if route == "/api/watch":
                capture = json.loads(body)
                records = validate_capture(capture)
                self.send_json(job.start(records, capture.get("partial") is True))
            elif route == "/api/stop":
                job_id = json.loads(body)["id"]
                job.transition(job_id, "waiting", "stopped")
                self.send_json(job.snapshot())
            elif route == "/api/import":
                with TemporaryDirectory(dir=job.db_path.parent) as folder:
                    files = uploaded_files(body, self.headers.get("Content-Type", ""), Path(folder))
                    self.send_json({"counts": import_files(job.db_path, files)})
            else:
                self.send_json({"error": "Page not found."}, HTTPStatus.NOT_FOUND)
        except (*IMPORT_ERRORS, KeyError, TypeError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def make_server(db_path, downloads, port=PORT):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    server.host = f"127.0.0.1:{server.server_port}"
    server.url = "http://" + server.host
    server.job = ImportJob(db_path, downloads)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("research-data/poker.sqlite"))
    parser.add_argument("--downloads", type=Path, default=Path.home() / "Downloads")
    parser.add_argument("--open", action="store_true", help="Open the dashboard in your browser")
    args = parser.parse_args()
    try:
        with make_server(args.db.resolve(), args.downloads.resolve()) as server:
            print(f"PokerAnalyzer dashboard: {server.url}")
            print(f"Watching for new exports in: {server.job.downloads}")
            if args.open:
                webbrowser.open(server.url)
            server.serve_forever()
    except KeyboardInterrupt:
        return
    except OSError as exc:
        parser.exit(1, f"Dashboard could not start: {exc}\n")


if __name__ == "__main__":
    main()
