from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from workshop_targets import WorkshopTargets, WorkshopTargetsError


class WorkshopTargetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.path = self.root / "workshop_targets.json"
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "targets": {
                        "core": {
                            "workshop_id": 0,
                            "title": "RSS ZE ASSET - Core",
                            "asset_path": "in/packs/rss-core",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_loads_profile_and_resolves_asset_folder(self) -> None:
        target = WorkshopTargets.load(self.root).get("core")

        self.assertEqual(target.workshop_id, 0)
        self.assertEqual(
            target.folder(self.root),
            self.root / "in" / "packs" / "rss-core",
        )

    def test_records_first_positive_workshop_id(self) -> None:
        manifest = WorkshopTargets.load(self.root)
        manifest.set_workshop_id("core", 1234567890)

        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["targets"]["core"]["workshop_id"], 1234567890)

    def test_refuses_to_replace_existing_workshop_id(self) -> None:
        manifest = WorkshopTargets.load(self.root)
        manifest.set_workshop_id("core", 1234567890)

        with self.assertRaises(WorkshopTargetsError):
            manifest.set_workshop_id("core", 9999999999)


if __name__ == "__main__":
    unittest.main()
