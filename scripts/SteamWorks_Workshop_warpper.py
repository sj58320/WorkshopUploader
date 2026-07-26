from enum import Enum
from time import monotonic, sleep

from localization import tr
from steamworks import EResult, EWorkshopFileType, STEAMWORKS


APP_ID = 730
CALLBACK_TIMEOUT_SECONDS = 3600.0
INVALID_WORKSHOP_IDS = {-1, 0}
DEFAULT_TAGS = ("Custom", "Map", "Cs2")

RESULT_MESSAGES = {
    EResult.FAIL.value: tr("Steam에서 알 수 없는 오류를 반환했습니다.", "Steam returned an unknown error."),
    EResult.NO_CONNECTION.value: tr("Steam에 연결할 수 없습니다. 네트워크와 Steam 클라이언트를 확인하세요.", "Could not connect to Steam. Check the network and Steam client."),
    EResult.INVALID_PARAM.value: tr("Addon ID가 존재하지 않거나 업로드 요청 값이 올바르지 않습니다.", "The Addon ID does not exist or the upload request is invalid."),
    EResult.FILE_NOT_FOUND.value: tr("해당 Addon ID를 Steam 창작마당에서 찾을 수 없습니다.", "The Addon ID was not found in Steam Workshop."),
    EResult.ACCESS_DENIED.value: tr("현재 로그인한 계정에 이 Addon을 수정할 권한이 없습니다.", "The signed-in account cannot modify this Addon."),
    EResult.TIMEOUT.value: tr("Steam 창작마당 응답 시간이 초과되었습니다.", "The Steam Workshop request timed out."),
    EResult.SERVICE_UNAVAILABLE.value: tr("Steam 창작마당 서비스를 현재 사용할 수 없습니다.", "Steam Workshop is currently unavailable."),
    EResult.NOT_LOGGED_ON.value: tr("Steam에 로그인되어 있지 않습니다. Steam 로그인 상태를 확인하세요.", "Steam is not signed in. Check the Steam login status."),
    EResult.INSUFFICIENT_PRIVILEGE.value: tr("현재 Steam 계정의 창작마당 업로드 권한이 부족합니다.", "The current Steam account does not have sufficient Workshop upload permission."),
    EResult.LIMIT_EXCEEDED.value: tr("Steam 창작마당 업로드 제한을 초과했습니다.", "The Steam Workshop upload limit was exceeded."),
    EResult.ACCOUNT_DISABLED.value: tr("현재 Steam 계정이 비활성화되어 있습니다.", "The current Steam account is disabled."),
    EResult.SERVICE_READ_ONLY.value: tr("현재 Steam 계정은 아직 창작마당에 업로드할 수 없습니다.", "The current Steam account cannot upload to Workshop yet."),
}


class ERemoteStoragePublishedFileVisibility(Enum):
    PUBLIC = 0
    FRIENDS_ONLY = 1
    PRIVATE = 2
    UNLISTED = 3  # Not defined by the bundled SteamworksPy version.


class WorkshopResultError(RuntimeError):
    def __init__(self, action: str, result_code: int):
        self.action = action
        self.result_code = result_code
        detail = RESULT_MESSAGES.get(
            result_code,
            tr(
                "처리할 수 없는 Steam 오류가 발생했습니다 (EResult {code}).",
                "An unhandled Steam error occurred (EResult {code}).",
                code=result_code,
            ),
        )
        super().__init__(
            tr(
                "Steam 창작마당 {action}에 실패했습니다.\n{detail}",
                "Steam Workshop {action} failed.\n{detail}",
                action=action,
                detail=detail,
            )
        )


sw = STEAMWORKS()
sw.initialize()
workshop = sw.Workshop

WORKSHOP_ID = -1
_WORKSHOP_ID = -1
LAST_RESULT = None
LEGAL_AGREEMENT_REQUIRED = False
HOLD = False


def _reset_callback_state() -> None:
    global WORKSHOP_ID, _WORKSHOP_ID, LAST_RESULT, LEGAL_AGREEMENT_REQUIRED, HOLD
    WORKSHOP_ID = -1
    _WORKSHOP_ID = -1
    LAST_RESULT = None
    LEGAL_AGREEMENT_REQUIRED = False
    HOLD = True


def _record_callback(label: str, item) -> None:
    global WORKSHOP_ID, _WORKSHOP_ID, LAST_RESULT, LEGAL_AGREEMENT_REQUIRED, HOLD
    LAST_RESULT = int(item.result)
    _WORKSHOP_ID = int(item.publishedFileId)
    WORKSHOP_ID = _WORKSHOP_ID
    LEGAL_AGREEMENT_REQUIRED = bool(item.userNeedsToAcceptWorkshopLegalAgreement)
    print(
        f"\n[{label}] result={LAST_RESULT} "
        f"publishedFileId={WORKSHOP_ID} "
        f"legalAgreementRequired={LEGAL_AGREEMENT_REQUIRED}"
    )
    HOLD = False


def callback_create(item) -> None:
    _record_callback("WORKSHOP_CREATE", item)


def callback_update(item) -> None:
    _record_callback("WORKSHOP_UPDATE", item)


def _require_success(action: str) -> int:
    if LAST_RESULT is None:
        raise RuntimeError(tr("Steam 창작마당 {action} 결과를 받지 못했습니다.", "No Steam Workshop result was received for {action}.", action=action))
    if LAST_RESULT != EResult.OK.value:
        raise WorkshopResultError(action, LAST_RESULT)
    if WORKSHOP_ID in INVALID_WORKSHOP_IDS:
        raise RuntimeError(
            tr("Steam 창작마당 {action}은 성공했지만 유효한 Addon ID를 받지 못했습니다.", "Steam Workshop {action} succeeded but did not return a valid Addon ID.", action=action)
        )
    if LEGAL_AGREEMENT_REQUIRED:
        print(
            tr(
                "주의: Steam Workshop 이용 약관에 동의해야 항목을 정상적으로 공개할 수 있습니다.",
                "Warning: Accept the Steam Workshop legal agreement before publishing the item.",
            )
        )
    return WORKSHOP_ID


def workshop_create() -> int:
    _reset_callback_state()
    workshop.CreateItem(APP_ID, EWorkshopFileType.COMMUNITY, callback_create, True)
    check_callbacks()
    return _require_success(tr("새 항목 생성", "item creation"))


def _require_setting(result: bool, label: str) -> None:
    if not result:
        raise RuntimeError(tr("Steam 창작마당 {label} 설정에 실패했습니다.", "Failed to set Steam Workshop {label}.", label=label))


def workshop_update(
    iid,
    note=None,
    title=None,
    desc=None,
    preview=None,
    content=None,
    tags=DEFAULT_TAGS,
) -> int:
    iid = int(iid)
    if iid <= 0:
        raise ValueError(tr("Steam 창작마당 Addon ID는 1 이상의 숫자여야 합니다.", "The Steam Workshop Addon ID must be a number greater than zero."))

    print(f"Updating Steam Workshop item '{iid}'...")
    update_handle = workshop.StartItemUpdate(APP_ID, iid)
    if title:
        _require_setting(workshop.SetItemTitle(update_handle, title), tr("제목", "title"))
    if desc:
        _require_setting(workshop.SetItemDescription(update_handle, desc), tr("설명", "description"))
    _require_setting(
        workshop.SetItemVisibility(
            update_handle, ERemoteStoragePublishedFileVisibility.UNLISTED
        ),
        tr("공개 범위", "visibility"),
    )
    if tags:
        _require_setting(workshop.SetItemTags(update_handle, tags), tr("태그", "tags"))
    if preview:
        _require_setting(
            workshop.SetItemPreview(update_handle, preview), tr("대표 이미지", "preview image")
        )
    if content:
        _require_setting(
            workshop.SetItemContent(update_handle, content), tr("콘텐츠 폴더", "content folder")
        )

    _reset_callback_state()
    print("Submitting Steam Workshop update...")
    workshop.SubmitItemUpdate(
        update_handle,
        note or "Update asset",
        callback_update,
        True,
    )
    check_callbacks()
    return _require_success(tr("업로드", "upload"))


def check_callbacks(timeout_seconds: float = CALLBACK_TIMEOUT_SECONDS) -> None:
    global HOLD
    print()
    started_at = monotonic()
    dots = 0
    while HOLD:
        if monotonic() - started_at >= timeout_seconds:
            HOLD = False
            raise TimeoutError(
                tr(
                    "Steam 창작마당 응답을 기다리는 시간이 초과되었습니다. Steam 연결 상태를 확인하세요.",
                    "Timed out waiting for Steam Workshop. Check the Steam connection.",
                )
            )
        sleep(0.3)
        sw.run_callbacks()
        dots = (dots + 1) % 4
        print("\r" + tr("Steam 응답 대기 중", "Waiting for Steam") + "." * (dots + 1), end="")
    print()
