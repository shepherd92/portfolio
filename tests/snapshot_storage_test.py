"""Snapshot destinations stay separate from code, including through symlinks."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portfolio.models import MarketSnapshot, Quote
import portfolio.storage as portfolio_io
from portfolio.storage import load_snapshot, save_snapshot


class SnapshotStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.code = self.directory / "application"
        self.code.mkdir()
        self.snapshots = self.code / "snapshots"
        self.snapshot = MarketSnapshot({"A": Quote(10, "EUR")}, {})
        self.constants = patch.multiple(portfolio_io, SCRIPT_DIRECTORY=self.code,
                                        SNAPSHOT_DIRECTORY=self.snapshots)
        self.constants.start()
        self.addCleanup(self.constants.stop)

    def test_snapshot_directory_is_created_only_on_save(self):
        self.assertFalse(self.snapshots.exists())
        path = self.snapshots / "dated" / "prices.json"
        save_snapshot(self.snapshot, path)
        self.assertEqual(load_snapshot(path), self.snapshot)

    def test_external_directory_is_allowed(self):
        path = self.directory / "my-portfolio" / "prices.json"
        save_snapshot(self.snapshot, path)
        self.assertEqual(load_snapshot(path), self.snapshot)

    def test_acquisition_timestamp_round_trips_without_inventing_missing_metadata(self):
        for fetched_at in (None, '2026-10-14T09:12:34+00:00', '2026-10-14T11:12:34+02:00',
                           '2026-10-14T09:12:34Z'):
            with self.subTest(fetched_at=fetched_at):
                snapshot = MarketSnapshot(self.snapshot.quotes, self.snapshot.rates,
                                          fetched_at=fetched_at)
                path = self.snapshots / 'prices.json'
                save_snapshot(snapshot, path)
                self.assertEqual(load_snapshot(path), snapshot)
                self.assertEqual(load_snapshot(path).fetched_at, fetched_at)

    def test_acquisition_timestamp_requires_an_explicit_timezone(self):
        for fetched_at in ('2026-10-14', '2026-10-14T09:12:34', 'yesterday', 123, False, {}):
            with self.subTest(fetched_at=fetched_at), self.assertRaisesRegex(ValueError, 'timezone-aware ISO'):
                MarketSnapshot({}, {}, fetched_at=fetched_at)

    def test_code_and_config_destinations_are_rejected_before_directory_creation(self):
        for path in (self.code / "prices.json", self.code / "config" / "prices.json",
                     self.code / "snapshots-backup" / "prices.json",
                     self.snapshots / ".." / "prices.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "Save snapshots in"):
                save_snapshot(self.snapshot, path)
        self.assertEqual(list(self.code.iterdir()), [])

    def test_existing_source_and_config_files_cannot_be_overwritten(self):
        path = self.code / "settings.json"
        original = '{"setting": "keep"}\n'
        path.write_text(original)
        with self.assertRaisesRegex(ValueError, "Save snapshots in"):
            save_snapshot(self.snapshot, path)
        self.assertEqual(path.read_text(), original)

    def test_external_link_into_code_is_rejected(self):
        link = self.directory / "portfolio-link"
        link.symlink_to(self.code, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Save snapshots in"):
            save_snapshot(self.snapshot, link / "prices.json")
        self.assertFalse((self.code / "prices.json").exists())

    def test_snapshot_file_symlink_into_code_is_rejected(self):
        self.snapshots.mkdir()
        source = self.code / "settings.json"
        source.write_text("{}\n")
        link = self.snapshots / "prices.json"
        link.symlink_to(source)
        with self.assertRaisesRegex(ValueError, "Save snapshots in"):
            save_snapshot(self.snapshot, link)
        self.assertTrue(link.is_symlink())
        self.assertEqual(source.read_text(), "{}\n")

    def test_snapshot_directory_symlink_does_not_allow_writes_into_code(self):
        config = self.code / "config"
        config.mkdir()
        self.snapshots.symlink_to(config, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Save snapshots in"):
            save_snapshot(self.snapshot, self.snapshots / "prices.json")
        self.assertEqual(list(config.iterdir()), [])

    def test_link_from_snapshot_directory_to_external_storage_is_allowed(self):
        external = self.directory / "stored-prices"
        external.mkdir()
        self.snapshots.symlink_to(external, target_is_directory=True)
        save_snapshot(self.snapshot, self.snapshots / "prices.json")
        self.assertEqual(load_snapshot(external / "prices.json"), self.snapshot)

    def test_filesystem_errors_are_reported_without_leaving_temporary_files(self):
        self.snapshots.mkdir()
        path = self.snapshots / "prices.json"
        with patch.object(portfolio_io, "_stage", side_effect=PermissionError("read only")):
            with self.assertRaisesRegex(PermissionError, "read only"):
                save_snapshot(self.snapshot, path)
        self.assertEqual(list(self.snapshots.iterdir()), [])

    def test_symlink_loop_becomes_a_readable_validation_error(self):
        link = self.directory / "loop"
        link.symlink_to(link)
        with self.assertRaisesRegex((ValueError, OSError), "resolve|loop|symbolic"):
            save_snapshot(self.snapshot, link / "prices.json")


if __name__ == "__main__":
    unittest.main()
