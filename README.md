# WorkshopUploader GUI

EXE 하나로 CS2 에셋을 멀티청크 VPK로 만들고 Steam 창작마당에 업로드하는 Windows 프로그램입니다. 개인 로컬 폴더만 사용하는 모드와 `RevenantZE/RSS-ZE-ASSET`을 함께 동기화하는 GitHub 모드를 지원합니다.

## 다운로드

[Releases](https://github.com/sj58320/WorkshopUploader/releases)에서 `WorkshopUploader-v0.1.0.exe`만 받아 실행하면 됩니다. Python, VPKEdit CLI, SteamworksPy와 필요한 DLL은 EXE 안에 포함되어 있습니다. 실제 에셋 파일은 포함되지 않으며 사용자가 폴더를 선택해야 합니다.

개인 빌드라 코드 서명이 없으므로 Windows SmartScreen 경고가 표시될 수 있습니다.

## 사용법

1. 처음 실행할 때 `로컬 폴더` 또는 `GitHub 동기화`를 선택합니다. 이후 화면에서도 바꿀 수 있습니다.
2. 기존 항목을 갱신하려면 `Addon ID`에 해당 창작마당 ID를 입력합니다. 새 항목을 만들려면 `0`을 입력합니다.
3. `Workshop 제목`과 `Workshop 설명`을 입력합니다.
   - 기존 항목: 비워 두면 Steam에 등록된 현재 제목·설명을 유지합니다.
   - 새 항목(ID 0): 제목은 필수이며 설명은 선택 사항입니다.
4. 로컬 모드에서는 `에셋 폴더`를 선택합니다. GitHub 모드에서는 로그인과 최신화가 끝날 때까지 기다립니다.
5. `출력 폴더`를 선택합니다. 기본값을 그대로 사용해도 됩니다.
6. 여러 줄로 `업데이트 내역`을 적습니다. 비워 두면 `Update asset`을 사용합니다.
7. 먼저 `VPK만 만들기`로 결과를 확인한 다음 `Steam 창작마당 업로드`를 누릅니다.

Addon ID의 기본값은 비어 있습니다. 특정 서버의 Addon ID, 제목, 설명은 프로그램에 내장되어 있지 않습니다.

## GUI 입력값

- Addon ID: 기존 창작마당 항목 ID 또는 신규 생성용 `0`
- 에셋 소스: GitHub가 필요 없는 `로컬 폴더` 또는 협업용 `GitHub 동기화`
- 업데이트 내역: Steam 업데이트 노트이며 GitHub 모드에서는 같은 내용이 커밋 메시지가 됨
- Workshop 제목: 최대 128자. 기존 항목에서는 공백이면 현재 값 유지
- Workshop 설명: 최대 8,000자. 기존 항목에서는 공백이면 현재 값 유지
- 청크 크기: 기본 100 MiB
- 에셋 폴더: VPK 안에 들어갈 파일 구조의 루트
- 출력 폴더: 생성된 VPK를 보관할 위치

## 설정 저장

프로그램의 닫기 버튼으로 정상 종료할 때 다음 입력값을 `%LOCALAPPDATA%\WorkshopUploader\settings.json`에 저장합니다. 다음 실행 시 이 파일을 자동으로 불러옵니다.

- 에셋 소스 모드
- Addon ID
- Workshop 제목·설명
- 청크 크기
- 에셋 폴더와 출력 폴더 경로

업데이트 내역은 저장하지 않으며 업로드 성공 후 비웁니다. 입력 중이나 작업 시작 시에는 다른 설정도 저장하지 않습니다. 작업 관리자 종료, 강제 종료, 시스템 충돌 때는 새 값이 저장되지 않습니다.

Steam 계정·비밀번호는 저장하지 않습니다. GitHub 모드의 로그인 토큰은 평문 설정 파일이 아니라 Windows 자격 증명 관리자에 저장합니다. 초기화하려면 프로그램을 종료한 뒤 `settings.json`을 삭제하고 Windows 자격 증명 관리자에서 `WorkshopUploader:GitHub` 항목을 삭제하면 됩니다.

출력 폴더 아래에는 `Workshop_AddonID` 형식의 전용 하위 폴더가 생깁니다. 예를 들어 Addon ID가 `1234567890`이면 다음과 같습니다.

```text
선택한 출력 폴더\Workshop_1234567890\
    1234567890_dir.vpk
    1234567890_000.vpk
    1234567890_001.vpk
    publish_data.txt
```

Addon별 전용 폴더만 다시 만들기 때문에 선택한 출력 폴더의 다른 파일은 건드리지 않습니다. Steam에는 해당 전용 폴더 전체가 전달됩니다.

## GitHub 에셋 동기화

GitHub 모드는 다음 고정 저장소와 경로만 사용합니다.

```text
https://github.com/RevenantZE/RSS-ZE-ASSET
%LOCALAPPDATA%\WorkshopUploader\repos\RSS-ZE-ASSET\in\additional_files
```

처음에는 저장소를 한 번 내려받으므로 전체 에셋 용량만큼 시간이 걸립니다. 다음 실행부터는 `git fetch`와 fast-forward 방식으로 변경된 Git 객체만 내려받습니다. 원격 변경을 받는 동안 로컬 수정은 임시 보관했다가 다시 적용하며, 충돌이 나면 파일을 자동 삭제하거나 덮어쓰지 않고 작업을 잠급니다.

`Steam 창작마당 업로드`를 누르면 다음 순서로 처리합니다.

1. GitHub 로그인·권한·최신 상태 확인
2. VPK 생성 및 Steam 업로드
3. 같은 업데이트 내역으로 Git 커밋
4. `RevenantZE/RSS-ZE-ASSET`의 `main`에 push

Steam 업로드가 성공한 뒤 GitHub push만 실패하면 복구 정보가 `%LOCALAPPDATA%\WorkshopUploader\pending_upload.json`에 남습니다. 다음 실행의 `GitHub Push 재시도`는 Steam에 다시 올리지 않고 GitHub 단계만 이어서 처리합니다. 미완료 push가 있는 동안에는 소스 모드를 변경할 수 없습니다.

Steam 응답을 기록하기 직전에 프로그램이나 PC가 종료되면 `Steam 결과 확인 필요` 상태가 됩니다. 이때는 자동으로 Steam이나 GitHub에 다시 올리지 않습니다. Steam Workshop 페이지에서 마지막 업로드를 확인한 뒤 버튼을 눌러 성공 또는 실패를 선택하세요. 성공으로 확인하면 Steam 업로드 없이 GitHub 단계만 이어가며, 새 항목이었다면 생성된 Addon ID를 한 번 입력합니다. 실패로 확인하면 복구 기록을 지우고 정상 업로드를 다시 할 수 있습니다.

GitHub에 push하려면 프로그램에서 로그인한 사용자가 해당 저장소에 push 권한을 가져야 합니다. Steam 창작마당 기여자 권한과 GitHub 저장소 권한은 서로 별개입니다.

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

`asset_upload.py`와 `asset_update.bat`로도 Steam 업로드 기능을 사용할 수 있습니다. 기존 항목을 업데이트할 때 제목·설명 옵션을 생략하면 Steam의 현재 값을 유지합니다. `--change-note`에는 Steam 업데이트 내역을 전달합니다.

```powershell
python .\asset_upload.py 1234567890
python .\asset_upload.py 1234567890 --change-note "fix models`nadd materials"
python .\asset_upload.py 0 --title "새 애드온" --description "애드온 설명"
```

## 릴리스와 소스 코드 파일

릴리스에서 직접 사용하는 첨부 파일은 `WorkshopUploader-v0.1.0.exe` 하나입니다. GitHub가 모든 릴리스에 자동으로 붙이는 `Source code (zip)`과 `Source code (tar.gz)`는 저장소 전체의 스냅샷이라 삭제할 수 없습니다.

소스 저장소에는 현재 GUI, CLI, GitHub 동기화, 빌드와 테스트에 필요한 파일만 포함합니다. 생성된 VPK, 사용자 설정, 로그인 토큰, 내려받은 MinGit, 구형 RSS 서버 자동화 스크립트와 CS2MapPacker 런타임은 포함하지 않습니다.

## 주의

선택한 에셋 폴더 아래의 파일은 `.gitkeep`과 `__pycache__`를 제외하고 재귀적으로 VPK에 포함됩니다. 비밀키, 설정 파일, 개인 파일이 들어 있는 넓은 상위 폴더를 선택하지 마세요.

## 개발자 빌드

64비트 Python 3.10 이상이 필요합니다. GitHub App의 Device Flow를 켜고 `RevenantZE/RSS-ZE-ASSET`에 Contents 읽기·쓰기 권한으로 설치한 뒤 client ID를 환경 변수에 넣어야 합니다. client secret은 사용하지 않습니다.

```powershell
python -m pip install -r requirements-build.txt
$env:WORKSHOP_UPLOADER_GITHUB_CLIENT_ID = "GitHub App client ID"
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -Version v0.1.0
```

빌드 스크립트는 공식 MinGit 2.55.0.3 압축 파일을 내려받아 고정된 SHA-256을 확인하고 EXE 안에 포함합니다. 결과물은 `dist/WorkshopUploader-v0.1.0.exe` 하나이며 실제 에셋이나 생성된 VPK는 포함하지 않습니다.

서드파티 구성요소와 라이선스는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 확인하세요.
