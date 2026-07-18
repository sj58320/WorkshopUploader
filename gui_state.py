from __future__ import annotations

from dataclasses import dataclass

from app_settings import AssetSourceMode
from asset_sync import SyncState


@dataclass(frozen=True)
class ActionAvailability:
    vpk_enabled: bool
    steam_enabled: bool
    mode_enabled: bool
    retry_push_enabled: bool


def compute_action_availability(
    *,
    mode: AssetSourceMode,
    running: bool,
    local_folder_valid: bool,
    sync_state: SyncState,
    pending_exists: bool,
) -> ActionAvailability:
    if running:
        return ActionAvailability(False, False, False, False)
    if pending_exists:
        return ActionAvailability(
            False,
            False,
            False,
            mode is AssetSourceMode.GITHUB,
        )
    if mode is AssetSourceMode.LOCAL:
        return ActionAvailability(
            local_folder_valid,
            local_folder_valid,
            True,
            False,
        )
    ready = sync_state is SyncState.READY
    return ActionAvailability(ready, ready, True, False)
