# WorkshopUploader GUI

별도 설치나 고정 폴더 구조 없이 EXE 하나로 CS2 에셋을 멀티청크 VPK로 만들고 Steam 창작마당에 업로드하는 Windows 프로그램입니다.

## 다운로드

[Releases](https://github.com/sj58320/WorkshopUploader/releases)에서 `WorkshopUploader-v0.1.0.exe`만 받아 실행하면 됩니다. Python, VPKEdit CLI, SteamworksPy와 필요한 DLL은 EXE 안에 포함되어 있습니다. 실제 에셋 파일은 포함되지 않으며 사용자가 폴더를 선택해야 합니다.

개인 빌드라 코드 서명이 없으므로 Windows SmartScreen 경고가 표시될 수 있습니다.

## 사용법

1. 기존 항목을 갱신하려면 `Addon ID`에 해당 창작마당 ID를 입력합니다. 새 항목을 만들려면 `0`을 입력합니다.
2. `Workshop 제목`과 `Workshop 설명`을 입력합니다.
   - 기존 항목: 비워 두면 Steam에 등록된 현재 제목·설명을 유지합니다.
   - 새 항목(ID 0): 제목은 필수이며 설명은 선택 사항입니다.
3. `에셋 폴더`에서 VPK에 넣을 파일 구조의 루트 폴더를 선택합니다.
4. `출력 폴더`를 선택합니다. 기본값을 그대로 사용해도 됩니다.
5. 먼저 `VPK만 만들기`로 결과를 확인한 다음 `Steam 창작마당 업로드`를 누릅니다.

Addon ID의 기본값은 비어 있습니다. 특정 서버의 Addon ID, 제목, 설명은 프로그램에 내장되어 있지 않습니다.

## GUI 입력값

- Addon ID: 기존 창작마당 항목 ID 또는 신규 생성용 `0`
- Workshop 제목: 최대 128자. 기존 항목에서는 공백이면 현재 값 유지
- Workshop 설명: 최대 8,000자. 기존 항목에서는 공백이면 현재 값 유지
- 청크 크기: 기본 100 MiB
- 에셋 폴더: VPK 안에 들어갈 파일 구조의 루트
- 출력 폴더: 생성된 VPK를 보관할 위치

## 설정 저장

프로그램의 닫기 버튼으로 정상 종료할 때 다음 입력값을 `%LOCALAPPDATA%\WorkshopUploader\settings.json`에 저장합니다. 다음 실행 시 이 파일을 자동으로 불러옵니다.

- Addon ID
- Workshop 제목·설명
- 청크 크기
- 에셋 폴더와 출력 폴더 경로

입력 중이나 작업 시작 시에는 저장하지 않습니다. 작업 관리자 종료, 강제 종료, 시스템 충돌 때도 새 값은 저장되지 않습니다. Steam 계정·비밀번호·로그인 토큰은 저장하지 않습니다. 초기화하려면 프로그램을 종료한 뒤 `settings.json`을 삭제하면 됩니다.

출력 폴더 아래에는 `Workshop_AddonID` 형식의 전용 하위 폴더가 생깁니다. 예를 들어 Addon ID가 `1234567890`이면 다음과 같습니다.

```text
선택한 출력 폴더\Workshop_1234567890\
    1234567890_dir.vpk
    1234567890_000.vpk
    1234567890_001.vpk
    publish_data.txt
```

Addon별 전용 폴더만 다시 만들기 때문에 선택한 출력 폴더의 다른 파일은 건드리지 않습니다. Steam에는 해당 전용 폴더 전체가 전달됩니다.

## Steam 업로드 조건

업로드할 때는 Steam 클라이언트가 실행 중이고 계정에 로그인되어 있어야 합니다. 기존 Addon ID를 갱신하려면 로그인한 계정에 해당 항목을 수정할 권한이 있어야 합니다.

Steam 콜백 결과가 `EResult = 1 (OK)`이고 응답 Addon ID가 일치할 때만 성공으로 표시합니다. 미실행·미로그인, 존재하지 않는 ID, 수정 권한 없음 등은 실패로 처리합니다.

## 멀티청크 방식

- VPK v2
- 기본 청크 크기 100 MiB
- CS2 방식의 1 MiB 단위 BLAKE3-128 archive hash 생성 및 검증
- VPK tree, hash section, whole-file MD5와 CS2 unsigned footer 생성 및 검증
- single-file 옵션을 사용하지 않음
- `AddonID_dir.vpk`와 `AddonID_000.vpk` 이후 숫자 청크 생성

Steamworks `SetItemContent`에는 Addon별 출력 폴더 전체를 전달하므로 모든 청크가 함께 업로드됩니다.

## CLI 사용

`asset_upload.py`와 `asset_update.bat`로도 동일한 업로드 기능을 사용할 수 있습니다. 기존 항목을 업데이트할 때 제목·설명 옵션을 생략하면 Steam의 현재 값을 유지합니다.

```powershell
python .\asset_upload.py 1234567890
python .\asset_upload.py 0 --title "새 애드온" --description "애드온 설명"
```

## 릴리스와 소스 코드 파일

릴리스에서 직접 사용하는 첨부 파일은 `WorkshopUploader-v0.1.0.exe` 하나입니다. GitHub가 모든 릴리스에 자동으로 붙이는 `Source code (zip)`과 `Source code (tar.gz)`는 저장소 전체의 스냅샷이라 삭제할 수 없습니다.

소스 저장소에는 현재 GUI, CLI, 빌드와 테스트에 필요한 파일만 포함합니다. 생성된 VPK, 사용자 설정, 구형 RSS 서버 자동화 스크립트와 CS2MapPacker 런타임은 포함하지 않습니다.

## 주의

선택한 에셋 폴더 아래의 파일은 `.gitkeep`과 `__pycache__`를 제외하고 재귀적으로 VPK에 포함됩니다. 비밀키, 설정 파일, 개인 파일이 들어 있는 넓은 상위 폴더를 선택하지 마세요.

## 개발자 빌드

64비트 Python 3.10 이상이 필요합니다.

```powershell
python -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -Version v0.1.0
```

결과물은 `dist/WorkshopUploader-v0.1.0.exe` 하나이며 실제 에셋이나 생성된 VPK는 포함하지 않습니다.

서드파티 구성요소와 라이선스는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 확인하세요.
