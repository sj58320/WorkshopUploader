from __future__ import annotations

import unittest

from app_settings import AssetSourceMode
from asset_sync import SyncState
from gui_state import compute_action_availability


class GuiStateTests(unittest.TestCase):
    def test_local_mode_ignores_github_failure(self) -> None:
        state = compute_action_availability(
            mode=AssetSourceMode.LOCAL,
            running=False,
            local_folder_valid=True,
            sync_state=SyncState.ERROR,
            pending_exists=False,
        )

        self.assertTrue(state.vpk_enabled)
        self.assertTrue(state.steam_enabled)

    def test_github_requires_ready_state(self) -> None:
        checking = compute_action_availability(
            mode=AssetSourceMode.GITHUB,
            running=False,
            local_folder_valid=True,
            sync_state=SyncState.CHECKING,
            pending_exists=False,
        )
        ready = compute_action_availability(
            mode=AssetSourceMode.GITHUB,
            running=False,
            local_folder_valid=False,
            sync_state=SyncState.READY,
            pending_exists=False,
        )

        self.assertFalse(checking.vpk_enabled)
        self.assertTrue(ready.vpk_enabled)
        self.assertTrue(ready.steam_enabled)

    def test_pending_allows_only_push_retry(self) -> None:
        state = compute_action_availability(
            mode=AssetSourceMode.GITHUB,
            running=False,
            local_folder_valid=True,
            sync_state=SyncState.PUSH_PENDING,
            pending_exists=True,
        )

        self.assertFalse(state.vpk_enabled)
        self.assertFalse(state.steam_enabled)
        self.assertFalse(state.mode_enabled)
        self.assertTrue(state.retry_push_enabled)

    def test_running_disables_every_action(self) -> None:
        state = compute_action_availability(
            mode=AssetSourceMode.GITHUB,
            running=True,
            local_folder_valid=True,
            sync_state=SyncState.READY,
            pending_exists=False,
        )

        self.assertFalse(state.vpk_enabled)
        self.assertFalse(state.steam_enabled)
        self.assertFalse(state.mode_enabled)
        self.assertFalse(state.retry_push_enabled)


if __name__ == "__main__":
    unittest.main()
