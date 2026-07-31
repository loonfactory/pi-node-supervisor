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
| EXT-SCH-001 | 스케줄 모드 | 주기, 일일 지정 시각, 자동 촬영 중지 상태를 제공한다. |
| EXT-SCH-002 | 시간 범위 생성 | 시작 시각부터 종료 시각까지 분 간격의 일일 촬영 시각을 생성한다. |
| EXT-SCH-003 | 적용값 표시 | Preview 하단 Schedule은 편집값이 아닌 서버에 반영된 값을 표시한다. |
| EXT-WEB-001 | 즉시 촬영 | Web에서 스케줄과 별개로 한 번의 촬영을 요청한다. |
| EXT-WEB-002 | 이벤트 페이지네이션 | 최근 이벤트를 제한된 메모리와 페이지 단위로 조회한다. |
| EXT-STO-001 | 저장 파일 브라우저 | 설정된 Storage가 제공하는 파일을 조회, 미리보기, 다운로드한다. |
| EXT-STO-002 | SFTP Storage | 검증된 SSH host와 유한 timeout을 사용해 원격 저장·조회·목록을 제공한다. |
| EXT-IMG-001 | 이미지 정책 변경 | 실행 중 이미지 형식, 품질·압축 수준, 메타데이터 정책을 변경한다. |

### 2.1 기존 기준선과의 차이

| 영역 | 기존 문서 상태 | 추가 구현 |
| --- | --- | --- |
| USB 카메라 | SRS의 CAP-006에서 P4 후순위 | v0.1.0에 V4L2/FFmpeg 구현을 선반영 |
| 촬영 스케줄 | 초 단위 주기 촬영 | Daily 지정 시각, 시간 범위 생성, 자동 촬영 중지 추가 |
| 실행 중 설정 | CFG-009의 촬영 주기·파라미터 | Image policy까지 변경 범위 확대 |
| 카메라 설정 | 구현별 `apply(params)` 계약 | control 탐색 UI, 장치·해상도·FPS 교체 추가 |
| 상태 표시 | Web의 일반 설정·상태 확인 | 실제 연결 health, Offline 표시, 카메라 없는 Web 기동 추가 |
| 촬영 실행 | 스케줄 기반 자동 촬영 | Web 즉시 촬영 추가 |
| 이벤트 | 현재 프로세스 이벤트 표시 | 500건 제한과 페이지 조회 추가 |
| 저장 파일 | 저장 pipeline | Web 파일 목록, 미리보기, 다운로드 추가 |
| 원격 저장 | 향후 FTP 추가 가능성 | SFTP 공개키·환경변수 비밀번호 인증과 원격 파일 조회 구현 |
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
    "frame_rate": 24,
    "startup_timeout_seconds": 10
  }
}
```

- FFmpeg가 V4L2 장치의 MJPEG 스트림을 한 번만 연다.
- Preview와 스틸 촬영은 같은 최신 프레임 소스를 공유한다.
- 저장 이미지는 카메라 설정의 해상도를 유지한다.
- Web Preview는 컨테이너 너비 `100%`와 원본 비율로 표시한다.
- 런타임에는 FFmpeg와 `v4l2-ctl`이 필요하다.

### 3.2 장치 탐색과 연결

- `/api/camera/devices`는 등록된 모든 Camera 구현의 탐색 결과를 통합한다.
- Picamera2는 CSI camera 번호와 sensor model을 반환한다.
- Picamera2 main stream은 Python 배열이 RGB byte order가 되는 `BGR888`을
  사용해 PIL Preview와 저장 이미지의 적색·청색 channel이 뒤바뀌지 않게 한다.
- V4L2는 UVC driver와 video-capture capability를 모두 만족하는 실제 촬영
  `/dev/video*` 장치만 반환한다. ISP·metadata·codec node는 제외한다.
- 운영자는 Web에서 CSI와 USB/UVC 장치를 구분 없이 선택할 수 있다.
- CSI는 Width, Height, Preview FPS와 Preview quality를 변경할 수 있다.
- USB/UVC는 Width, Height와 FPS를 변경할 수 있다.
- 입력값이 현재 적용값과 다를 때만 `Connect camera` 버튼이 활성화된다.
- 연결 성공 후 버튼은 `Connected camera`로 바뀌고 비활성화된다.
- 새 장치 연결이 실패하면 이전 카메라 구성을 복구한다.

### 3.3 카메라 파라미터

- `/api/camera/controls`는 `v4l2-ctl --list-ctrls-menus` 결과를 구조화해 반환한다.
- Picamera2는 `horizontal_flip`, `vertical_flip` boolean parameter를 제공한다.
  변경 시 main stream Transform을 재구성하며 설정 파일에 영구 저장한다.
- 지원 UI는 정수 slider·number, boolean checkbox, menu select다.
- inactive control은 편집할 수 없다.
- `Camera parameters`는 기본적으로 접혀 있으며 우측 아이콘으로 펼친다.
- 변경한 control만 기존 `capture.params`와 병합해 `Apply changes`로 저장한다.

각 V4L2 촬영은 최신 프레임을 가져온 직후 전체 control 값을 조회한다.

- `observed_controls`: gain, exposure, white balance, focus 등을 포함한 조회값
- `control_snapshot_at`: control 조회 시각
- `control_snapshot_error`: 조회 실패 원인

조회는 촬영 프레임에 내장된 동기화 metadata가 아니라 촬영 직후의 장치 상태
snapshot이다. 따라서 프레임과 수십 ms 차이가 날 수 있다. 조회 실패는 이미지
촬영과 저장을 중단시키지 않으며 오류 정보만 metadata에 남긴다.

### 3.4 연결 상태와 분리 처리

`GET /api/health` 응답:

```json
{
  "status": "running",
  "camera_connected": false,
  "camera_detail": "V4L2 device does not exist: /dev/video0"
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

### 4.1 모드 계약

| mode | interval_seconds | daily_times | 동작 |
| --- | ---: | --- | --- |
| `interval` | 0보다 큰 초 | 빈 목록 | 적용 시점부터 지정 주기로 자동 촬영한다. |
| `daily` | `null` | 1개 이상의 시각 | 지정 timezone의 시각마다 매일 촬영한다. |
| `disabled` | `null` | 빈 목록 | Preview와 수동 촬영만 유지하고 자동 촬영은 중지한다. |

- 세 모드의 trigger 필드는 상호 배타적이다.
- `daily_times`는 `HH:MM:SS` 형식이며 중복을 허용하지 않는다.
- timezone은 IANA 이름으로 저장한다.
- 현재 장비의 Web UI에서는 `Asia/Seoul`을 내부 설정으로 유지하고 편집 필드는
  표시하지 않는다.

### 4.2 주기 입력

Web에서는 일, 시, 분, 초 입력을 제공한다. 서버와 설정 파일에는 합산한 초를
`capture.interval_seconds`로 저장한다.

### 4.3 시간 블록 생성

Daily times 화면은 하나 이상의 시간 블록을 입력받는다. 각 블록은 다음 값을
가진다.

- `Start`: 첫 촬영 시각
- `Every (min)`: 양의 정수 분 간격
- `End`: 마지막 허용 시각

예를 들어 `09:00`, `10`, `10:00`을 생성하면 다음 목록을 만든다.

```text
09:00:00, 09:10:00, 09:20:00, ..., 10:00:00
```

- 종료 시각이 간격에 정확히 포함될 때만 종료 시각도 생성한다.
- 종료 시각은 시작 시각보다 빠를 수 없다.
- 시간 블록 추가 버튼으로 오전·오후처럼 분리된 촬영 구간을 구성할 수 있다.
- 여러 블록을 생성하면 결과를 시간순으로 합치며, 동일 시각이 겹치면 적용하지
  않고 오류를 표시한다.
- 설정 파일에는 기존과 동일하게 병합된 `daily_times` 목록을 저장한다.
- 생성 후 개별 시각을 추가, 편집, 삭제할 수 있다.
- 휴지통 버튼은 전체 시각 목록을 한 번에 비운다.
- 빈 목록을 `Apply changes`하면 `disabled`로 저장한다.

### 4.4 적용값 표시

- Preview 하단 Schedule은 폼의 미적용 입력값이 아니라 `/api/config`의 적용값을
  표시한다.
- Interval 예: `Every 2m`
- 균일한 Daily 범위 예: `09:00-18:00 · Every 10m · Asia/Seoul`
- 다중 Daily 블록 예:
  `09:00-12:00 · Every 10m / 14:00-18:00 · Every 15m · Asia/Seoul`
- 모든 블록의 간격이 같으면 반복을 제거해
  `09:00-12:00 / 14:00-18:00 · Every 10m · Asia/Seoul`로 표시한다.
- 자동 촬영 중지 예: `Preview only`

## 5. Web 운영 기능

### 5.1 즉시 촬영

- `POST /api/capture`는 스케줄과 별개로 촬영과 인코딩을 동기 실행한다.
- 인코딩된 이미지가 백그라운드 저장 작업에 등록되면 저장 완료를 기다리지 않고
  `capture_id`와 `captured_at`을 반환한다.
- API 성공은 저장 완료가 아니라 촬영, 인코딩, 저장 작업 등록의 성공을 뜻한다.
- 저장 완료나 폐기는 기존 terminal 이벤트로 비동기 반영한다.
- 저장이 종결되기 전까지 해당 수집은 진행 중이며 다음 즉시 촬영은 `409`, 다음
  스케줄 촬영은 생략 이벤트로 처리한다.
- 이미 수집이 진행 중이면 `409`를 반환한다.
- 카메라가 분리된 상태에서도 `409`를 반환한다.
- `disabled` 스케줄에서도 카메라가 연결되어 있으면 즉시 촬영을 사용할 수 있다.

### 5.2 Recent activity

- 현재 프로세스의 최근 이벤트만 메모리에 보관한다.
- 최대 보관량은 500건이다.
- `/api/events`는 `page`, `page_size`를 받으며 페이지 크기는 최대 50건이다.
- Web 기본 페이지 크기는 10건이다.
- 각 행은 이벤트 종류, `capture_id` 또는 실패 원인, 발생 시각을 표시한다.

`Storage completed` 행의 의미:

- 이미지가 설정된 모든 저장 대상에 정상 저장됐다.
- 32자리 hex 문자열은 해당 촬영의 `capture_id`다.
- 표시 시각은 브라우저 현지 시각의 이벤트 발생 시각이다.

### 5.3 저장 파일 브라우저

- `Storage`는 필수 쓰기와 파일 읽기·목록 조회 계약을 제공한다.
- 읽기나 목록 조회를 지원하지 않는 구현은 `io.UnsupportedOperation`을 발생시킨다.
- `/api/storage/targets`는 `StorageProvider`에 등록된 대상을 이름으로 제공한다.
- `/api/storage/files`는 최신 파일부터 페이지 단위로 반환한다.
- Web 기본 페이지 크기는 12개다.
- 이미지 파일은 thumbnail을 표시하고 원본 열기와 다운로드를 제공한다.
- Web은 Storage 설정 타입이나 로컬 디렉터리를 해석하지 않고 해당 구현의
  `list`와 `read`를 호출한다.
- File Storage는 storage 최상위 파일만 읽을 수 있다.
- 알 수 없는 target, 경로 이탈, 외부를 가리키는 symlink는 거절한다.
- 원격 OS 파일관리자나 임의 filesystem 탐색 기능은 제공하지 않는다.

### 5.4 Web 범위와 오류

- Web은 카메라 제어와 상태 확인을 함께 제공하는 단일 사용자 운영 화면이다.
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
  "remote_directory": "/captures",
  "password_env": "CAPTURE_NODE_SFTP_PASSWORD",
  "known_hosts": "/home/huintech/.ssh/known_hosts",
  "connect_timeout_seconds": 10,
  "operation_timeout_seconds": 30
}
```

- `remote_directory`는 정규화된 절대 POSIX 경로여야 하며 서버에 미리 존재해야
  한다.
- 각 저장·조회·목록 작업은 독립된 SSH/SFTP 연결을 열고 작업 후 명시적으로
  닫는다.
- connect, SSH banner, 인증, channel open과 SFTP read/write에 유한 timeout을
  적용한다.
- 서버 host key는 `known_hosts`와 일치해야 하며 알 수 없는 host를 자동
  등록하지 않는다.
- 업로드는 같은 원격 디렉터리의 임시 파일에 먼저 기록하고 POSIX rename을
  우선 사용한다. 서버가 확장을 지원하지 않으면 표준 SFTP rename으로 대체한다.
- 업로드 실패 시 임시 파일 삭제를 시도하며 자동 재시도는 하지 않는다.
- 목록과 읽기는 최상위 일반 파일만 제공하고 symlink와 경로 이탈을 거부한다.
- `private_key` 또는 `password_env` 중 하나 이상이 필요하다. 암호화된 개인키의
  passphrase는 `private_key_passphrase_env`로 참조할 수 있다.
- JSON은 Web의 `/api/config`에서 조회되므로 password와 passphrase 원문을
  허용하지 않는다.
- 모든 instance가 공유하는 secret은 systemd가 먼저 읽는 `.env.local`에
  저장한다. 특정 instance의 `instances/<instance>.env`에 같은 변수를 지정하면
  나중에 읽은 instance 값이 우선한다.
- `.env.local`과 instance `.env`는 모두 권한을 `0600`으로 제한하고 배포 시
  기존 내용을 보존한다.

## 6. Image policy

### 6.1 실행 중 변경

Web의 `Apply changes`로 다음 값을 변경한다.

- `image.format`: `jpeg` 또는 `png`
- `image.quality`: 내부 품질·압축 척도 `1-100`
- `image.embed_metadata`: metadata 포함 여부

변경은 설정 파일에 저장되며 다음 촬영부터 적용된다. 기존 저장 파일은 변경하지
않는다.

`Embed metadata`가 활성화되면 JPEG와 PNG 모두 EXIF에 촬영 시각, 카메라
파라미터, 노출과 gain을 기록한다. PNG metadata는 `eXIf` chunk만 사용하며 PNG
text chunk는 기록하지 않는다. 비활성화하면 EXIF를 생략한다.

표준 EXIF tag에 직접 대응하지 않는 구현별 카메라 파라미터는 `UserComment`에
JSON으로 기록한다. 따라서 전체 파라미터를 보존할 수 있지만, 일반 이미지
뷰어에서는 `UserComment`의 JSON이 표시되지 않을 수 있다. V4L2 촬영의
`observed_controls`와 snapshot 시각·오류도 이 JSON에 포함한다.

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
- 하단 요약 예: `PNG · Compression 6 · Metadata off`

Web의 PNG 압축 수준은 기존 설정 모델과 다음처럼 변환한다.

```text
image.quality = 100 - (compression × 11)
```

따라서 Compression 0은 내부 quality 100, Compression 9는 내부 quality 1이다.

## 7. API 추가·변경 목록

| Method | Path | 목적 |
| --- | --- | --- |
| `GET` | `/api/health` | Web runtime과 실제 카메라 연결 상태 조회 |
| `GET` | `/api/camera/devices` | 연결 가능한 V4L2 capture 장치 조회 |
| `GET` | `/api/camera/controls` | 현재 장치의 control과 범위 조회 |
| `PUT` | `/api/camera` | 장치, 해상도, FPS를 적용하고 카메라 교체 |
| `POST` | `/api/capture` | 즉시 촬영 |
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

- 배포 시 두 instance 설정과 `captures/`는 보존한다.
- 기존 단일 `config.usb.json`은 최초 전환 시 Camera 0 설정으로 복사하고 같은
  일정과 Image policy를 Camera 1 초기 설정에도 적용한다.
- 기존 capture root의 파일은 Camera 0 디렉터리로 이동한다.
- 최초 배포 시 애플리케이션·인스턴스별 `captures/`·가상환경 경로와 두 설정을
  생성한다.
- Raspberry Pi 배포 가상환경은 system site packages를 사용해 OS가 제공하는
  Picamera2와 libcamera Python binding을 사용할 수 있게 한다.
- Web 접속 주소는 배포 네트워크에서 장비에 할당된 현재 주소를 사용하며 문서에서
  고정하지 않는다.
- `scripts/deploy.sh <user@host>`는 로컬 테스트와 Web build 후 소스를 배포하고
  systemd linger 활성화, 두 template service 재시작과 health 확인을 수행한다.
- 배포 환경은 `capture-node[sftp]` 선택 dependency를 설치한다.
- 공통 `.env.local`은 최초 배포 시 빈 파일로 만들고 `0600`으로 제한한다.
- `instances/camera0.env`, `instances/camera1.env`는 최초 생성 후 내용을
  보존하며 `.env.local`보다 나중에 읽어 같은 환경변수를 재정의할 수 있다.
- linger 활성화로 사용자 로그인 없이 재부팅 직후 user service가 시작된다.
- 카메라가 없어도 user service는 active 상태를 유지한다.
- 분리된 카메라가 다시 나타난 후 각 Web instance에서 카메라를 다시 연결할 수
  있다.

## 9. 검증 근거

추가 기능은 다음 자동화 테스트에서 검증한다.

| 영역 | 테스트 파일 | 검증 내용 |
| --- | --- | --- |
| USB 카메라 | `tests/test_v4l2_camera.py` | MJPEG 공유, control, discovery, 분리 후 stale capture 차단 |
| 카메라 교체 | `tests/test_camera_manager.py` | 교체 직렬화, 실패 복구, 연결 상태 전달 |
| 스케줄 | `tests/test_events_and_scheduler.py` | Daily 다음 시각, disabled 대기와 재개 |
| 설정 | `tests/test_config.py` | 모드 검증, Image policy 런타임 저장 |
| Web | `tests/test_web.py` | Preview, Offline 상태, 즉시 촬영, 이벤트 페이지 |
| 저장 조회 | `tests/test_storage_browser.py` | 페이지 조회, target·경로·symlink 경계 |
| SFTP | `tests/test_sftp_storage.py` | 인증 secret 분리, timeout, 임시 업로드·rename, 조회·목록·symlink 경계 |
| 인코딩 | `tests/test_encoding_storage.py` | JPEG/PNG와 metadata 정책 |
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
