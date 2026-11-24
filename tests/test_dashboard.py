import io
import json
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile

from dashboard import dashboard_data, make_server
from poker_analyzer import connect, import_batch, rebuild_database
from raw_store import raw_dir


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def zip_bytes(member):
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.write(EXAMPLES / member, member)
    return output.getvalue()


class DashboardTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.downloads = Path(folder.name) / "downloads"
        self.downloads.mkdir()
        self.server = make_server(Path(folder.name) / "poker.sqlite", self.downloads, port=0)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.shutdown)

    def request(self, path, body=None, **headers):
        request = Request(self.server.url + path, data=body, headers=headers)
        with urlopen(request, timeout=5) as response:
            return response.read()

    def data(self, path):
        return json.loads(self.request(path))

    def send(self, path, payload):
        return json.loads(self.request(path, json.dumps(payload).encode(),
                                       **{"Content-Type": "application/json", "X-PokerAnalyzer": "1"}))

    def test_existing_downloads_and_csv_export(self):
        boundary = "PokerAnalyzerTest"
        files = [("morning.zip", zip_bytes("table-morning.txt")),
                 ("evening.zip", zip_bytes("table-evening.txt")),
                 ("winners.csv", (EXAMPLES / "winners.csv").read_bytes())]
        parts = []
        for name, content in files:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n\r\n'.encode()
                         + content + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "X-PokerAnalyzer": "1"}
        first = json.loads(self.request("/api/import", body, **headers))["counts"]
        second = json.loads(self.request("/api/import", body, **headers))["counts"]
        self.assertEqual((first["added"], second["duplicate"]), (3, 3))
        data = self.data("/api/dashboard")
        self.assertEqual((data["hands"], data["identified"], data["appearances"]), (3, 2, 8))
        self.assertIn("Example Alice", self.request("/stats.csv").decode("utf-8-sig"))
        original = self.server.job.db_path
        archive = raw_dir(original)
        self.assertEqual(len(list((archive / "blobs").iterdir())), 3)
        recovered = original.with_name("recovered.sqlite")
        self.assertEqual(rebuild_database(recovered, archive), 2)
        with closing(connect(recovered)) as db:
            self.assertEqual(dashboard_data(db), data)

    def test_watcher_uses_only_a_new_zip_with_a_shared_hand_id(self):
        (self.downloads / "old.zip").write_bytes(zip_bytes("table-morning.txt"))
        job = self.send("/api/watch", {"records": [["VH900000001", "Example Alice"]]})
        self.assertEqual(job["status"], "waiting")
        self.assertEqual(len(list((raw_dir(self.server.job.db_path) / "blobs").glob("*.csv"))), 1)
        (self.downloads / "unrelated.zip").write_bytes(zip_bytes("table-evening.txt"))
        (self.downloads / "pending.crdownload").write_bytes(zip_bytes("table-morning.txt"))
        time.sleep(0.65)
        self.assertEqual(self.data("/api/job")["status"], "waiting")
        (self.downloads / "pending.crdownload").rename(self.downloads / "new.zip")
        for _ in range(60):
            result = self.data("/api/job")
            if result["status"] not in ("waiting", "importing"):
                break
            time.sleep(0.05)
        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(result["file"], "new.zip")
        data = self.data("/api/dashboard")
        self.assertEqual((data["hands"], data["identified"]), (2, 1))

    def test_rejects_import_requests_from_external_pages(self):
        with self.assertRaises(HTTPError) as rejected:
            self.request("/api/watch", b"{}", **{"X-PokerAnalyzer": "1", "Origin": "https://example.com"})
        self.addCleanup(rejected.exception.close)
        self.assertEqual(rejected.exception.code, 403)

    def test_bdi_endpoint_displays_an_empty_dataset(self):
        data = self.data("/api/bdi")
        self.assertEqual(data["players"], {})
        self.assertEqual(data["population"]["overall"], [{
            "label": "Overall", "bets": 0, "named_bets": 0, "players": 0, "bet_weighted": None,
            "player_weighted": None,
        }])
        self.assertIn(b'id="bdi-panel"', self.request("/"))

    def test_analysis_tracks_new_hands_and_changed_mappings(self):
        original = self.server.job.db_path
        morning = self.downloads / "morning.zip"
        morning.write_bytes(zip_bytes("table-morning.txt"))
        winners = self.downloads / "first.csv"
        winners.write_text("hand_id,winner_nickname\nHD900000001,Example Alice\n", encoding="utf-8")
        with closing(connect(original)) as db:
            import_batch(db, original, [morning, winners], session="morning")

        first = self.send("/api/analyze", {})["analysis"]
        self.assertTrue(first["initial"])
        self.assertEqual((first["new_hands"], first["new_mappings"], first["direct"], first["propagated"]),
                         (2, 1, 1, 1))
        self.assertEqual(self.data("/api/dashboard")["analysis"], first)
        repeated = self.send("/api/analyze", {})["analysis"]
        self.assertEqual((repeated["new_hands"], repeated["new_mappings"], repeated["removed_mappings"]), (0, 0, 0))

        evening = self.downloads / "evening.zip"
        evening.write_bytes(zip_bytes("table-evening.txt"))
        later_winners = self.downloads / "second.csv"
        later_winners.write_text("hand_id,winner_nickname\nHD900000001,Conflicting Name\nHD900000003,Example Casey\n",
                                 encoding="utf-8")
        with closing(connect(original)) as db:
            import_batch(db, original, [evening, later_winners])

        result = self.send("/api/analyze", {})
        summary = result["analysis"]
        self.assertEqual(result["mappings"], 1)
        self.assertEqual((summary["new_hands"], summary["new_mappings"], summary["removed_mappings"]), (1, 1, 1))
        self.assertEqual((summary["direct"], summary["propagated"], summary["conflicts"]), (1, 0, 2))


if __name__ == "__main__":
    unittest.main()
