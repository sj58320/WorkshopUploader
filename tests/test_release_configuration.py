from __future__ import annotations

import unittest
from pathlib import Path


class ReleaseConfigurationTests(unittest.TestCase):
    def test_fetch_script_pins_mingit_asset_and_hash(self) -> None:
        script = Path("scripts/fetch_mingit.ps1").read_text(encoding="utf-8")

        self.assertIn("MinGit-2.55.0.3-64-bit.zip", script)
        self.assertIn(
            "f48e2d2dc74a24454adc6d8fd0ac25bf9c2386f19cfb06202b9465aaad4f9f05",
            script,
        )
        self.assertIn("MinGit.extracting", script)
        self.assertIn(
            "Expand-Archive -LiteralPath $zipPath -DestinationPath $stagingPath",
            script,
        )
        self.assertIn(
            "Move-Item -LiteralPath $stagingPath -Destination $extractPath", script)

    def test_build_requires_config_and_bundles_mingit(self) -> None:
        script = Path("scripts/build_release.ps1").read_text(encoding="utf-8")

        self.assertIn("WORKSHOP_UPLOADER_GITHUB_CLIENT_ID", script)
        self.assertIn(";mingit", script)
        self.assertIn('$githubConfigPath;.', script)
        self.assertNotIn('$githubConfigPath;github_app.json', script)
        self.assertIn("fetch_mingit.ps1", script)

    def test_vendor_cache_is_ignored(self) -> None:
        ignore = Path(".gitignore").read_text(encoding="utf-8")

        self.assertIn(".vendor/", ignore)


if __name__ == "__main__":
    unittest.main()
