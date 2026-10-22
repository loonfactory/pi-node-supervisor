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
  필드는 `schemaVersion`, `eventType`, `timestamp`이며 스키마 버전은 1이다.
- Host 구성: Core는 이벤트 스키마와 발생만 소유한다. Headless와 Web은 각각의
  `logging_config.py`에서 Formatter와 Handler 구성을 독립적으로 소유한다.
  초기 Console 정책이 같더라도 공통 logging configuration을 공유하지 않으며,
  향후 DB나 host 전용 출력 정책 변경이 다른 host에 전파되지 않게 한다.
- 근거: Core가 handler나 출력 형식을 구성하지 않으면서도 Web과 사용 프로젝트가
  안정적으로 이벤트를 해석할 수 있다.
- 기각: JSON 문자열 메시지. 수신자가 다시 파싱해야 하고 일반 로그 메시지와
  구조화 필드의 경계가 약해지므로 기각했다.

## DEC-004 Image Defaults

- 상태: 설정 구조는 DEC-025로 대체됐다. 형식별 quality·compression과 EXIF
  의미는 계속 유효하다.
- 결정: Image Options는 `JpegImageOptions`(quality 기본 90)와
  `PngImageOptions`의 discriminated union으로 분리한다. PNG compression의
  초기 기본값 9는 DEC-023에서 7로 변경했다.
  Options 모델에서 `images`를 생략할 때의 기본 형식은 무손실 PNG이고
  metadata는 기본 포함한다. 최초 배포 템플릿은 DEC-030을 따른다.
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

- 결정: 실행 중에는 `captures`, `camera.params`, 이미지 형식·품질·메타데이터
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

- 상태: DEC-028의 Capture rule resource 계약으로 전체 대체됐다.
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

- 상태: 단일 사용자 Web 범위는 유지한다. 외부 API 소비자를 지원하지 않는 결정은
  Supervisor 전용 촬영 계약을 도입한 DEC-032에서 부분 대체됐다.
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
  경로를 생성한다. instance 설정은 DEC-030의 `config.example.json`
  템플릿에서 Camera 번호와 instance별 Storage 경로만 바꿔 생성한다. V4L2는 DNG 대신
  PNG를 사용한다. 이후 배포에서는 이미 생성된 운영 설정과 촬영 파일을
  교체하지 않는다.
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
- 최초 SSH: 알 수 없는 host key는 OpenSSH `accept-new`로 로컬 knownHosts에
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
- 신뢰: 지정한 `knownHosts`를 읽고 기본 reject policy를 명시적으로 사용한다.
  알 수 없는 host를 자동 등록하는 `AutoAddPolicy`는 사용하지 않는다.
- 저장: 동일 디렉터리 임시 파일을 완성한 뒤 OpenSSH POSIX rename을 우선하고,
  지원하지 않는 서버에서는 표준 SFTP rename을 사용한다. 자동 재시도는 기존
  단일 시도 정책에 따라 제공하지 않는다.
- 인증: JSON에는 `passwordEnv`와 `privateKeyPassphraseEnv` 같은 환경변수
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
  검증한다. 자체 서명 인증서는 `caFile`로 명시적으로 신뢰한다. 물리적으로
  격리된 2-node 유선망에 한해 `verifyTls: false`를 명시할 수 있으며 이때도
  로그인 후 `PROT P`로 data channel을 보호한다.
- 저장·조회: 작업마다 독립 연결을 열며 같은 디렉터리 임시 업로드와 rename으로
  저장을 확정한다. rename 전에 `SIZE`로 전송 크기를 검증하고 읽기도 전후 크기를
  비교한다. 날짜 경로와 기존 평면 파일 호환은 DEC-019를 따른다.
- 디렉터리 생성: 저장 작업은 configured `remoteDirectory`를 루트부터
  재귀적으로 확인하고 누락된 경로를 생성한다. 실제 생성에 성공한 경로만 INFO로
  기록하며 조회·목록·삭제 작업은 원격 구조를 변경하지 않는다. 날짜 디렉터리
  생성은 반복적인 정상 동작이므로 별도 INFO 로그를 남기지 않는다.
- 목록: 구조화된 `MLSD` fact를 우선 사용한다. 운영 대상처럼 `MLSD`가 없는
  서버는 `NLST` 후 `SIZE`·`MDTM` 표준 명령으로 대체한다. 서버별 `LIST` 문자열
  해석은 추가하지 않는다.
- 인증: JSON에는 `passwordEnv`만 저장한다. SFTP와 마찬가지로 실제 secret은
  `.env.local` 또는 instance `.env`에서 주입한다.
- 서버 호환: 일부 FTPS 서버가 data channel의 TLS `close_notify` 응답을
  생략하거나 연결을 바로 닫을 수 있으므로 종료 대기는 1초로 제한한다. 표준
  shutdown은 우선 시도하며 TLS record 전송, FTP 완료 응답과 원격 크기 검증은
  그대로 요구한다.
- 서버 배포: `eng/deploy-captureftp.ps1`은 IIS 설정 명령의 종료 코드를
  검증하고 DNS SAN과 IPAddress SAN을 구분한다. 자체 서명 인증서는 Linux
  `caFile`에서 사용할 PEM으로 내보내며, 완료 전에 인증서 검증과 passive
  업로드·목록·다운로드·내용 비교·삭제를 수행한다.
- 범위: port 990 implicit FTPS, active mode와 자동 재시도는 제공하지 않는다.

## DEC-021 Storage Retention Maintenance

- 상태: 설정 소유권과 Scheduler 구성은 DEC-027로 대체됐다. Adapter의 단일 파일
  삭제 경계와 실패 격리 원칙은 계속 유효하다.
- 결정: Storage adapter는 단일 파일 삭제만 수행하고 대상 선정과 주기는
  `core/maintenance` 영역의 `StorageMaintenance`, `RetentionOptions`,
  `MaintenanceScheduler`가 소유한다.
- Scheduler: 촬영 Scheduler와 독립된 고정 지연 스레드로 시작 즉시
  실행하고 기본 86,400초마다 반복한다. 정책이 없으면 스레드를
  생성하지 않는다.
- 정책: target별 `maxAgeDays`를 명시하며 Storage가 반환한 UTC
  `modifiedAt`을 기준으로 경계보다 오래된 파일만 삭제한다. 기본은 빈
  정책이고 운영 예제의 local target 보존기간은 DEC-023을 따른다.
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

## DEC-023 Picamera2 RAW Capture and Seven-Day Local Retention

- 상태: `image.raw` 설정 위치는 DEC-025의 `images.dng`로 대체됐다. 센서 촬영,
  저장 묶음과 보존 결정은 계속 유효하다.
- 결정: Picamera2 운영 프로필은 PNG Compression 7과 metadata를 유지하면서
  같은 센서 요청의 DNG를 함께 저장한다. RAW 설정은 `image.raw`라는 명시적
  옵션이며 V4L2에서는 검증 단계에서 거절한다.
- 센서 정확성: 임의의 12-bit container로 확장하지 않고 센서가 제공하는 전체
  해상도 최고 비트 깊이를 사용한다. 현재 IMX708과 IMX219는 10-bit다.
- 촬영 수명주기: RAW가 활성화된 촬영만 still configuration으로 전환한다.
  `switch_mode_capture_request_and_stop`으로 요청을 얻고 DNG 생성과 request release
  후 기존 preview configuration을 다시 시작해 장기 운용의 CMA fragmentation
  위험을 줄인다.
- 저장 묶음: 처리 영상과 DNG는 같은 UTC timestamp와 capture ID를 사용하며
  Distributor의 단일 bounded 작업에서 모든 대상에 전달한다. 개별 Storage가
  다중 파일 transaction을 제공하지 않으므로 부분 저장을 rollback하지는 않되,
  모든 산출물 시도가 끝날 때까지 촬영을 terminal state로 전환하지 않는다.
- metadata: PNG/JPEG EXIF `UserComment`에는 capture ID와 기존 카메라 metadata를
  포함한다. DNG metadata는 Picamera2의 센서 요청 metadata를 source-of-truth로
  사용하고 두 파일의 동일 stem으로 촬영 묶음을 식별한다.
- 보존: 운영 local Storage는 7일을 유지하며 원격 Storage에는 자동 삭제 정책을
  넣지 않는다. 보존 판단은 기존 StorageMaintenance의 파일별 modified time을
  사용한다.
- 기본값: PNG compression 7 결정은 형식을 PNG로 선택할 때 유효하다.
  최초 배포 템플릿은 DEC-030이 정하며 기존 운영 설정은 배포 중
  덮어쓰지 않는다.
- 기각: PNG만 저장하는 방식은 ISP 이전 Bayer data와 더 넓은 계조를 복원할 수
  없어 정량 분석 원본으로 부족하다. RAW만 저장하는 방식은 현장 확인과 일반
  도구 호환성이 떨어져 처리 영상과 RAW 병행을 채택했다.

## DEC-024 System-Local Daily Schedule

- 결정: timezone을 설정, API와 Web 상태에서 제거하고 매일 wall-clock
  Capture rule의 시각을 프로세스가 실행되는 OS local time으로 해석한다. 시스템
  timezone의 구성과 정확성은 설치자 책임이며 CaptureNode와 배포 스크립트는
  특정 timezone을 설정하거나 검증하지 않는다.
- 호환성: 기존 `capture` resource와 `capture.timezone`은 자동 제거하거나 변환하지 않는다. 설정 로드,
  API 제출과 배포 검증에서 strict options 오류로 거절하고 원본 파일을 수정하지
  않는다.
- UTC 경계: 촬영 파일명, 저장 날짜 layout, event timestamp, RTC와 metadata의
  절대 시각은 기존처럼 UTC를 유지한다. OS local time은 매일 trigger의 wall
  clock 해석에만 사용한다.
- 근거: 배포 환경의 timezone을 설정과 중복 저장하지 않고 host를 단일
  source-of-truth로 둔다. 장비의 현지 시각 정책은 설치 영역에 속하며 schedule
  설정은 촬영할 wall-clock 시각만 표현한다.
- 대체: DEC-007의 IANA timezone 영속화와 host timezone 변경 격리 결정을
  대체한다. 스케줄 자료 구조는 DEC-028이 대체한다.

## DEC-025 Extension-Keyed ImageFormat Resources

- 결정: 단일 `image` discriminated resource와 중첩 `raw`를 확장자 key 기반
  `images` object로 대체한다. 허용 key는 `png`, `jpeg`, `dng`이고 key가
  존재하면 해당 형식을 생성한다. 최소 하나가 필요하며 여러 형식을 동시에
  활성화할 수 있다.
- 출력 식별: 같은 촬영의 모든 출력은 동일한 UTC timestamp와 capture ID stem을
  사용하고 `.png`, `.jpeg`, `.dng` 확장자로만 구분한다. 별도 `type`, `format`,
  이름이나 suffix 속성은 두지 않는다.
- 형식별 정책: PNG는 `compression`과 `embedMetadata`, JPEG는 `quality`와
  `embedMetadata`, DNG는 `embedMetadata`를 가진다. DNG의 field는 향후
  `camera.calibration` 같은 CaptureNode 사용자 정의 metadata 주입을 제어한다. 현재
  Picamera2 native DNG metadata는 항상 보존하고 사용자 정의 주입은 하지 않는다.
- 호환성: 기존 `image`, `image.format`, `image.raw` resource를 자동 변환하지
  않는다. 설정 로드, API 제출과 배포 검증에서 strict options 오류로 거절하고
  원본 파일을 수정하지 않는다.
- 근거: object key가 형식과 확장자의 단일 source-of-truth가 되어 중복 형식을
  구조적으로 방지하고, runtime lookup과 설정 참조를 단순화한다. 현재 요구에는
  형식당 정책이 하나뿐이므로 배열과 별도 discriminator가 불필요하다.
- 기각: `[{"type": "png", ...}]` 배열은 같은 형식을 여러 profile로 생성할 때는
  적합하지만 현재 계약에서는 중복 검증과 식별 이름이 추가로 필요해 채택하지
  않았다.

## DEC-026 Camera-Scoped Calibration Metadata

- 결정: `calibration`은 Picamera2와 V4L2 Camera resource가 각각 소유하는 자유
  형식 JSON object다. sensor, lens와 기하 특성이 카메라마다 다르므로
  `camera.calibration`을 해당 장치 설정과 함께 저장한다.
- 런타임: calibration metadata만 바뀐 설정은 카메라 장치 변경으로 취급하지
  않고 재연결 없이 영속화한다. CaptureNode는 값을 보존할 뿐 내부 schema를
  검증하거나 이미지 보정을 수행하지 않는다.
- metadata: `images.dng.embedMetadata`가 활성화되면 향후 이 값을 DNG 사용자
  정의 metadata로 기록할 수 있다. 현재 Picamera2 native DNG metadata는 그대로
  유지하며 calibration 주입은 구현하지 않는다.
- 근거: calibration과 적용 대상을 같은 resource에 두어 여러 camera instance의
  보정값이 공용 상태로 섞이는 것을 방지한다.

## DEC-027 Storage-Scoped Maintenance Resources

- 결정: 각 File, SFTP, FTPS options가 선택적인 `maintenance` resource를 직접
  소유한다. 이 resource는 해당 Storage의 `intervalSeconds`와
  `maxAgeDays`만 가지며 별도 target field나 retention 배열을 두지 않는다.
- 실행: Maintenance가 설정된 Storage마다 독립된 `MaintenanceScheduler`를 만들고
  시작 즉시 한 번 실행한 뒤 각자의 주기로 반복한다. Storage adapter는 기존처럼
  단일 파일 삭제만 수행하고 선정과 오류 격리는 `StorageMaintenance`가 담당한다.
- 기본값: `maintenance`를 생략하면 해당 Storage에는 자동 삭제를 적용하지 않는다.
  신규 운영 local Storage만 86,400초 점검과 7일 보존을 명시한다.
- 런타임: Maintenance는 Storage resource의 일부이므로 변경하려면 설정 파일을
  수정하고 프로세스를 재시작한다.
- 근거: 보존 정책과 적용 Storage를 같은 resource에 두어 이름 기반 cross-reference,
  존재 검증과 중복 target 상태를 제거하고 Storage별 서로 다른 주기를 자연스럽게
  표현한다.

## DEC-028 Daily Capture Rule Resources

- 상태: wire 속성 이름과 daily type 식별자는 DEC-033이 대체한다. 나머지 반복,
  중첩과 timezone 결정은 유지한다.
- 결정: 단일 `capture` mode resource를 `captures` 배열로 대체한다. 각 항목은
  `type: "daily"`와 `time`, 또는 `type: "interval"`과 양의 `interval`을
  가진다. Interval의 `startTime`과 `endTime`은 둘 다 있거나 둘 다 없다.
- 반복 의미: `daily`는 지정한 OS local wall-clock 시각에 매일 한 번
  실행한다. 시간 구간 interval도 매일 반복하며 시작과, 간격에 맞는
  종료 시각을 포함한다. 날짜를 지정하는 촬영 규칙은 제공하지 않는다.
- 계속 interval: 시간 구간을 생략하면 실행 시점부터 monotonic clock
  기준으로 계속 반복해 wall-clock 보정의 영향을 받지 않는다.
- 중첩: 서로 다른 규칙이 정확히 같은 시각에 due가 되면 스케줄러는
  하나의 capture tick으로 합친다. 동일한 규칙 자체를 중복 저장하는 것은 거절한다.
- 빈 배열: `captures: []`는 자동 촬영 없이 Preview와 즉시 촬영만
  유지한다. 별도 disabled mode를 두지 않는다.
- 이름: Capture resource wire contract는 `startTime`, `endTime` camelCase를
  사용한다. 전체 JSON naming 계약은 DEC-033을 따른다.
- Camera parameter: 촬영 규칙과 카메라 control을 분리하기 위해 기존
  `capture.params`의 기능은 `camera.params`로 이동한다.
- 호환성: 기존 `capture.mode`, `intervalSeconds`, `daily_times`, `params`를
  변환·보존·거절 전용 코드로 다루지 않는다. 현재 strict `Options`에 없는
  속성이므로 일반 알 수 없는 속성과 동일하게 검증 실패한다.
- 대체: DEC-007의 mode 계약과 DEC-024의 기존 Daily 자료 구조를
  대체하며 DEC-024의 OS-local timezone 소유권 결정은 유지한다.

## DEC-029 Picamera2 Maximum Resolution Default

- 결정: Picamera2 `width`와 `height`를 둘 다 생략하거나 `null`로 지정하면
  카메라가 보고한 `sensor_resolution`을 main stream 크기로 사용한다. 하나만
  지정하는 설정은 계속 거절한다.
- 적용 범위: 처리 이미지 촬영과 이들이 공유하는 Preview main stream에
  적용한다. DNG raw 촬영은 기존처럼 full sensor output의 최고 bit depth
  mode를 사용한다.
- 탐색·배포: 새 Picamera2 장치 탐색 결과와 최초 배포 설정은 고정
  `1920×1080`을 넣지 않고 두 속성을 `null`로 두어 이 규칙을 사용한다.
- V4L2: frame size 목록이 pixel format과 장치마다 다르고 현재 지속 MJPEG
  pipeline이 해상도를 명시하므로 자동 최대 선택을 적용하지 않는다.
- 근거: Picamera2 library의 암묵적 preview 기본값 대신 hardware가 보고한
  sensor resolution을 명시적 source-of-truth로 사용해 설정 생략의 의미를
  예측 가능하게 만든다.

## DEC-030 Example-Based Initial Deployment

- 상태: 최초 배포 template의 예약 촬영과 File Storage 구성은 DEC-032로
  대체됐다. 설정 파일이 없을 때 example을 source-of-truth로 사용하는 결정은
  유지한다.
- 결정: `config.example.json`을 설정 파일이 없는 instance의 최초 배포
  템플릿으로 사용한다. 빈 root object와 개별 속성 생략은 계속 Options
  모델 자체의 기본값을 사용하며 예제 파일과 같을 필요가 없다.
- 템플릿 구성: Picamera2 camera 0, `width`/`height` 자동 최대 해상도,
  00:00:00~23:59:59의 1800초 interval, metadata를 포함한 DNG,
  `./captures`의 daily File Storage와 86,400초 점검·7일 보존을 사용한다.
- 단일 template: 신규 배포용 일정·이미지·보존 값을 배포 코드에 별도로
  정의하지 않고 배포된 `config.example.json`을 읽어 가져온다. Camera 번호와
  instance별 Storage 경로만 바꾸며, V4L2는 DNG 비지원 때문에 PNG로 대체한다.
- 검증: 배포는 템플릿을 `Options` 모델로 검증한 후 instance 설정을
  생성한다.

## DEC-031 React Frontend Responsibility Split

- 결정: React entry point `main.jsx`는 `createRoot`, `StrictMode`, `App`와 stylesheet
  import만 소유한다. 최상위 화면 조립은 `App.jsx`, HTTP boundary는
  `api/captureNodeApi.js`, 순수 표시 변환은 `lib/presentation.js`로 분리한다.
- Hook: `useRuntimeStatus`, `useCaptureSettings`, `useActivityFeed`, `useManualCapture`,
  `useStorageBrowser`가 각 기능의 상태와 workflow를 소유한다.
  `useCaptureNode`는 이 hook들을 호출하고 component에 전달할 화면 model을
  조합하는 역할만 담당한다.
- 컴포넌트: Preview, Settings, Events, Storage modal을 운영 화면 영역별로
  나누고 Settings는 Camera device, Image policy, Capture schedule, Camera parameter
  editor로 한 번 더 분해한다.
- 참고: `temp/downcap-light-main/reactapp`의 entry point, App, API, component, area/lib
  분리 원칙을 참고하되 CaptureNode는 단일 운영 화면이므로 router,
  page lazy loading, context provider와 전역 store는 도입하지 않는다.
- 근거: API polling, 설정 편집, 표시 utility와 큰 JSX tree를 하나의
  파일에 두면 변경 영향과 재사용 경계를 파악하기 어렵다. 기능 경계를
  파일 경계와 맞추되 현재 규모에 필요하지 않은 framework는 추가하지 않는다.

## DEC-032 Supervisor-Owned Scheduling and Streamed Capture Bundle

- 결정: Supervisor 운영 profile에서는 CaptureNode가 카메라 설정, 단일 촬영,
  JPEG/PNG/DNG 인코딩과 HTTP 응답 스트리밍만 담당한다. 예약 실행, file/SFTP/FTPS
  저장, 재시도, 저장 이력과 power-off readiness는 Supervisor가 소유한다.
- 호환성: Scheduler, Distributor와 Storage 구현 코드는 기존 profile을 위해
  유지한다. Supervisor profile의 설정은 `captures`와 `storages`를 생략하며,
  Options의 빈 기본값으로 해석한다. repository는 두 값이 비어 있으면 정규화
  저장에서도 속성을 생략한다.
- API: `POST /api/captures`가 `requestId`와 요청별 camera parameters를 받고
  활성 ImageFormat을 PNG, JPEG, DNG 순서의 `multipart/mixed`로 반환한다.
  응답과 각 part는 capture ID를 제공하고 각 part는 길이와 base64 SHA-256
  digest를 포함한다.
- 요청별 parameter: configured `camera.params`에 요청 값을 병합해 촬영 전에
  적용하고 센서 촬영 직후 configured 값을 복구한다. 적용 실패는 422, 촬영 또는
  복구 실패는 capture failure로 다룬다. 요청 값은 영속 설정을 변경하지 않는다.
- 설정 일관성: reservation 직후 immutable Options snapshot을 얻고 camera parameter
  기준값, RAW 요청 여부와 artifact encoding 전체에 동일하게 사용한다. 동시
  `/api/config` 변경은 다음 capture부터 적용하고 진행 중 bundle을 바꾸지 않는다.
- 수명주기: 촬영 예약은 capture와 encoding뿐 아니라 response iterator가 정상
  종료되거나 취소될 때까지 유지한다. 따라서 두 capture bundle이 메모리와 camera를
  동시에 소유하지 않는다. 취소 경로의 `finally`에서 reservation을 해제한다.
- 메모리: 현재 Camera와 ImageEncoder 계약이 bytes를 반환하므로 artifact 자체는
  메모리에 존재한다. multipart/ZIP 전체를 추가 byte sequence로 조립하지 않고
  part header와 기존 artifact bytes를 차례로 yield해 추가 bundle 복사를 피한다.
- 전달 실패: CaptureNode는 spool과 재전송을 제공하지 않는다. Supervisor는 종료
  boundary, artifact 수, filename, 길이, digest와 capture ID를 모두 검증한 뒤에만
  로컬 수신 성공으로 기록하고, 불완전한 응답은 새 촬영 요청이 필요한 실패로
  처리한다.
- 기각: CaptureNode 내부의 영구 spool과 원격 upload worker는 느린 외부 저장과
  촬영을 분리하지만 예약·재시도·종료 준비 책임을 두 프로세스에 중복시킨다.
  전체 응답 ZIP 조립은 큰 DNG bundle의 추가 메모리 복사를 유발하므로 채택하지
  않았다.

## DEC-033 Strict camelCase JSON Contract and Daily Capture Type

- 결정: CaptureNode가 소유하는 설정, HTTP request/response와 event document의
  모든 JSON 속성 이름은 `camelCase`로 직렬화하고 검증한다. `snake_case` alias나
  입력 변환은 제공하지 않으며 알 수 없는 속성과 동일하게 검증 실패한다.
- 적용 경계: Pydantic resource, 수동으로 구성하는 API response, event envelope와
  camera metadata wrapper에 동일하게 적용한다. HTTP header 이름, Python 내부
  identifier, camera control 이름과 외부 library option 이름은 JSON 속성 계약이
  아니므로 변경하지 않는다.
- 사용자 정의 object: `camera.params`와 `camera.calibration` 내부 key는 연결된
  camera 구현이 소유하므로 CaptureNode가 이름을 변환하지 않는다.
- Daily rule: 매일 지정 시각에 실행하는 Capture resource의 식별자를
  `type: "once"`에서 `type: "daily"`로 바꾼다. `once` 입력 호환은 제공하지
  않는다. 실행 의미와 내부 scheduler 구현은 DEC-028의 daily 반복 계약을
  유지한다.
- 테스트: 단순히 속성 이름이나 문서 문자열의 존재만 확인하는 테스트는 두지
  않는다. 유효한 camelCase document의 동작과 기존 이름을 포함한 잘못된 입력의
  거절처럼 계약의 실제 동작을 검증한다.
- 근거: 동일한 public naming 규칙으로 Supervisor와 Web client의 변환 분기를
  제거하고, 호환 alias 때문에 오래된 계약이 조용히 계속 사용되는 것을 막는다.

## DEC-034 Request-Scoped Capture Formats

- 결정: `POST /api/captures`의 `requestId`만 필수로 두고 `parameters`와
  `formats`는 선택 속성으로 제공한다. 두 속성은 해당 촬영에만 적용하며 영속
  설정을 변경하지 않는다.
- 생략 의미: `parameters`를 생략하면 임시 control 적용 없이 현재 camera 상태로
  촬영한다. `formats`를 생략하거나 `null`로 보내면 immutable Options snapshot의
  `images`를 사용한다.
- Format override: `formats`가 있으면 설정의 `images`와 병합하지 않고 요청
  object에 포함된 PNG, JPEG, DNG만 생성한다. 각 resource는 설정과 같은 quality,
  compression과 metadata 옵션을 검증한다. 따라서 요청에서 DNG를 제외하면 raw
  capture도 요청하지 않는다.
- 일관성: 요청 format은 촬영 예약 시 얻은 설정 snapshot과 함께 고정한다.
  DNG 가능 여부, camera raw 요청과 artifact encoding이 동일한 format policy를
  사용하며 진행 중인 설정 변경의 영향을 받지 않는다.
- 근거: Supervisor가 촬영 목적별 산출물을 선택할 수 있게 하면서도 기본 요청은
  장치에 구성된 정책을 그대로 사용하고 CaptureNode 설정의 영속성을 유지한다.

## DEC-035 Idempotent Supervisor-managed Deployment

- 결정: 배포 가능한 Python/Web/package/systemd 입력으로 결정적 SHA-256 source ID를
  만들고, 성공한 원격 activation 뒤에만
  `~/capture-node/.deployed-source.sha256`로 게시한다.
- 생략 조건: `--skip-if-current`에서 원격 marker와 로컬 source ID가 같을 때는
  test, frontend build, 전송, service 변경과 activation을 모두 생략한다. marker가
  없거나 다르면 전체 배포를 실행한다.
- Supervisor profile: `--supervisor-managed`는 source와 runtime dependency를
  설치·검증하지만 standalone instance 설정과 systemd service를 만들거나 시작하지
  않는다. 설정 생성과 child process lifecycle은 Supervisor activation이 소유한다.
- 실패 원자성: source ID는 runtime import 검증 뒤 기록하며 activation 실패 시
  이전 source를 복구하고 기존 marker를 유지한다. 따라서 marker는 전송 시도가
  아니라 마지막 성공 deployment를 나타낸다.
- 근거: Supervisor와 CaptureNode를 별도 명령으로 갱신하면 동일한 package version
  아래에서도 설정 contract가 어긋날 수 있다. source identity와 단일 orchestration
  entry point로 이 drift를 막되 변경이 없는 반복 배포 비용은 제거한다.

## DEC-036 Continuous Software Synchronization and Timed Capture

- 결정: Picamera2 `camera.synchronization`은 server/client 역할과 공통 frameRate를
  받아 libcamera SyncMode를 설정한다. Supervisor의 다중 카메라는 항상 이 설정을
  생성한다. standalone 단일 카메라는 기존 설정을 유지한다.
- DNG: 동기화 중에는 전체 센서 RAW와 main을 계속 구동하며 같은 CompletedRequest에서
  둘을 읽는다. 모드 전환 촬영은 프레임 위상과 SyncReady를 리셋하므로 사용하지 않는다.
  메모리 사용량을 줄이기 위해 버퍼는 3개이고 프레임 캐시는 사용하지 않는다.
- 경계: Core는 optional TimedCamera capability로 목표 시각 촬영을 요청한다.
  Picamera2 callback은 SyncReady와 SensorTimestamp를 만족하는 요청 하나만 acquire하고,
  대기자에게 소유권을 넘긴다. callback에서 인코딩하거나 블록하지 않는다. timeout과
  close 뒤의 늦은 프레임은 참조를 보유하지 않는다.
- 계약: `POST /api/captures`의 선택 `captureAtNs`는 같은 호스트 CLOCK_BOOTTIME ns다.
  목표는 미래 30초 이내여야 하며 첫 프레임이 목표에서 한 주기 이상 늦으면 실패한다.
  sensor timestamp와 SyncReady는 응답 헤더로 전달한다. `capturedAt`은 기존 의미를
  유지하므로 스테레오 시간 차이 계산에 사용하지 않는다.
- 기각: HTTP 요청을 동시에 보내는 것만으로는 센서 노출 시점이 같아지지 않는다.
  무한 capture_sync_request 대기는 준비 실패나 분리 시 종료를 막고, 요청 timeout 후
  완료된 요청의 참조 회수가 어려워 callback의 명시적 소유권 전달을 선택했다.
- 제약: SyncReady는 초기 준비 신호이며 지속적인 정확도 보증이 아니다. Supervisor가
  실제 타임스탬프 차이와 누락을 별도로 기록한다. 혼합 센서, 노출 시간, rolling shutter
  차이와 다른 호스트의 시계는 이 기능만으로 해결하지 않는다.


## DEC-037 Lossless Preview Default and Per-Request Negotiation

- 결정: 기본 프리뷰 인코딩을 PNG로 변경하고, 요청별 format 또는 Accept로 JPEG를
  선택하게 한다. 같은 카메라의 서로 다른 뷰어가 독립적으로 선택할 수 있으므로
  카메라 전역 params를 변경하거나 재시작하지 않는다.
- 경계: Web이 협상하고 FrameSource의 PreviewFormat을 Manager와 어댑터에 전달한다.
  공통 인코더는 픽셀과 선택된 형식만 받는다. Supervisor는 요청과 응답을 전달하며
  인코딩이나 협상 정책을 복제하지 않는다.
- 기각: Supervisor에서 JPEG를 PNG로 재인코딩하면 손실을 복구할 수 없고 추가 CPU만
  사용한다. Picamera2의 main 픽셀에서 직접 인코딩한다. V4L2는 기존 MJPEG 입력의
  제약을 명시한다. PNG는 센서 RAW를 의미하지 않는다.
- 비용: PNG 압축 레벨 1은 무손실을 유지하면서 CPU 비용을 줄이지만 JPEG보다 큰
  데이터와 낮은 실효 FPS가 가능하다. 프레임은 최대 1 MiB 조각으로 전달해 기존
  프록시의 단일 HTTP chunk 상한을 넘지 않도록 한다.
- 기존 JPEG-only 설명은 이 결정으로 대체한다. 브라우저의 고정 img URL,
  multipart 컨테이너, 인증과 previewQuality의 JPEG 의미는 유지한다.


## DEC-038 Compact Calibration in Capture Artifacts

- 결정: Core가 촬영 시점 옵션 snapshot에서 보정값과 요청 설정을 추출한다. 보정
  객체는 화이트리스트로 투영해 출처/오차/샘플 기록을 저장 이미지에 복제하지 않는다.
  배열을 깊은 복사하여 이후 설정 변경이 과거 촬영 메타데이터에 영향을 주지 않는다.
- 모델: 각 카메라 설정에 K/D와 공통 장치 좌표계에서 해당 카메라로 가는 R/T를 직접
  둔다. 파일에는 보정 수치와 해상도만 복사하며 카메라 식별자/모델/변환 설명은
  반복하지 않는다. 왜곡 계수는 k1/k2/p1/p2/k3 이름 객체다. OpenCV pinhole과
  X_camera = R @ X_rig + T_mm 규칙은 저장 계약으로 고정한다. baseline/center도
  파생값이라 중복 저장하지 않는다. 촬영 묶음/경로에서 카메라 대응을 유지해야 한다.
- 설정: Supervisor defaults에 보정을 두면 모든 카메라에 잘못 상속될 수 있어 거부한다.
  사용자 요청으로 공용 config.json에는 NoIR/RGB 실측값을 사용 예시로 제공하되,
  다른 장치에서는 교체해야 함을 명시한다. 실제 장치 프로필은 Git에서 제외해 별도로 보관한다.
- 경계: camera adapter는 원본 센서 메타데이터/RAW를 제공한다. Core가 설정 정보를
  결합하고 encoding 계층이 파일 표현을 담당한다. 관측 controls와 설정 controls는
  별도로 남긴다. 이 기능은 보정 적용/삼각측량 기능이 아니다.
- DNG: 기본 산출물이 DNG이므로 JPEG/PNG에만 넣거나 별도 JSON 파일에 의존하지 않는다.
  기존 RAW 바이트를 디코딩/재인코딩하지 않고 classic TIFF의 IFD0을 append해
  ImageDescription JSON을 추가한다. 기존 strip/tile/SubIFD offsets와 pixel bytes는
  바꾸지 않는다. 메타데이터 비활성화는 기존 DNG 반환이며 필수 DNG 태그 제거가 아니다.
- 제약: 이 경로는 현재 Picamera2/PiDNG classic TIFF 산출물 대상이다. BigTIFF나 잘못된
  원본을 조용히 통과시키지 않는다. 새 RAW writer 도입 시 독립 reader로 검증해야 한다.
