from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import asset_upload
import workshop_uploader_gui


class FakeWorkshop:
    def __init__(self, created_id: int = 987654321):
        self.created_id = created_id
        self.update_calls: list[tuple] = []

    def workshop_create(self) -> int:
        return self.created_id

    def workshop_update(self, *args):
        self.update_calls.append(args)
        return args[0]


class WorkshopMetadataTests(unittest.TestCase):
    def test_cli_accepts_multiline_change_note(self) -> None:
        with patch(
            "sys.argv",
            ["asset_upload.py", "1234567890", "--change-note", "fix\nmodels"],
        ):
            arguments = asset_upload.parse_args()

        self.assertEqual(arguments.change_note, "fix\nmodels")
    def test_default_addon_id_is_blank(self) -> None:
        self.assertEqual(workshop_uploader_gui.DEFAULT_WORKSHOP_ID, "")

    def test_existing_item_blank_metadata_is_preserved(self) -> None:
        fake = FakeWorkshop()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with (
                patch.object(asset_upload, "load_workshop_module", return_value=fake),
                patch.object(asset_upload, "pack_content"),
                patch.object(asset_upload, "create_publish_data") as publish_data,
                patch.object(asset_upload, "sleep"),
            ):
                result = asset_upload.auto_update(
                    1234567890,
                    100,
                    False,
                    asset_folder=root / "assets",
                    output_folder=root / "output",
                    isolated_output=True,
                )

        self.assertEqual(fake.update_calls[0][2:4], (None, None))
        publish_data.assert_called_once_with("Workshop 1234567890", result.pack_folder)

    def test_preview_image_is_forwarded_to_workshop_update(self) -> None:
        fake = FakeWorkshop()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            preview = root / "rss_banner.png"
            preview.write_bytes(b"preview")
            with (
                patch.object(asset_upload, "load_workshop_module", return_value=fake),
                patch.object(asset_upload, "pack_content"),
                patch.object(asset_upload, "create_publish_data"),
                patch.object(asset_upload, "sleep"),
            ):
                asset_upload.auto_update(
                    1234567890,
                    100,
                    False,
                    asset_folder=root / "assets",
                    output_folder=root / "output",
                    isolated_output=True,
                    preview_path=preview,
                )

        self.assertEqual(
            Path(fake.update_calls[0][4]),
            preview.resolve(),
        )

    def test_metadata_is_trimmed_and_forwarded(self) -> None:
        fake = FakeWorkshop()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with (
                patch.object(asset_upload, "load_workshop_module", return_value=fake),
                patch.object(asset_upload, "pack_content"),
                patch.object(asset_upload, "create_publish_data") as publish_data,
                patch.object(asset_upload, "sleep"),
            ):
                result = asset_upload.auto_update(
                    1234567890,
                    100,
                    False,
                    asset_folder=root / "assets",
                    output_folder=root / "output",
                    isolated_output=True,
                    workshop_title="  Public title  ",
                    workshop_description="  Public description  ",
                )

        self.assertEqual(
            fake.update_calls[0][2:4],
            ("Public title", "Public description"),
        )
        publish_data.assert_called_once_with("Public title", result.pack_folder)

    def test_multiline_change_note_and_confirmed_id_are_returned(self) -> None:
        fake = FakeWorkshop()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with (
                patch.object(asset_upload, "load_workshop_module", return_value=fake),
                patch.object(asset_upload, "pack_content"),
                patch.object(asset_upload, "create_publish_data"),
                patch.object(asset_upload, "sleep"),
            ):
                result = asset_upload.auto_update(
                    1234567890,
                    100,
                    False,
                    asset_folder=root / "assets",
                    output_folder=root / "output",
                    isolated_output=True,
                    change_note="add models\nfix materials",
                )

        self.assertEqual(fake.update_calls[0][1], "add models\nfix materials")
        self.assertEqual(result.workshop_id, 1234567890)
        self.assertTrue(result.steam_submitted)

    def test_new_item_requires_title_before_loading_steam(self) -> None:
        load_workshop = Mock()
        with patch.object(asset_upload, "load_workshop_module", load_workshop):
            with self.assertRaisesRegex(ValueError, "제목"):
                asset_upload.auto_update(0, 100, False)
        load_workshop.assert_not_called()

    def test_new_item_uses_created_id_and_metadata(self) -> None:
        fake = FakeWorkshop(created_id=987654321)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with (
                patch.object(asset_upload, "load_workshop_module", return_value=fake),
                patch.object(asset_upload, "pack_content"),
                patch.object(asset_upload, "create_publish_data") as publish_data,
                patch.object(asset_upload, "sleep"),
            ):
                result = asset_upload.auto_update(
                    0,
                    100,
                    False,
                    asset_folder=root / "assets",
                    output_folder=root / "output",
                    isolated_output=True,
                    workshop_title="New title",
                    workshop_description="New description",
                )

        self.assertEqual(result.pack_folder.name, "Workshop_987654321")
        self.assertEqual(fake.update_calls[0][0], 987654321)
        self.assertEqual(fake.update_calls[0][2:4], ("New title", "New description"))
        publish_data.assert_called_once_with("New title", result.pack_folder)


class SettingsPersistenceTests(unittest.TestCase):
    def test_settings_round_trip(self) -> None:
        settings = {
            "workshop_id": "1234567890",
            "workshop_title": "테스트 제목",
            "workshop_description": "테스트 설명",
            "chunk_size_mb": "100",
            "asset_folder": r"D:\CS2\Assets",
            "output_folder": r"D:\CS2\Output",
            "preview_path": r"D:\CS2\rss_banner.png",
            "github_repository": "octo-org/assets",
            "github_branch": "dev",
            "github_asset_subdir": "game/assets",
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "settings.json"
            workshop_uploader_gui.save_settings(settings, path)

            self.assertEqual(workshop_uploader_gui.load_settings(path), settings)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_normal_close_saves_settings(self) -> None:
        app = object.__new__(workshop_uploader_gui.WorkshopUploaderApp)
        app.root = Mock()
        app.running = False
        values = {
            "workshop_id": "1234567890",
            "workshop_title": "Title",
            "workshop_description": "Description",
            "chunk_size_mb": "100",
            "asset_folder": r"D:\CS2\Assets",
            "output_folder": r"D:\CS2\Output",
            "preview_path": r"D:\CS2\rss_banner.png",
            "github_repository": "octo-org/assets",
            "github_branch": "dev",
            "github_asset_subdir": "game/assets",
        }
        app.workshop_id = Mock(**{"get.return_value": values["workshop_id"]})
        app.workshop_title = Mock(
            **{"get.return_value": values["workshop_title"]}
        )
        app.workshop_description = Mock(
            **{"get.return_value": values["workshop_description"]}
        )
        app.chunk_size = Mock(**{"get.return_value": values["chunk_size_mb"]})
        app.asset_folder = Mock(**{"get.return_value": values["asset_folder"]})
        app.output_folder = Mock(**{"get.return_value": values["output_folder"]})
        app.preview_path = Mock(**{"get.return_value": values["preview_path"]})
        app.github_repository = Mock(
            **{"get.return_value": values["github_repository"]}
        )
        app.github_branch = Mock(**{"get.return_value": values["github_branch"]})
        app.github_asset_subdir = Mock(
            **{"get.return_value": values["github_asset_subdir"]}
        )

        with patch.object(workshop_uploader_gui, "save_settings") as save:
            app._on_close()

        save.assert_called_once_with(values)
        app.root.destroy.assert_called_once_with()

    def test_parse_options_forwards_selected_preview_image(self) -> None:
        app = object.__new__(workshop_uploader_gui.WorkshopUploaderApp)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            assets = root / "assets"
            assets.mkdir()
            preview = root / "rss_banner.png"
            preview.write_bytes(b"preview")

            app.workshop_id = Mock(**{"get.return_value": "1234567890"})
            app.workshop_title = Mock(**{"get.return_value": "Title"})
            app.workshop_description = Mock(**{"get.return_value": "Description"})
            app.chunk_size = Mock(**{"get.return_value": "100"})
            app.asset_folder = Mock(**{"get.return_value": str(assets)})
            app.output_folder = Mock(**{"get.return_value": str(root / "output")})
            app.preview_path = Mock(**{"get.return_value": str(preview)})
            app.asset_source_mode = Mock(
                **{"get.return_value": workshop_uploader_gui.AssetSourceMode.LOCAL.value}
            )

            options = app._parse_options()

        self.assertEqual(options.preview_path, preview.resolve())

    def test_parse_options_accepts_custom_github_target(self) -> None:
        app = object.__new__(workshop_uploader_gui.WorkshopUploaderApp)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            app.workshop_id = Mock(**{"get.return_value": "1234567890"})
            app.workshop_title = Mock(**{"get.return_value": "Title"})
            app.workshop_description = Mock(**{"get.return_value": ""})
            app.chunk_size = Mock(**{"get.return_value": "100"})
            app.asset_folder = Mock(**{"get.return_value": ""})
            app.output_folder = Mock(**{"get.return_value": str(root / "output")})
            app.preview_path = Mock(**{"get.return_value": ""})
            app.asset_source_mode = Mock(
                **{"get.return_value": workshop_uploader_gui.AssetSourceMode.GITHUB.value}
            )
            app.github_repository = Mock(
                **{"get.return_value": "octo-org/assets"}
            )
            app.github_branch = Mock(**{"get.return_value": "release/v2"})
            app.github_asset_subdir = Mock(
                **{"get.return_value": "game/additional_files"}
            )

            options = app._parse_options()

        self.assertEqual(options.github_target.full_name, "octo-org/assets")
        self.assertEqual(options.github_target.branch, "release/v2")
        self.assertEqual(
            options.github_target.asset_subdir_text,
            "game/additional_files",
        )

    def test_close_while_running_does_not_save(self) -> None:
        app = object.__new__(workshop_uploader_gui.WorkshopUploaderApp)
        app.root = Mock()
        app.running = True

        with (
            patch.object(workshop_uploader_gui, "save_settings") as save,
            patch.object(workshop_uploader_gui.messagebox, "showwarning"),
        ):
            app._on_close()

        save.assert_not_called()
        app.root.destroy.assert_not_called()

    def test_invalid_settings_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "settings.json"
            path.write_text("not json", encoding="utf-8")

            self.assertEqual(workshop_uploader_gui.load_settings(path), {})

    def test_unknown_and_non_string_values_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "settings.json"
            path.write_text(
                '{"workshop_id":"123","chunk_size_mb":100,"unknown":"value"}',
                encoding="utf-8",
            )

            self.assertEqual(
                workshop_uploader_gui.load_settings(path),
                {"workshop_id": "123"},
            )


if __name__ == "__main__":
    unittest.main()
