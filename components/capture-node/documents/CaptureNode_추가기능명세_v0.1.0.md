# CaptureNode v0.1.0 추가 기능 명세

| 항목 | 값 |
| --- | --- |
| 문서 ID | EXT-CAPTURENODE-001 |
| 버전 | 0.1.0 |
| 상태 | 구현 반영 |
| 작성일 | 2026-07-24 |
| 기준 문서 | PLAN-CAPTURENODE-001, SRS-CAPTURENODE-001, DES-CAPTURENODE-001 |

## 1. 문서 목적

이 문서는 CaptureNode v0.1.0 개발 과정에서 기존 기획서, SRS, 설계서,
테스트 케이스의 초기 범위를 확장해 구현한 기능을 기록한다.

- 기존 기준 문서는 수정하지 않고 원래 요구사항의 기준선으로 유지한다.
- 이 문서는 추가 기능의 동작, 설정 계약, API, 운영 방법과 검증 근거를 정의한다.
- 세부 구현 선택과 근거는
  `documents/developer/implementation-decisions.md`에서 관리한다.

## 2. 추가 기능 요약

| ID | 기능 | 주요 동작 |
| --- | --- | --- |
| EXT-CAM-001 | USB/UVC 카메라 | V4L2와 FFmpeg를 사용해 USB 카메라 촬영과 Preview를 제공한다. |
| EXT-CAM-002 | 카메라 탐색·교체 | Web에서 video capture 장치, 해상도와 FPS를 선택해 실행 중 연결한다. |
| EXT-CAM-003 | 카메라 파라미터 | V4L2 control을 탐색해 int, bool, menu 형식의 편집 UI를 제공한다. |
| EXT-CAM-004 | 연결 상태 감시 | 카메라 분리 시 Web은 유지하고 Running/Live 상태를 실제 연결 상태로 갱신한다. |
| EXT-SCH-001 | 매일 촬영 규칙 | 매일 한 번, 매일 시간 구간 반복, 계속 간격 규칙을 함께 제공한다. |
| EXT-SCH-002 | 시간 구간 반복 | 시작·종료 시각과 초 단위 간격을 규칙 하나로 저장한다. |
| EXT-SCH-003 | 적용값 표시 | Preview 하단 Schedule은 편집값이 아닌 서버에 반영된 값을 표시한다. |
| EXT-WEB-001 | 즉시 촬영 | Web에서 스케줄과 별개로 한 번의 촬영을 요청한다. |
| EXT-WEB-002 | 이벤트 페이지네이션 | 최근 이벤트를 제한된 메모리와 페이지 단위로 조회한다. |
| EXT-WEB-003 | Supervisor 촬영 스트림 | 요청별 카메라 파라미터로 한 번 촬영하고 검증 가능한 multipart 이미지 묶음을 반환한다. |
| EXT-STO-001 | 저장 파일 브라우저 | 설정된 Storage가 제공하는 파일을 조회, 미리보기, 다운로드한다. |
| EXT-STO-002 | SFTP Storage | 검증된 SSH host와 유한 timeout을 사용해 원격 저장·조회·목록을 제공한다. |
| EXT-STO-003 | 일 단위 저장 경로 | 파일 수 증가를 제한하도록 캡처를 UTC 날짜 디렉터리에 저장한다. |
| EXT-STO-004 | Explicit FTPS Storage | 검증된 TLS 제어·데이터 채널로 원격 저장·조회·목록을 제공한다. |
| EXT-IMG-001 | 이미지 정책 변경 | 실행 중 이미지 형식, 품질·압축 수준, 메타데이터 정책을 변경한다. |
| EXT-IMG-002 | Picamera2 RAW 묶음 | 처리 영상과 센서 원본 DNG를 같은 촬영 ID로 저장한다. |

### 2.1 기존 기준선과의 차이

| 영역 | 기존 문서 상태 | 추가 구현 |
| --- | --- | --- |
| USB 카메라 | SRS의 CAP-006에서 P4 후순위 | v0.1.0에 V4L2/FFmpeg 구현을 선반영 |
| 촬영 스케줄 | 초 단위 주기 촬영 | `captures` 배열의 매일 daily·시간 구간 interval·계속 interval 규칙 추가 |
| 실행 중 설정 | CFG-009의 촬영 주기·파라미터 | Image policy까지 변경 범위 확대 |
| 카메라 설정 | 구현별 `apply(params)` 계약 | control 탐색 UI, 장치·해상도·FPS 교체 추가 |
| 상태 표시 | Web의 일반 설정·상태 확인 | 실제 연결 health, Offline 표시, 카메라 없는 Web 기동 추가 |
| 촬영 실행 | 스케줄 기반 자동 촬영 | Web 즉시 촬영 추가 |
| 이벤트 | 현재 프로세스 이벤트 표시 | 500건 제한과 페이지 조회 추가 |
| 저장 파일 | 저장 pipeline | Web 파일 목록, 미리보기, 다운로드 추가 |
| 원격 저장 | 향후 FTP 추가 가능성 | SFTP 공개키·환경변수 비밀번호 인증과 원격 파일 조회 구현 |
| FTPS 저장 | 기존 범위에 없음 | Explicit FTPS와 인증서 검증, 보호된 데이터 채널 구현 |
| 저장 경로 | 단일 디렉터리에 저장 | File·SFTP를 `yyyyMMdd/기존 파일명` 구조로 분산 저장 |
| Web API | Web 기능 제공 | 단일 사용자 운영 화면을 위한 내부 API와 최소 오류 형식 제공 |

## 3. USB/UVC 카메라

### 3.1 카메라 입력

`camera.type`이 `v4l2`이면 다음 설정을 사용한다.

```json
{
  "camera": {
    "type": "v4l2",
    "device": "/dev/video0",
    "width": 1920,
    "height": 1080,
    "frameRate": 24,
    "startupTimeoutSeconds": 10
  }
}
```

- FFmpeg가 V4L2 장치의 MJPEG 스트림을 한 번만 연다.
- Preview와 스틸 촬영은 같은 최신 프레임 소스를 공유한다.
- 저장 이미지는 카메라 설정의 해상도를 유지한다.
- Web Preview는 컨테이너 너비 `100%`와 원본 비율로 표시한다.
- FFmpeg는 V4L2 Preview와 촬영에 필수다. `v4l2-ctl`은 장치 탐색, control
  조회·적용과 촬영 직후 control snapshot을 위한 선택 의존성이다.

### 3.2 장치 탐색과 연결

- `/api/camera/devices`는 등록된 모든 Camera 구현의 탐색 결과를 통합한다.
- Picamera2는 CSI camera 번호와 sensor model을 반환한다.
- Picamera2 main stream은 Python 배열이 RGB byte order가 되는 `BGR888`을
  사용해 PIL Preview와 저장 이미지의 적색·청색 channel이 뒤바뀌지 않게 한다.
- V4L2는 UVC driver와 video-capture capability를 모두 만족하는 실제 촬영
  `/dev/video*` 장치만 반환한다. ISP·metadata·codec node는 제외한다.
- `v4l2-ctl`이 없으면 자동 탐색 결과는 비우되 설정된 `/dev/video*`의 FFmpeg
  Preview와 촬영은 계속 제공한다.
- 운영자는 Web에서 CSI와 USB/UVC 장치를 구분 없이 선택할 수 있다.
- V4L2 설정에는 재부팅 후에도 장치를 식별할 수 있는 전체 `by-id` 경로를
  유지한다. Web 선택 목록과 적용값에는 장치명과 짧은 node 이름만 표시하고,
  전체 경로는 hover title로 제공한다.
- CSI는 Width, Height, Preview FPS와 Preview quality를 변경할 수 있다.
- Picamera2의 Width와 Height를 둘 다 생략하면 sensor resolution을 조회해
  처리 이미지와 Preview main stream을 최대 센서 해상도로 구성한다.
  하나만 생략하는 설정은 거절한다.
- USB/UVC는 Width, Height와 FPS를 변경할 수 있다.
- 입력값이 현재 적용값과 다를 때만 `Connect camera` 버튼이 활성화된다.
- 연결 성공 후 버튼은 `Connected camera`로 바뀌고 비활성화된다.
- 새 장치 연결이 실패하면 이전 카메라 구성을 복구한다.

### 3.3 카메라 파라미터

각 Picamera2와 V4L2 `camera` resource는 자유 형식 `calibration`과
`params` object를 가진다. Calibration은 해당 sensor와 lens 조합에 귀속되며
CaptureNode는 내용을 검증하거나 이미지 보정에 적용하지 않고 보존한다.
Calibration metadata나 params만 변경할 때는 실행 중인 카메라를 재연결하지 않는다.

- `/api/camera/controls`는 `v4l2-ctl --list-ctrls-menus` 결과를 구조화해 반환한다.
- Picamera2는 `horizontal_flip`, `vertical_flip` boolean parameter를 제공한다.
  변경 시 main stream Transform을 재구성하며 설정 파일에 영구 저장한다.
- 지원 UI는 정수 slider·number, boolean checkbox, menu select다.
- inactive control은 편집할 수 없다.
- `Camera parameters`는 기본적으로 접혀 있으며 우측 아이콘으로 펼친다.
- 변경한 control만 기존 `camera.params`와 병합해 `Apply changes`로 저장한다.
- 좌우·상하 반전은 각각 `horizontal_flip`, `vertical_flip` boolean으로 제공하고
  별도의 `mirror` 별칭은 제공하지 않는다. 같은 이름의 V4L2 hardware control이
  있으면 장치에 적용하고, 없는 축만 software fallback을 사용한다.
- software fallback은 저장 이미지에는 PIL transpose, Web Preview에는 CSS
  transform을 적용한다. Preview CSS는 control 응답의 `application`이
  `software`인 축에만 적용해 hardware 반전을 중복 적용하지 않는다. 원본 MJPEG
  Preview API stream은 재인코딩하지 않는다.
- Web은 software fallback control 이름 뒤에 `(Software)`를 표시하고 hardware
  control 이름에는 별도 접미사를 붙이지 않는다.
- Web은 초기 진입 시 control을 한 번 조회하고, 이후 성공한 촬영이
  감지될 때마다 실제 적용값과 inactive 상태를 다시 조회한다. 이
  조회는 control을 변경하지 않으며, 저장하지 않은 편집값을 덮어쓰지
  않는다.
- `v4l2-ctl`이 없으면 `/api/camera/controls`는 500 오류 대신
  `v4l2_ctl_unavailable` 경고와 software 좌우·상하 반전 control을 반환한다.
  Web은 경고를 한 번 표시하고 두 반전만 편집할 수 있게 유지한다.
- 누락 경고는 프로세스당 한 번만 기록한다. 빈 params와 params가 바뀌지 않은
  스케줄·Image policy 변경, software 반전은 계속 허용하지만 그 외 실제 device
  control 변경은 거절한다.

각 V4L2 촬영은 최신 프레임을 가져온 직후 전체 control 값을 조회한다.

- `observedControls`: 촬영 직후 V4L2 driver가 보고한 gain, exposure, white
  balance, focus 등의 hardware 조회값. software control은 포함하지 않는다.
- `controlSnapshotAt`: control 조회 시각
- `controlSnapshotError`: 조회 실패 원인
- `horizontal_flip`, `vertical_flip`: 요청되어 최종 적용된 반전 설정
- `flipApplication`: 각 반전 축의 `hardware` 또는 `software` 적용 경로

조회는 촬영 프레임에 내장된 동기화 metadata가 아니라 촬영 직후의 장치 상태
snapshot이다. 따라서 프레임과 수십 ms 차이가 날 수 있다. 조회 실패는 이미지
촬영과 저장을 중단시키지 않으며 오류 정보만 metadata에 남긴다.

### 3.4 연결 상태와 분리 처리

`GET /api/health` 응답:

```json
{
  "status": "running",
  "cameraConnected": false,
  "cameraDetail": "V4L2 device does not exist: /dev/video0"
}
```

- Web은 1.5초마다 상태를 갱신한다.
- 연결 상태이면 상단에 `Running`, Preview에 `Live`를 표시한다.
- 분리 상태이면 상단에 `Camera disconnected`, Preview에 `Offline`을 표시한다.
- 카메라가 없어도 Web 서비스, 설정, 저장 파일 조회는 계속 동작한다.
- 분리 상태에서는 Preview API가 `503`, 즉시 촬영 API가 `409`를 반환한다.
- 장치 노드가 사라진 뒤 마지막 프레임을 새 촬영 결과로 재사용하지 않는다.
- 카메라를 다시 연결한 후 장치 목록을 갱신하고 `Connect camera`로 복구한다.

## 4. 촬영 스케줄

### 4.1 규칙 계약

`captures`는 0개 이상의 discriminated Capture resource 배열이다.

```json
{
  "captures": [
    { "type": "daily", "time": "12:00:00" },
    {
      "type": "interval",
      "startTime": "12:00:00",
      "endTime": "23:00:00",
      "interval": 60
    },
    { "type": "interval", "interval": 60 }
  ]
}
```

- `daily`는 `time`에 매일 한 번 촬영한다. 특정 날짜에 한 번 촬영하는
  의미가 아니며 date 속성은 제공하지 않는다.
- 시간 구간 `interval`은 `startTime`부터 `endTime`까지 매일 `interval`
  초 간격으로 촬영한다. 시작은 포함하고, 간격에 정확히 맞는 종료도
  포함한다.
- 시간 구간이 없는 `interval`은 실행 시점부터 monotonic clock 기준으로
  계속 반복한다.
- `startTime`과 `endTime`은 함께 지정하고, `endTime`은 `startTime`보다
  빠를 수 없다. 자정을 넘는 구간은 지원하지 않는다.
- 매일 wall-clock 시각은 OS local time으로 해석하며 timezone 구성은
  설치자 책임이다.
- 여러 규칙이 같은 시각에 만나면 실제 촬영은 한 번으로 합친다.
- 같은 규칙을 중복 저장할 수 없다.
- `captures: []`는 Preview와 즉시 촬영만 유지한다.
- 기존 단일 `capture` resource와 mode 구조는 자동 변환하지 않고 strict
  검증에서 거절한다.

### 4.2 Web 편집

- 규칙을 추가·삭제하고 `daily` 시각, `interval` 초, 선택적 일일 시간 구간을
  직접 편집한다.
- 시간 구간 checkbox를 끄면 `startTime`과 `endTime`을 모두 제거해 계속
  interval resource로 저장한다.
- 모든 규칙을 삭제하고 `Apply changes`하면 `captures: []`로 저장한다.

### 4.3 적용값 표시

- Preview 하단 Schedule은 폼의 미적용 입력값이 아니라 `/api/config`의 적용값을
  표시한다.
- Daily 예: `Daily · 12:00`
- 계속 Interval 예: `Every 2m`
- 시간 구간 Interval 예: `09:00-18:00 · Every 10m`
- 다중 규칙은 `/`로 구분한다.
- 자동 촬영 중지 예: `Preview only`

## 5. Web 운영 기능

### 5.1 즉시 촬영

- `POST /api/capture`는 스케줄과 별개로 촬영과 인코딩을 동기 실행한다.
- 인코딩된 이미지가 백그라운드 저장 작업에 등록되면 저장 완료를 기다리지 않고
  `captureId`와 `capturedAt`을 반환한다.
- API 성공은 저장 완료가 아니라 촬영, 인코딩, 저장 작업 등록의 성공을 뜻한다.
- 저장 완료나 폐기는 기존 terminal 이벤트로 비동기 반영한다.
- 저장이 종결되기 전까지 해당 수집은 진행 중이며 다음 즉시 촬영은 `409`, 다음
  스케줄 촬영은 생략 이벤트로 처리한다.
- 이미 수집이 진행 중이면 `409`를 반환한다.
- 카메라가 분리된 상태에서도 `409`를 반환한다.
- `captures: []`에서도 카메라가 연결되어 있으면 즉시 촬영을 사용할 수 있다.

### 5.2 Recent activity

- 현재 프로세스의 최근 이벤트만 메모리에 보관한다.
- 최대 보관량은 500건이다.
- `/api/events`는 `page`, `pageSize`를 받으며 페이지 크기는 최대 50건이다.
- Web 기본 페이지 크기는 10건이다.
- 각 행은 이벤트 종류, `captureId` 또는 실패 원인, 발생 시각을 표시한다.

`Storage completed` 행의 의미:

- 이미지가 설정된 모든 저장 대상에 정상 저장됐다.
- 32자리 hex 문자열은 해당 촬영의 `captureId`다.
- 표시 시각은 브라우저 현지 시각의 이벤트 발생 시각이다.

### 5.3 저장 파일 브라우저

- `Storage`는 필수 쓰기와 파일 읽기·목록 조회·삭제 계약을 제공한다.
- 미지원 작업은 `io.UnsupportedOperation`, 권한 부족은 `PermissionError`,
  대상 누락은 `FileNotFoundError`로 표현한다. 원격 protocol이 권한과
  누락을 구분하지 못하면 adapter의 protocol 예외를 전달할 수 있다.
- `/api/storage/targets`는 `StorageProvider`에 등록된 대상을 이름으로 제공한다.
- `/api/storage/files`는 최신 파일부터 페이지 단위로 반환한다.
- Web 기본 페이지 크기는 12개다.
- 이미지 파일은 thumbnail을 표시하고 원본 열기와 다운로드를 제공한다.
- Web은 Storage 설정 타입이나 로컬 디렉터리를 해석하지 않고 해당 구현의
  `list`와 `read`를 호출한다.
- File·SFTP Storage는 날짜 디렉터리의 파일과 기존 최상위 파일을 함께 조회한다.
- 목록 조회는 전체 개수를 계산하되 요청한 페이지 끝까지의 최신 항목만
  메모리에 유지한다.
- 알 수 없는 target, 경로 이탈, 외부를 가리키는 symlink는 거절한다.
- 원격 OS 파일관리자나 임의 filesystem 탐색 기능은 제공하지 않는다.

### 5.4 Web 범위와 오류

- Web은 카메라 제어와 상태 확인을 함께 제공하는 단일 사용자 운영 화면이다.
- React entry point인 `main.jsx`는 root render와 공통 stylesheet import만 담당한다.
  화면 조립은 `App.jsx`, HTTP 호출은 `api/`, 상태와 workflow는 `hooks/`,
  표시 변환은 `lib/`, 운영 화면 영역은 `components/`로 분리한다.
- 단일 화면이므로 router, context 계층과 전역 store는 추가하지 않는다.
  Health, 설정·카메라, 이벤트, 수동 촬영, Storage browser를 독립 hook으로
  나누고 `useCaptureNode`는 결과를 화면 model로 조합한다.
- Web API는 이 화면을 위한 내부 인터페이스이며 외부 사용이나 독립적인 하위
  호환성을 보장하지 않는다.
- 인증·인가와 API versioning은 제공하지 않는다.
- 일반 HTTP 오류는 `application/problem+json`으로 `type`, `title`, `status`,
  `detail`, `code`를 반환한다.
- 이벤트의 실패 원인은 `code`, `message`와 필요한 경우 `target`만 노출한다.

### 5.5 SFTP Storage

SFTP는 기존 `Storage.save`, `read`, `list` 계약을 구현하므로 CaptureNode,
Distributor와 Web은 원격 저장소 타입을 판별하지 않는다.

```json
{
  "type": "sftp",
  "name": "archive",
  "host": "storage.example.local",
  "port": 22,
  "username": "capture-node",
  "remoteDirectory": "/captures",
  "passwordEnv": "CAPTURE_NODE_SFTP_PASSWORD",
  "knownHosts": "/home/huintech/.ssh/known_hosts",
  "connectTimeoutSeconds": 10,
  "operationTimeoutSeconds": 30,
  "layout": "daily"
}
```

- `remoteDirectory`는 정규화된 절대 POSIX 경로여야 하며 서버에 미리 존재해야
  한다.
- 각 저장·조회·목록 작업은 독립된 SSH/SFTP 연결을 열고 작업 후 명시적으로
  닫는다.
- connect, SSH banner, 인증, channel open과 SFTP read/write에 유한 timeout을
  적용한다.
- 서버 host key는 `knownHosts`와 일치해야 하며 알 수 없는 host를 자동
  등록하지 않는다.
- 업로드는 같은 원격 디렉터리의 임시 파일에 먼저 기록하고 POSIX rename을
  우선 사용한다. 서버가 확장을 지원하지 않으면 표준 SFTP rename으로 대체한다.
- 업로드 실패 시 임시 파일 삭제를 시도하며 자동 재시도는 하지 않는다.
- 목록과 읽기는 `yyyyMMdd` 날짜 디렉터리의 일반 파일과 기존 최상위 일반 파일을
  제공하고 symlink와 경로 이탈을 거부한다.
- `privateKey` 또는 `passwordEnv` 중 하나 이상이 필요하다. 암호화된 개인키의
  passphrase는 `privateKeyPassphraseEnv`로 참조할 수 있다.
- JSON은 Web의 `/api/config`에서 조회되므로 password와 passphrase 원문을
  허용하지 않는다.
- 모든 instance가 공유하는 secret은 systemd가 먼저 읽는 `.env.local`에
  저장한다. 특정 instance의 `instances/<instance>.env`에 같은 변수를 지정하면
  나중에 읽은 instance 값이 우선한다.
- `.env.local`과 instance `.env`는 모두 권한을 `0600`으로 제한하고 배포 시
  기존 내용을 보존한다.

### 5.6 일 단위 Storage 경로

- File과 SFTP의 `layout` 기본값은 `daily`다. `flat`을 명시하면 기존 평면
  저장 방식을 사용할 수 있다.
- CaptureNode 파일명의 UTC `yyyyMMdd` 접두어를 검증한 뒤
  `yyyyMMdd/기존 파일명`에 저장한다. 논리 파일명과 Web API 응답은 바꾸지
  않는다.
- 캡처 명명 규칙을 따르지 않는 일반 파일명은 최상위에 저장한다.
- `daily` 전환 전에 생성된 최상위 파일도 읽기와 목록 조회에서 계속 제공한다.
- 설정된 Storage root가 없으면 첫 저장 시 루트부터 재귀적으로 생성하고 생성된
  경로마다 INFO 로그를 남긴다. 날짜 하위 디렉터리도 저장 시 자동 생성하지만
  반복적인 운영 로그는 남기지 않는다.

### 5.7 Explicit FTPS Storage

FTPS는 SSH 기반 SFTP와 다른 Storage type인 `ftps`로 등록한다.

```json
{
  "type": "ftps",
  "name": "ftps-archive",
  "host": "localhost",
  "port": 21,
  "username": "capture-node",
  "remoteDirectory": "/captures",
  "passwordEnv": "CAPTURE_NODE_FTPS_PASSWORD",
  "verifyTls": true,
  "caFile": "/home/huintech/.config/capture-node/ftps-ca.pem",
  "connectTimeoutSeconds": 10,
  "operationTimeoutSeconds": 30,
  "layout": "daily"
}
```

- port 21의 explicit FTPS만 지원하며 implicit FTPS(port 990)는 지원하지 않는다.
- TLS 인증서는 기본적으로 OS trust store 또는 `caFile`로 검증한다.
- 노트북 서버와 Capture Node만 연결된 물리적으로 격리된 전용 유선망에서는
  `verifyTls: false`를 명시할 수 있다. 이때 `caFile` 값은 보존하되 사용하지
  않으므로 설정 하나만 바꿔 검증을 복구할 수 있다. 서버 신원 검증만 생략하며
  TLS와 `PROT P`는 유지한다. 공유 LAN과 외부망에서는 사용하지 않는다.
- 로그인 후 `PROT P`를 적용해 목록, 업로드와 다운로드 data channel도
  암호화한다.
- 각 작업은 독립 연결을 사용하고 connect와 operation timeout을 구분한다.
- 업로드는 같은 디렉터리의 임시 파일을 완성한 뒤 FTP rename으로 확정하며 실패
  시 임시 파일 삭제를 시도한다. rename 전에 원격 `SIZE`가 전송 크기와 같은지
  검증한다.
- 목록은 `MLSD`의 `type`, `size`, `modify` fact를 우선 사용한다. 서버가
  `MLSD`를 지원하지 않으면 `NLST`, `SIZE`, `MDTM` 표준 명령으로 대체하며
  서버별 `LIST` 문자열은 해석하지 않는다.
- 비밀번호 원문은 JSON에 저장하지 않고 `passwordEnv`가 가리키는 환경변수에서
  읽는다.
- 일부 FTPS 서버가 data channel의 TLS `close_notify` 응답을 생략하는
  경우 종료 대기를 1초로 제한하고 FTP 완료 응답과 파일 크기로 전송을 확정한다.

### 5.8 Storage 보존 정책

- File, SFTP, FTPS Storage resource는 선택적인 `maintenance` object를 직접
  소유한다. 이 object의 `intervalSeconds`는 점검 주기, `maxAgeDays`는 해당
  Storage 파일의 보존 기간이다.
- `maintenance`가 없으면 해당 Storage에는 자동 삭제 정책을 적용하지 않는다.
- 보존 기간 판단과 실행 주기는 Storage adapter가 아닌 별도
  `StorageMaintenance`와 `MaintenanceScheduler`가 소유한다.
- Maintenance가 설정된 Storage마다 Scheduler를 독립적으로 생성하며 시작 즉시
  한 번 실행하고 그 Storage의 설정 주기마다 반복한다.
- `modifiedAt < now - maxAgeDays`를 만족하는 파일만 삭제하고 경계
  시각과 같은 파일은 유지한다.
- 목록은 50개 페이지 단위로 오래된 항목부터 처리하며 한 파일의
  삭제 실패가 다른 파일이나 Storage 처리를 중단하지 않는다.
- 기본 정책은 없다. Supervisor 운영 예제에는 Storage와 보존 정책을 구성하지
  않으며 기존 Storage profile에서만 필요에 따라 명시한다.
- PNG와 DNG의 실제 크기는 센서와 촬영 내용에 따라 달라지므로 용량 상한을
  보장하지 않는다.

### 5.9 Supervisor 촬영 스트림

- `POST /api/captures`는 Supervisor가 지정한 `requestId`, 선택적인 요청별
  `parameters`와 `formats`를 받아 PNG, JPEG, DNG 산출물을 `multipart/mixed`로
  스트리밍한다. 최소 요청은 `{"requestId":"supervisor-run-id"}`다.
- `parameters`를 생략하면 임시 camera control을 적용하지 않고 현재 장치 설정으로
  촬영한다. 지정하면 현재 `camera.params`에 요청 값을 병합해 해당 센서 촬영에만
  적용하고, 촬영 직후 설정값을 복구한다.
- `formats`를 생략하거나 `null`로 보내면 현재 `config.json.images` 정책을
  사용한다. 지정하면 그 object에 포함된 형식만 생성하며, 각 형식은 설정과 같은
  `quality`, `compression`, `embedMetadata` 정책을 독립적으로 지정할 수 있다.
  예를 들어 `{"formats":{"jpeg":{"quality":85,"embedMetadata":true}}}`는
  기본 설정에 DNG가 있어도 JPEG만 생성한다. 요청 정책은 영속 설정을 변경하지
  않는다.
- 촬영 예약 직후 전체 Options를 한 번 snapshot하고, 요청별 parameter의 기준값,
  요청별 format, DNG 요청 여부와 모든 artifact 인코딩에 같은 snapshot을 사용한다.
  촬영 도중 `/api/config`가 변경돼도 진행 중 bundle에는 새 Image policy를 섞지
  않는다.
- 처리 산출물 순서는 설정 문서의 속성 순서와 무관하게 PNG, JPEG, DNG로 고정한다.
- 응답은 `X-Request-Id`, `X-Capture-Id`, `X-Captured-At`,
  `X-Artifact-Count`를 제공한다. 각 part는 `Content-Type`, filename을 포함한
  `Content-Disposition`, `Content-Length`, base64 SHA-256 `Digest`,
  `X-Capture-Id`를 제공한다.
- multipart body는 별도 ZIP이나 전체 응답 byte sequence로 합치지 않고 header와
  artifact bytes를 part 단위로 yield한다.
- capture reservation은 모든 인코딩이 끝난 뒤에도 유지하며 multipart 종료
  boundary를 보내거나 client가 응답 소비를 취소할 때 해제한다. 그 전의 다음
  촬영 요청은 `409 capture_in_progress`로 거절한다.
- client 연결이 끊어진 artifact를 CaptureNode가 spool하거나 재전송하지 않는다.
  Supervisor는 부분 수신을 실패로 처리하고 필요하면 새 촬영을 요청한다.
- 오류 코드는 `camera_unavailable`, `capture_in_progress`,
  `invalid_capture_parameters`, `capture_failed`, `encoding_failed`로 구분한다.
- Supervisor 운영 profile은 예약 촬영과 Storage를 구성하지 않는다. 빈
  `captures`와 `storages`는 설정 파일에 속성 자체를 기록하지 않으며 Options
  모델에서는 각각 빈 tuple 기본값으로 해석한다. 기존 Scheduler와 Storage
  구현 계약은 다른 profile의 호환성을 위해 유지한다.

## 6. Image policy

### 6.1 실행 중 변경

Web의 `Apply changes`로 다음 값을 변경한다.

- JPEG: `images.jpeg.quality: 1-100`, metadata 포함 여부
- PNG: `images.png.compression: 0-9`, metadata 포함 여부
- DNG: `images.dng.embedMetadata`, 사용자 정의 metadata 포함 정책
- `png`, `jpeg`, `dng` 키의 존재 여부로 해당 출력을 활성화한다.
- 최소 한 형식이 필요하며 PNG, JPEG, DNG를 동시에 활성화할 수 있다.
- `images`를 생략하면 PNG Compression 7과 metadata 포함을 모델 기본값으로
  사용한다.

변경은 설정 파일에 저장되며 다음 촬영부터 적용된다. 기존 저장 파일은 변경하지
않는다.

같은 촬영에서 생성된 모든 출력은 동일한 UTC timestamp와 capture ID의 stem을
공유하며 `.png`, `.jpeg`, `.dng` 확장자로만 구분한다. 형식별 별칭이나 별도
suffix는 제공하지 않는다.

`Embed metadata`가 활성화되면 JPEG와 PNG 모두 EXIF에 촬영 시각, 카메라
파라미터, 노출과 gain을 기록한다. PNG metadata는 `eXIf` chunk만 사용하며 PNG
text chunk는 기록하지 않는다. 비활성화하면 EXIF를 생략한다.

표준 EXIF tag에 직접 대응하지 않는 구현별 카메라 파라미터는 `UserComment`에
JSON으로 기록한다. 따라서 전체 파라미터를 보존할 수 있지만, 일반 이미지
뷰어에서는 `UserComment`의 JSON이 표시되지 않을 수 있다. V4L2 촬영의
`observedControls`와 snapshot 시각·오류, 반전 설정과 `flipApplication`도 이
JSON에 포함한다. software 반전값은 device snapshot인 `observedControls`에
병합하지 않는다.

### 6.2 JPEG 표시

- UI 이름: `Quality`
- 범위: `1-100`
- 값이 높을수록 손실이 적고 일반적으로 파일이 커진다.
- 하단 요약 예: `JPEG · Q90 · Metadata on`

### 6.3 PNG 표시

- UI 이름: `Compression`
- 범위: `0-9`
- 값이 높을수록 압축이 강해져 저장 시간이 늘고 파일 크기가 작아질 수 있다.
- PNG는 무손실이므로 압축 수준에 따른 픽셀 화질 변화는 없다.
- 하단 요약 예: `PNG · Compression 7 · Metadata on / DNG · Metadata on`

### 6.4 Picamera2 DNG RAW

- `images.dng` resource가 존재하면 활성화한다.
- Picamera2만 지원하며 V4L2 설정에 RAW를 활성화하면 설정 검증에서 거절한다.
- 촬영 순간에 전체 센서 해상도의 가장 높은 지원 비트 깊이로 still mode를
  구성한다. 배포된 IMX708과 IMX219는 모두 10-bit이므로 12-bit로 변환하거나
  표기하지 않는다.
- 같은 센서 요청에서 처리 영상과 DNG를 생성하고 동일 UTC 시각과 capture ID의
  파일명 stem을 사용한다.
- 두 산출물을 모든 Storage에 한 작업으로 전달하며 두 파일의 저장 시도가 모두
  종결되기 전에는 다음 촬영을 수락하지 않는다.
- DNG는 Picamera2가 기록하는 센서·노출 metadata를 포함한다. PNG/JPEG에는 기존
  EXIF와 `UserComment` metadata를 유지하고 capture ID도 포함한다.
- `images.dng.embedMetadata`는 향후 `camera.calibration` 같은 CaptureNode 사용자 정의
  metadata를 DNG에 주입할지 결정하는 정책이다. 현재는 Picamera2가 생성한 native
  DNG metadata를 항상 보존하며 사용자 정의 metadata 주입은 구현하지 않는다.
- RAW 촬영 뒤 preview configuration을 복구해 Web MJPEG를 계속 제공한다.

## 7. API 추가·변경 목록

| Method | Path | 목적 |
| --- | --- | --- |
| `GET` | `/api/health` | Web runtime과 실제 카메라 연결 상태 조회 |
| `GET` | `/api/camera/devices` | 연결 가능한 V4L2 capture 장치 조회 |
| `GET` | `/api/camera/controls` | 현재 장치의 control과 범위 조회 |
| `PUT` | `/api/camera` | 장치, 해상도, FPS를 적용하고 카메라 교체 |
| `POST` | `/api/capture` | 즉시 촬영 |
| `POST` | `/api/captures` | Supervisor 요청별 multipart 촬영 bundle 스트리밍 |
| `GET` | `/api/events` | 최근 이벤트 페이지 조회 |
| `GET` | `/api/storage/targets` | 등록된 Storage 목록 |
| `GET` | `/api/storage/files` | 저장 파일 페이지 조회 |
| `GET` | `/api/storage/files/{target}/{filename}` | 파일 열기·다운로드 |
| `PUT` | `/api/config` | 스케줄, 카메라 파라미터, Image policy 변경 |

## 8. 운영 배포 기준

현재 Raspberry Pi 운영 배포는 다음 경로를 사용한다.

```text
Application: /home/huintech/capture-node
Camera 0 configuration: /home/huintech/capture-node/config.camera0.json
Camera 1 configuration: /home/huintech/capture-node/config.camera1.json
Camera 0 captures: /home/huintech/capture-node/captures/camera0
Camera 1 captures: /home/huintech/capture-node/captures/camera1
Camera 0 service: capture-node-web@camera0.service
Camera 1 service: capture-node-web@camera1.service
Camera 0 Web port: 8000
Camera 1 Web port: 8001
```

- 배포 시 기존 instance 설정과 `captures/`는 보존한다.
- instance 설정이 없으면 `config.example.json`을 최초 배포 템플릿으로
  사용한다. instance별 Camera 번호만 배포 환경에 맞게 바꾸며, V4L2는 DNG를
  사용할 수 없어 PNG로 대체한다. 신규 설정에는 예약 촬영과 Storage를 넣지
  않는다.
- 기존 capture root의 파일은 Camera 0 디렉터리로 이동한다.
- 최초 배포는 기존 profile과 데이터 보존 호환성을 위해 instance별 `captures/`
  경로를 유지하지만 Supervisor profile의 CaptureNode는 이 경로에 저장하지 않는다.
- Raspberry Pi 배포 가상환경은 system site packages를 사용해 OS가 제공하는
  Picamera2와 libcamera Python binding을 사용할 수 있게 한다.
- Web 접속 주소는 배포 네트워크에서 장비에 할당된 현재 주소를 사용하며 문서에서
  고정하지 않는다.
- `eng/deploy.sh <user@host>`는 기본적으로 Camera 0 하나를 배포한다.
  `--camera-count 2`를 지정하면 Camera 0/1을 함께 배포한다. 로컬 테스트와 Web
  build 후 소스를 배포하고 systemd linger 활성화, 선택한 template service
  재시작과 health 확인을 수행한다.
- `--skip-if-current`는 마지막 성공 배포의 source ID가 로컬과 같으면
  test, Web build, 전송, service 변경과 activation을 생략한다. source ID
  marker는 activation 성공 후에만 갱신한다.
- `--supervisor-managed`는 CaptureNode source와 runtime만 설치·검증하고
  standalone camera 설정과 service를 만들거나 시작하지 않는다. 이 모드에서
  설정 생성과 child process lifecycle은 Pi Node Supervisor가 소유한다.
- `--camera picamera2[:N]` 또는 `--camera v4l2:/absolute/path`를 1~2회
  지정해 초기 instance 설정을 생성할 수 있다. 이 값은 해당 설정 파일이
  없을 때만 사용하며 기존 설정은 검증 후 보존한다.
- 최초 접속에서 `--bootstrap-ssh`는 알 수 없던 host key를 기록하고 로컬
  public key를 설치한 뒤 key 인증을 다시 확인한다. 변경된 host key는
  자동 승인하지 않는다.
- `--cache-wheels`는 온라인 provisioning 시 대상 Pi에 호환 wheel을 보관한다.
  `--offline`은 기존 runtime 또는 `.wheelhouse`만 사용하고 package index에
  접근하지 않는다. 호환 wheel은 `--wheelhouse`로 외부에서 전송할 수 있다.
- health probe 실패를 매 시도마다 출력하지 않고, 최종 실패 시 설정 경로·port,
  systemd 상태와 종료 코드, 최근 journal을 한번에 출력한다.
- 1대 배포 시 Camera 1 service는 중지·비활성화하되 기존 설정과 촬영 파일은
  보존한다.
- 기존 단일 Web service는 중지·비활성화하고 설치된 user unit을 제거한다.
- 배포 환경은 `capture-node[sftp]` 선택 dependency를 설치한다.
- 기존 가상환경에 Web·SFTP 런타임 의존성과 entry point가 모두 있으면 package
  index에 접속하지 않고 설치 상태를 재사용한다. 신규 장비의 최초 provisioning
  또는 의존성 누락 시에는 package source가 필요하다.
- 공통 `.env.local`은 최초 배포 시 빈 파일로 만들고 `0600`으로 제한한다.
- 선택한 instance의 `instances/cameraN.env`는 최초 생성 후 내용을 보존하며
  `.env.local`보다 나중에 읽어 같은 환경변수를 재정의할 수 있다.
- linger 활성화로 사용자 로그인 없이 재부팅 직후 user service가 시작된다.
- 카메라가 없어도 user service는 active 상태를 유지한다.
- 분리된 카메라가 다시 나타난 후 각 Web instance에서 카메라를 다시 연결할 수
  있다.

## 9. 검증 근거

추가 기능은 다음 자동화 테스트에서 검증한다.

| 영역 | 테스트 파일 | 검증 내용 |
| --- | --- | --- |
| USB 카메라 | `tests/test_v4l2_camera.py` | MJPEG 공유, control, discovery, 선택 도구 누락, 분리 후 stale capture 차단 |
| 카메라 교체 | `tests/test_camera_manager.py` | 교체 직렬화, 실패 복구, 연결 상태 전달 |
| 스케줄 | `tests/test_events_and_scheduler.py` | Daily, 매일 interval window, 계속 interval, 빈 규칙 대기와 재개 |
| 설정 | `tests/test_config.py` | 모드 검증, Image policy 런타임 저장, 빈 schedule·storage 영속화 생략 |
| Web | `tests/test_web.py` | Preview, Offline 상태, 즉시 촬영, Supervisor multipart 촬영, 이벤트 페이지 |
| 저장 조회 | `tests/test_storage_browser.py` | 페이지 조회, target·경로·symlink 경계 |
| SFTP | `tests/test_sftp_storage.py` | 인증 secret 분리, timeout, 날짜 경로, 임시 업로드·rename, 조회·목록·symlink 경계 |
| FTPS | `tests/test_ftps_storage.py` | 인증서 CA, `PROT P`, timeout, 날짜 경로, 임시 업로드·rename, 기존 파일 조회 |
| 인코딩·File 저장 | `tests/test_encoding_storage.py` | JPEG/PNG metadata, 날짜 경로와 기존 평면 파일 호환 |
| 통합 | `tests/test_core.py` | Preview only 수동 촬영, Image policy 적용, 종료 시 신규 작업 거부와 Storage drain |

## 10. 서비스 정상 종료

- systemd가 서비스를 중지하면 Web은 Uvicorn lifespan, Headless는
  `SIGTERM` handler를 통해 `CaptureNode.stop()`을 호출한다.
- 종료가 시작된 CaptureNode는 즉시 신규 수동·예약 촬영을 거부한다.
- 이미 수락한 촬영과 인코딩, 비동기 Storage 저장은 terminal state에 도달할
  때까지 기다린다.
- drain 완료 전에 프로세스를 정상 종료하거나 `lifecycle.stopped` 이벤트를
  발생시키지 않는다.
- 정지된 동일 인스턴스는 다시 시작하지 않으며, 서비스 재시작은 새 Core
  인스턴스를 구성한다.
- RTC, 시스템 전원 차단 및 Power Manager 정책은 이 기능 범위에 포함하지 않는다.
