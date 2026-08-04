# CaptureNode v0.1.0 Implementation Decisions

이 기록은 DES-CAPTURENODE-001에서 `[검토 필요]`로 남긴 항목과 개발 중 승인된
v0.1.0 추가 기능의 구현 결정과 근거를 관리한다. 원래 요구사항의 기준선은
SRS-CAPTURENODE-001이며, 추가된 기능 계약은
`documents/CaptureNode_추가기능명세_v0.1.0.md`에서 관리한다.

## DEC-001 Camera Capabilities

- 결정: 스틸 촬영 `Camera`와 Preview `FrameSource`를 능력별 Protocol로 분리한다.
- 근거: Preview 미지원 구현을 `None` 반환이나 런타임 옵션으로 표현하지 않고 타입
  수준에서 판별할 수 있으며, WEB-009의 독립된 수명주기를 유지하기 쉽다.
- 기각: 선택 메서드를 가진 단일 인터페이스. 모든 카메라 구현에 Preview 의미를
  강제하고 미지원 상태 조합을 늘리므로 기각했다.

## DEC-002 Storage Registration

- 결정: 설정의 `type` 식별자를 검증된 저장 옵션 모델로 변환하고,
  `ConfiguredStorageProvider`가 구현을 생성해 `StorageProvider` 계약으로
  Core에 주입된다.
- 등록 경계: Storage 옵션과 구현 등록은 `core/storages` 안에서 관리한다.
  `ConfiguredStorageProvider`는 구체 구현을 직접 생성하지 않고
  `StorageRegistry`에 위임한다. 새 Storage 추가 시 해당 기능 영역의 options,
  registration, adapter만 변경하며 CaptureNode, Distributor와 host 조립은
  변경하지 않는다.
- 원격 Storage: FTP 같은 네트워크 기반 구현의 연결, 인증, timeout과 전송
  수명주기는 해당 Storage adapter가 소유한다. 범용 NetworkProvider는 실제
  공유 계약이 생기기 전까지 도입하지 않는다.
- Web 경계: 이 결정의 file storage 전용 조회 범위는 DEC-015로 대체됐다.
- 근거: Headless가 JSON 설정만으로 완전히 조립되어야 하는 CFG-004와 저장 제공자
  계약 IF-STO-004를 함께 만족한다.
- 기각: 코드 전용 등록. 재사용 호스트에는 유연하지만 Headless 설정 완전성을
  깨므로 기본 경로로 채택하지 않았다.

## DEC-003 Event Envelope

- 결정: 표준 `logging`의 `extra.capture_event`에 JSON 호환 dict를 싣는다. 공통
  필드는 `schema_version`, `event_type`, `timestamp`이며 스키마 버전은 1이다.
- Host 구성: Core는 이벤트 스키마와 발생만 소유한다. Headless와 Web은 각각의
  `logging_config.py`에서 Formatter와 Handler 구성을 독립적으로 소유한다.
  초기 Console 정책이 같더라도 공통 logging configuration을 공유하지 않으며,
  향후 DB나 host 전용 출력 정책 변경이 다른 host에 전파되지 않게 한다.
- 근거: Core가 handler나 출력 형식을 구성하지 않으면서도 Web과 사용 프로젝트가
  안정적으로 이벤트를 해석할 수 있다.
- 기각: JSON 문자열 메시지. 수신자가 다시 파싱해야 하고 일반 로그 메시지와
  구조화 필드의 경계가 약해지므로 기각했다.

## DEC-004 Image Defaults

- 결정: Image Options는 `JpegImageOptions`(quality 기본 90)와
  `PngImageOptions`(compression 기본 9)의 discriminated union으로 분리한다.
  새 설정의 기본 형식은 무손실 PNG이고 메타데이터는 기본 포함한다.
- 근거: 손실 품질과 무손실 압축 수준은 의미가 다르므로 공통 `quality`
  수치와 역변환을 공유하지 않는다.
- Web 표현: JPEG는 `quality 1-100`, PNG는 `compression 0-9`를 각 설정
  필드에 그대로 표시하며 서로 변환하지 않는다.
  PNG 압축 수준이 커질수록 파일 압축은 강해지지만 픽셀 화질은 변하지 않는다.
- 메타데이터: 활성화 시 JPEG와 PNG 모두 EXIF를 기록한다. 구현별 전체 카메라
  파라미터는 EXIF `UserComment`의 JSON으로 보존하며, PNG text metadata는
  중복 기록하지 않는다.
- V4L2 snapshot: 촬영 직후 `v4l2-ctl` control 조회값과 조회 시각을
  `UserComment` JSON에 포함한다. 조회 실패는 수집 실패로 승격하지 않고 오류
  필드만 기록한다.
- 주의: 운영 데이터의 용량 측정 결과에 따라 새 결정 기록으로 기본값을 변경한다.

## DEC-005 Runtime Configuration Boundary

- 결정: 실행 중에는 촬영 스케줄, `capture.params`, 이미지 형식·품질·메타데이터
  정책을 변경한다. 저장 대상은 파일 수정 후 재시작으로 적용한다.
- 근거: 이미지 인코더는 각 촬영을 처리할 때 현재 정책을 읽으므로 외부 자원 교체
  없이 다음 촬영부터 원자적으로 적용할 수 있다. 전체 옵션 문서를 제출하되 저장
  영역의 차이가 있으면 전체 변경을 거절한다.

## DEC-006 USB Camera Extension

- 결정: 사용자 요청으로 CAP-006의 V4L2 구현을 v0.1.0 개발 범위에 선반영한다.
  설정의 `camera.type`으로 `picamera2`와 `v4l2`를 선택한다.
- 구현: V4L2 장치는 하나의 지속적인 FFmpeg 프로세스가 MJPEG를 읽고, 스틸 촬영과
  Preview가 그 프레임 소스를 공유한다. 카메라 제어는 `v4l2-ctl` 경계로 격리한다.
- 선택 기능: FFmpeg는 필수지만 `v4l2-ctl`은 discovery, control과 촬영 직후
  snapshot의 선택 의존성이다. 도구가 없으면 프로세스당 한 번 경고하고 Web에는
  구조화된 unavailable capability를 반환하며 Preview·촬영·저장은 유지한다.
  변경되지 않은 params는 재적용하지 않고 실제 control 변경만 거절한다.
- Web 관찰: 초기 진입에서 control을 한 번 읽고 이후 EventBuffer의 최신
  `capture.succeeded` ID가 변경될 때마다 실제 적용값을 다시 읽는다.
  조회는 장치에 값을 쓰지 않고, 저장 전 편집 중인 control은 임시
  표시값을 유지한다.
- Web 장치명: V4L2 options와 API 식별자는 전체 장치 경로를 유지한다. 화면에는
  kernel 장치명과 짧은 node 또는 정리한 `by-id` basename을 표시하고 전체 경로는
  hover title로만 제공해 안정적인 식별과 가독성을 분리한다.
- 반전 우선순위: `horizontal_flip`과 `vertical_flip`은 축별로 같은 이름의
  V4L2 control 존재 여부를 판정한다. 존재하면 hardware에 위임하고, 없는 축만
  저장 이미지의 PIL transpose와 Web Preview CSS transform으로 처리한다. control
  응답의 `application`은 `hardware` 또는 `software`를 명시하며 Web은 software
  축만 CSS로 변환한다. `mirror`는 horizontal flip과 중복되므로 추가하지 않는다.
- 성능: software Preview 반전은 브라우저 합성으로 처리하고 FFmpeg의 MJPEG
  `copy` pipeline은 유지한다. 따라서 두 USB 카메라의 모든 frame을 서버에서
  decode·filter·re-encode하지 않는다.
- 근거: OpenCV 같은 추가 런타임 없이 UVC 카메라의 압축 프레임을 그대로
  사용하며, 운영 패키지 일부가 누락돼도 기본 수집 경로를 유지한다.
- 기각: 스틸과 Preview마다 장치를 별도 개방하는 방식. 장치 점유 충돌과
  WEB-009 위반 가능성이 있어 기각했다.

## DEC-007 Capture Schedule Modes

- 결정: 촬영 트리거는 `interval`, `daily`, `disabled` 상태를 제공한다. `daily`는
  IANA timezone과 하루 한 번씩 반복되는 `HH:MM:SS` 목록을 사용한다.
  `disabled`는 Preview와 수동 촬영을 유지하면서 자동 촬영 트리거만 중지한다.
- 근거: 초 단위 주기 계약을 유지하면서 운영자가 특정 현지 시각을 명시할 수 있다.
  timezone을 설정에 영속화하여 호스트 timezone 변경에 따른 의미 변화를 막는다.
- 제약: 트리거 필드는 상호 배타적이며 중복 시각은 전체 설정 검증 오류로
  처리한다. `daily_times`의 `null`은 빈 목록으로 정규화하고, 시각이 없는
  `daily`는 모드를 유지한 채 자동 촬영 없이 대기한다. 이를 통해 Daily 모드와
  촬영 시각을 단계적으로 설정할 수 있다.

## DEC-008 Web Event Pagination

- 결정: Web 이벤트 보관은 프로세스 메모리의 최근 500건으로 제한하고, API는
  페이지당 최대 50건만 반환한다. 운영 화면 기본 페이지 크기는 10건이다.
- 근거: WEB-008의 프로세스 범위 보관을 유지하면서 DOM 크기와 응답 크기를
  이벤트 발생량과 분리한다.
- 기각: 무한 스크롤. 최근 이벤트와 과거 이벤트의 위치가 계속 이동해 운영자가
  현재 보고 있는 범위를 명확히 식별하기 어려우므로 명시적 페이지를 채택했다.

## DEC-009 Camera Discovery and Live Replacement

- 결정: Camera Registry의 각 Registration은 선택적인 discovery를 제공한다.
  Provider는 등록된 모든 구현의 결과를 하나의 `CameraDevice` 목록으로 합친다.
  Picamera2는 CSI camera 번호와 model을, V4L2는 UVC driver와 video-capture
  ioctl을 모두 만족하는 node를 제공한다.
- 경계: Pi의 ISP·metadata·codec node는 선택 대상에서 제외한다. 제한 없는
  탐색은 임의 `/dev/video*` 노출이 아니라 등록된 Camera 구현이 지원하는 모든
  실제 촬영 장치의 탐색을 의미한다.
- CSI 색상: Picamera2/libcamera format 명칭의 byte order를 그대로 가정하지
  않는다. PIL이 RGB로 해석할 Python 배열에는 `BGR888` stream을 사용한다.
- CSI 반전: H/V 반전은 Camera Options가 아니라 구조화된 Camera parameter로
  노출한다. 적용 시 Picamera2 main stream Transform을 재구성하고 다른 libcamera
  control은 기존 `set_controls` 경로로 전달한다.
- 런타임 교체: `ConfiguredCameraProvider`가 `CameraManager`, 구현 선택, rollback,
  종료 수명주기를 소유한다. Web은 `CameraReplacementProvider` capability만
  호출한다. 새 장치 개방 실패 시 이전 옵션으로 장치를 다시 열어 런타임을
  복구한다.
- 연결 상태: Web 호스트는 시작 시 카메라가 없거나 실행 중 분리되더라도
  `UnavailableCamera`와 상태 capability를 통해 계속 실행한다. 상태 API는 실제
  연결 여부를 반환하고, 연결이 복구되기 전 Preview와 촬영 요청을 거절한다.
- Preview 동시성: 여러 Preview 소비자는 동일한 최신 프레임 흐름을 독립적으로
  받는다. Camera 수명주기 잠금은 프레임 대기 중 유지하지 않으며, 소비자 수에
  따라 각 소비자의 프레임률이 분할되지 않게 한다.
- 기각: Web에서 임의 `/dev/video*` 문자열 입력. metadata/ISP 노드 선택과 장치
  오입력 가능성이 있어 탐색된 capture node 선택을 기본 경로로 채택했다.

## DEC-010 Stored Capture Browser

- 상태: 저장 조회 대상과 계약은 DEC-015로 대체됐다. filesystem 경계 결정은
  File Storage에 계속 적용한다.
- 결정: 브라우저에서 원격 OS 파일관리자를 실행하지 않는다. 대신 설정에 등록된
  Storage가 노출한 파일만 읽기 전용으로 페이지 조회·미리보기·다운로드한다.
- 보안 경계: 대상 이름과 basename을 검증하고, resolve 결과가 storage root
  외부이거나 외부를 가리키는 symlink면 거절한다.
- 기각: 임의 filesystem 탐색 API. 신뢰된 로컬 네트워크 전제와 별개로 설정된
  저장 책임 범위를 넘어 서버 filesystem을 노출하므로 기각했다.

## DEC-011 Source Package Layout

- 결정: 공유 애플리케이션과 계약은 `src/capture_node/core/`, CLI 호스트는
  `src/capture_node/headless/`, FastAPI 호스트는 `src/capture_node/web/`에서
  관리한다. Camera 계약·Provider·수명주기·구현은
  `src/capture_node/core/cameras/`, Storage 계약·Provider·분배·구현은
  `src/capture_node/core/storages/`에서 기능 단위로 관리한다.
- 의존성: Headless와 Web은 Core를 사용할 수 있지만 Core는 어느 호스트에도
  의존하지 않는다.
- Web 자산: React/Vite 원본은 `src/capture_node/web/frontend/`, FastAPI가
  제공하는 빌드 결과는 `src/capture_node/web/static/`에 둔다. wheel에는
  frontend 원본을 제외하고 static 결과만 포함한다.
- 근거: Web 백엔드와 프론트엔드의 변경 위치를 하나로 모으면서도 Python 런타임
  배포물에 Node.js 개발 의존성을 포함하지 않는다. Camera나 Storage 변경 시
  기술 역할별 디렉터리를 오가지 않고 관련 코드를 한 패키지에서 찾을 수 있다.
- 기각: 루트 `web/`에 React 원본을 분리하는 방식은 하나의 Web 기능이 두 경로로
  나뉜다. `src/core`, `src/web` 같은 최상위 일반 패키지는 이름 충돌 가능성이
  있고 현재 `capture_node` wheel 경계를 벗어나므로 채택하지 않았다. 공용
  `adapters/`, `providers/` 분류는 하나의 기능을 여러 기술 디렉터리로 나누므로
  채택하지 않았다.

## DEC-012 Resource Provider Injection

- 결정: `CaptureNode`는 구체 Camera, Distributor 또는 Storage 구현을 직접 받지
  않고 `CameraProvider`와 `StorageProvider` Protocol을 생성자로 주입받는다.
- Camera Provider: 현재 Camera 접근과 종료를 기본 계약으로 한다. 장치 탐색과
  런타임 교체는 각각 `CameraDiscoveryProvider`,
  `CameraReplacementProvider` capability로 분리한다.
- 기본 구현: `ConfiguredCameraProvider`가 Camera Registry, 직렬화된 교체,
  rollback, unavailable fallback과 종료를 소유한다.
  `ConfiguredStorageProvider`는 Storage Registry로 생성한 구현을 검증된 설정
  순서대로 제공한다.
- 인터페이스 방식: Provider와 자원 계약은 구조적 인터페이스인 `Protocol`을
  사용한다. 실제 `isinstance` 판별이 필요한 선택 capability에만
  `runtime_checkable`을 적용한다.
- 근거: Headless와 Web에서 구현 선택과 수명주기 코드를 중복하지 않고, 테스트와
  외부 호스트가 상속 없이 자체 Provider를 주입할 수 있다.
- 기각: 전역 service locator와 범용 `Provider[T]`. 의존성을 숨기고 단일 Camera와
  복수 Storage의 서로 다른 수명주기 의미를 약화하므로 채택하지 않았다.

## DEC-013 Camera Registration and Options

- 결정: 중앙 `create_camera()`의 `isinstance` 분기를 제거한다. immutable
  `CameraRegistry`가 문자열 key를 `CameraRegistration`에 연결하고,
  Registration은 Options 타입, Camera 생성 callable과 선택적인 discovery를
  묶는다.
- Options: `Picamera2Options`, `V4L2CameraOptions`, discriminated
  `CameraOptions`는 `core/cameras/options.py`에서 Camera 기능과 함께 관리한다.
  전체 `config.py`는 이 모델을 조합하고 영속화한다.
- 검증: Picamera2 width/height 동시 지정 같은 불변조건은 Camera 생성 시점이
  아니라 Pydantic Options 모델 검증 시점에 적용한다.
- 생성: 각 Camera 구현은 `from_options()`를 제공한다. 기본 Registry 조립은
  `default_camera_registry()` 한 곳에서 수행하며 중복 key를 거절한다.
- Provider: `ConfiguredCameraProvider`는 구체 구현이나 분기 함수를 알지 않고
  주입된 Registry의 `create()`만 사용한다.
- 근거: ASP.NET Core Authentication의 Scheme Registry와 Handler Provider처럼
  등록 정보 조회, 인스턴스 공급, 자원 수명주기를 분리한다. Options 검증 역시
  구현 생성과 분리한다.
- 제약: 지원 카메라 타입은 현재 제품 설정 스키마의 discriminated union에
  명시한다. 런타임 플러그인 검증이나 service locator는 도입하지 않는다.

## DEC-014 Private Web Host Contract

- 범위: Web은 카메라 제어와 상태 확인을 함께 제공하는 단일 사용자 운영 화면이며,
  서버와 같은 버전으로 배포된다. 외부 API 소비자는 지원하지 않는다.
- API: 인증·인가와 API versioning은 도입하지 않는다. 일반 HTTP 오류만 최소
  Problem Details 형식으로 통일하며 별도 오류 카탈로그는 만들지 않는다.
- 즉시 촬영: 촬영과 인코딩, 백그라운드 저장 등록까지 동기 처리하고 HTTP 응답을
  반환한다. 저장 종결은 기다리지 않고 이벤트 화면에 비동기 반영한다.
- 수집 불변조건: 저장이 완료 또는 폐기로 종결되기 전에는 기존 pending 상태를
  유지해 다음 촬영을 거절하거나 생략한다.
- 근거: 독립 클라이언트 호환성보다 한 명의 운영자가 장치와 결과를 확인하는 최소
  기능과 기존 이진 종결 보장이 우선한다.

## DEC-015 Storage Read Contract

- 결정: `Storage`는 필수 `save`와 파일 `read`, `list`, `delete` 계약을
  함께 제공한다.
  조회를 지원하지 않는 구현은 `io.UnsupportedOperation`을 발생시킨다.
- Web 경계: Web은 Options의 구체 Storage 타입을 판별하지 않고
  `StorageProvider`가 제공한 구현에 조회를 위임한다. 대상 응답에는 안정된
  Storage 이름만 노출한다.
- File Storage: 쓰기·읽기·목록 조회를 모두 제공하며 basename, root 이탈과 외부
  symlink를 구현 경계에서 검증한다.
- 근거: 현재 세 가지 동작만 필요한 최소 범위에서는 capability Protocol을 더
  분할하지 않으면서도 새 Storage 추가가 Web이나 배포 pipeline 수정으로
  이어지지 않게 한다.

## DEC-016 Web Runtime Deployment

- 결정: `eng/deploy.sh <user@host>`는 기본적으로 Raspberry Pi의 Camera 0 Web
  instance만 갱신한다. `--camera-count 2`를 지정하면 Camera 0/1을 함께
  갱신한다. 장비 주소는 실행 시 받고 저장소나 문서에 고정하지 않는다.
- 명시적 카메라: `--camera`를 instance 순서대로 반복해 `picamera2[:N]` 또는
  `v4l2:/absolute/path`를 지정할 수 있다. 지정값은 설정 파일이 없을 때만 초기
  Options 생성에 사용하고 기존 운영 설정은 검증 후 보존한다. `--camera-count`는
  Picamera2 기본값을 만드는 호환 옵션으로 유지한다.
- 인스턴스: systemd template unit을 사용한다. Camera 0은
  `config.camera0.json`, `captures/camera0`, port 8000을 사용하고 Camera 1은
  `config.camera1.json`, `captures/camera1`, port 8001을 사용한다. 소스와
  `.venv`만 공유한다.
- 초기 설치: 대상 경로가 없으면 애플리케이션·선택한 instance의 촬영·가상환경
  경로를 생성한다. instance 설정은 예제 JSON을 복사하지 않고 검증된 Options
  모델로 Picamera2, Preview-only 일정, PNG, 로컬 Storage와 365일 보존 정책을
  생성한다. 이후 배포에서는 이미 생성된 운영 설정과 촬영 파일을 교체하지
  않는다.
- Raspberry Pi Python: 배포 venv는 system site packages를 포함해 OS의
  Picamera2/libcamera binding을 사용한다. 플랫폼 모듈은 해당 Registration의
  생성 또는 discovery 시점에만 import한다.
- 검증: 전송 전에 로컬 테스트와 Web build를 수행하고, 전송 후 선택한 systemd
  user service를 재시작한 다음 해당 port의 `/api/health` 응답을 확인한다. probe
  오류는 반복 출력하지 않고 실패 시 설정 경로와 port, systemd result와 exit
  status, service status, 최근 journal을 함께 출력한다.
- 격리망 재배포: 기존 가상환경의 Web·SFTP 런타임 의존성과 entry point를 import로
  검증할 수 있으면 원격 `pip install`을 생략한다. 신규 장비나 의존성 변경은 별도
  provisioning 단계에서 package source를 사용할 수 있어야 한다.
- 오프라인 모드: `--cache-wheels`는 온라인 provisioning 중 대상 Pi에서 현재
  architecture와 Python에 맞는 runtime 및 build wheel을 보관한다. `--offline`은
  package index 접근을 금지하고 기존 runtime 또는 `.wheelhouse`만 사용한다.
  외부 wheelhouse는 `--wheelhouse`로 전송할 수 있으며 대상과 호환되지 않거나
  불완전하면 이전 source로 rollback한다.
- 최초 SSH: 알 수 없는 host key는 OpenSSH `accept-new`로 로컬 known_hosts에
  기록하되 변경된 key는 거절한다. `--bootstrap-ssh`는 지정 public key를
  `ssh-copy-id`로 설치한 뒤 BatchMode key 인증을 다시 검증한다.
- 부팅: 배포 시 대상 사용자의 systemd linger를 활성화해 대화형 로그인 없이
  user service가 부팅 과정에서 시작되게 한다. 최초 활성화에는 sudo 권한이
  필요할 수 있다.
- 전환: 새 `capture_node` 소스는 staging 경로에 먼저 전송한다. 활성화 또는
  health 확인에 실패하면 직전 소스로 되돌리고 서비스를 다시 시작한다.
- 손상 방지: package 설치 후 Web entry point와 import, 정적 index가 비어 있지
  않은지 확인한다. systemd template unit은 임시 파일을 검증한 뒤 같은
  filesystem에서 원자적으로 교체해 설치 중단 시 기존 unit을 보존한다.
- 보존: instance 운영 설정, 촬영 파일 `captures/`, Python 가상 환경 `.venv/`는
  후속 배포에서 교체하지 않는다. 1대 배포는 Camera 1 service를 중지하고
  비활성화하지만 기존 Camera 1 설정과 촬영 파일은 삭제하지 않는다.
- 정리: template service 전환 이후 기존 단일 Web service는 중지·비활성화하고
  설치된 user unit을 제거한다. 기존 설정 파일과 촬영 데이터는 자동 삭제하지
  않는다.

## DEC-017 Graceful Service Shutdown

- 결정: `CaptureNode.stop()`은 종료 진입과 동시에 신규 수집을 거부하고,
  Scheduler를 중지한 다음 이미 수락한 촬영·인코딩과 비동기 Storage 배포가
  종결될 때까지 기다린다. drain이 끝난 뒤에만 `lifecycle.stopped` 이벤트를
  발생시키고 정상 종료한다.
- Host 연결: Headless는 `SIGINT`와 `SIGTERM`, Web은 Uvicorn의 application
  lifespan 종료를 동일한 `stop()` 호출로 연결한다. systemd나 특정 전원 관리
  구현은 Core가 알지 않는다.
- 수명주기: 정지된 `CaptureNode` 인스턴스는 실행기와 Distributor가 닫혔으므로
  다시 시작하지 않는다. 재시작은 Host가 새 인스턴스를 구성하는 방식으로
  처리한다.
- 근거: 현재 순간의 idle 상태 조회에는 종료 판단과 다음 작업 예약 사이의 경쟁
  조건이 있다. 신규 작업을 먼저 차단한 뒤 수락된 작업을 drain하는 방식이 저장
  종결 보장을 유지하면서 표준 프로세스 종료 신호를 처리하는 최소 계약이다.

## DEC-018 SFTP Storage and Secrets

- 결정: SFTP는 별도 프로젝트가 아니라 기존 Storage registration으로 제공한다.
  `SftpStorage`가 `save`, `read`, `list`를 모두 구현하며 CaptureNode,
  Distributor와 Web에는 SFTP 분기를 추가하지 않는다.
- 연결: 작업마다 독립된 Paramiko SSH/SFTP session을 생성하고 명시적으로
  닫는다. 공유 연결과 connection pool은 현재 동시성 요구가 없고 장애 수명주기를
  복잡하게 하므로 도입하지 않는다.
- 신뢰: 지정한 `known_hosts`를 읽고 기본 reject policy를 명시적으로 사용한다.
  알 수 없는 host를 자동 등록하는 `AutoAddPolicy`는 사용하지 않는다.
- 저장: 동일 디렉터리 임시 파일을 완성한 뒤 OpenSSH POSIX rename을 우선하고,
  지원하지 않는 서버에서는 표준 SFTP rename을 사용한다. 자동 재시도는 기존
  단일 시도 정책에 따라 제공하지 않는다.
- 인증: JSON에는 `password_env`와 `private_key_passphrase_env` 같은 환경변수
  이름만 저장한다. 원문 secret field는 strict options 검증에서 거절한다.
  Web이 전체 options를 반환하므로 JSON password는 운영 편의보다 노출 위험이
  크다.
- 배포: 공통 secret은 배포가 덮어쓰지 않는 `.env.local`에서 주입한다.
  instance별 `.env`를 나중에 읽어 동일한 환경변수를 재정의할 수 있으며 두
  파일의 권한은 `0600`으로 제한한다. Paramiko는 `capture-node[sftp]` 선택
  dependency로 격리하되 운영 Web 배포에는 설치한다.
- 제약: SFTP 디렉터리는 서버에 미리 생성돼 있어야 한다. Web read 계약은 현재
  파일 전체를 메모리에 반환하므로 대형 영상 streaming은 이 결정 범위가 아니다.

## DEC-019 Daily Storage Layout

- 결정: File과 SFTP Storage의 기본 `layout`은 `daily`이며 CaptureNode가 만든
  UTC 파일명 접두어를 이용해 `yyyyMMdd/기존 파일명`에 저장한다. `flat`을
  명시하면 평면 저장을 유지한다.
- 경계: 물리 경로만 변경하고 Storage의 논리 파일명, `SaveResult`, Web API와
  Distributor 계약은 변경하지 않는다. 파일명의 날짜가 유효하지 않은 일반
  `save` 호출은 최상위에 저장한다.
- 호환성: 날짜 디렉터리와 기존 최상위 파일을 함께 읽고 목록에 제공한다.
  symlink, basename과 root 이탈 검증은 양쪽 경로에 동일하게 적용한다.
- 자원 사용: 목록은 모든 파일을 순회해 정확한 전체 개수를 계산하지만 요청한
  페이지 끝까지 필요한 최신 항목만 메모리에 유지한다. SFTP도 날짜 디렉터리를
  하나씩 조회하며 전체 원격 파일 객체를 한 목록으로 합치지 않는다.
- 근거: 장기 운용 시 단일 디렉터리의 파일 수 증가로 발생할 수 있는 filesystem
  및 원격 Storage 장애와 조회 비용을 제한하면서 기존 파일 접근을 보존한다.

## DEC-020 Explicit FTPS Storage

- 결정: SSH 기반 SFTP와 구분되는 `ftps` Storage registration을 추가하고
  Python 표준 라이브러리 `FTP_TLS`로 explicit FTPS만 구현한다. CaptureNode,
  Distributor와 Web은 구체 타입을 판별하지 않는다.
- TLS: 기본값은 `ssl.create_default_context`로 certificate chain과 hostname을
  검증한다. 자체 서명 인증서는 `ca_file`로 명시적으로 신뢰한다. 물리적으로
  격리된 2-node 유선망에 한해 `verify_tls: false`를 명시할 수 있으며 이때도
  로그인 후 `PROT P`로 data channel을 보호한다.
- 저장·조회: 작업마다 독립 연결을 열며 같은 디렉터리 임시 업로드와 rename으로
  저장을 확정한다. rename 전에 `SIZE`로 전송 크기를 검증하고 읽기도 전후 크기를
  비교한다. 날짜 경로와 기존 평면 파일 호환은 DEC-019를 따른다.
- 디렉터리 생성: 저장 작업은 configured `remote_directory`를 루트부터
  재귀적으로 확인하고 누락된 경로를 생성한다. 실제 생성에 성공한 경로만 INFO로
  기록하며 조회·목록·삭제 작업은 원격 구조를 변경하지 않는다. 날짜 디렉터리
  생성은 반복적인 정상 동작이므로 별도 INFO 로그를 남기지 않는다.
- 목록: 구조화된 `MLSD` fact를 우선 사용한다. 운영 대상처럼 `MLSD`가 없는
  서버는 `NLST` 후 `SIZE`·`MDTM` 표준 명령으로 대체한다. 서버별 `LIST` 문자열
  해석은 추가하지 않는다.
- 인증: JSON에는 `password_env`만 저장한다. SFTP와 마찬가지로 실제 secret은
  `.env.local` 또는 instance `.env`에서 주입한다.
- 서버 호환: 일부 FTPS 서버가 data channel의 TLS `close_notify` 응답을
  생략하거나 연결을 바로 닫을 수 있으므로 종료 대기는 1초로 제한한다. 표준
  shutdown은 우선 시도하며 TLS record 전송, FTP 완료 응답과 원격 크기 검증은
  그대로 요구한다.
- 서버 배포: `eng/deploy-captureftp.ps1`은 IIS 설정 명령의 종료 코드를
  검증하고 DNS SAN과 IPAddress SAN을 구분한다. 자체 서명 인증서는 Linux
  `ca_file`에서 사용할 PEM으로 내보내며, 완료 전에 인증서 검증과 passive
  업로드·목록·다운로드·내용 비교·삭제를 수행한다.
- 범위: port 990 implicit FTPS, active mode와 자동 재시도는 제공하지 않는다.

## DEC-021 Storage Retention Maintenance

- 결정: Storage adapter는 단일 파일 삭제만 수행하고 대상 선정과 주기는
  `core/maintenance` 영역의 `StorageMaintenance`, `RetentionOptions`,
  `MaintenanceScheduler`가 소유한다.
- Scheduler: 촬영 Scheduler와 독립된 고정 지연 스레드로 시작 즉시
  실행하고 기본 86,400초마다 반복한다. 정책이 없으면 스레드를
  생성하지 않는다.
- 정책: target별 `max_age_days`를 명시하며 Storage가 반환한 UTC
  `modified_at`을 기준으로 경계보다 오래된 파일만 삭제한다. 기본은 빈
  정책이고 운영 예제의 local target에만 365일을 설정한다.
- 실패: 목록·삭제 예외를 대상별로 기록하고 다른 파일·Storage 처리와
  촬영 파이프라인은 계속한다. 런타임 정책 변경은 재시작을 요구한다.
- 근거: 저장 행위가 기존 데이터를 암묵적으로 삭제하지 않게 하고, 새
  Storage 구현이 촬영·보존 정책 파이프라인을 변경하지 않게 한다.

## DEC-022 Isolated Network Time Source

- 결정: 운영 CaptureNode는 gateway와 DNS가 없는 전용 `/24` 유선망에서 고정
  주소를 사용하고, 같은 link의 Windows W32Time server를 단일 NTP source로
  사용한다. Wi-Fi와 외부 fallback NTP는 사용하지 않는다.
- 서버: 상위 시계가 없는 Windows server는 `Type=NoSync`,
  `AnnounceFlags=5`, NTP server provider enabled 상태로 로컬 시계를 권위
  source로 제공한다. UDP 123 inbound는 CaptureNode 주소에만 허용한다.
- client: Pi는 `systemd-timesyncd`의 `NTP`에 server 주소를 명시하고
  `FallbackNTP`를 비운다. Windows local clock의 기본 root dispersion 약
  10초를 수용하도록 `RootDistanceMaxSec=15`를 사용하며 server dispersion을
  임의로 낮추지 않는다.
- RTC: Pi 5 내장 RTC는 UTC로 유지하고 NTP로 보정된 시스템 시각을 기록한다.
  완전 전원 차단 시각 유지는 software가 아니라 RTC battery에 의존한다.
- 검증: server 주소 표시는 성공 기준이 아니다. NTP header의 normal leap와
  유효 stratum, `Packet count >= 1`, `System clock synchronized: yes`를 함께
  확인한다.
- 운영 절차와 장애 판별은
  `documents/operations/isolated-network-time-sync.md`를 따른다.
