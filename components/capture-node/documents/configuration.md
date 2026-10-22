# CaptureNode Configuration Reference

CaptureNode가 읽는 JSON 설정 파일의 전체 스키마 참조다. 실행 가능한 기본 예제는
[`config.example.json`](../config.example.json)이며, 실제 검증 기준은
`capture_node.core.options.Options`와 각 기능 영역의 Options 모델이다.

| 항목 | 값 |
| --- | --- |
| 적용 버전 | CaptureNode v0.1.0 |
| 형식 | UTF-8 JSON object |
| 최상위 리소스 | `Options` |
| 알 수 없는 속성 | 거절 |
| 속성 이름 | 모든 CaptureNode 소유 속성에 `camelCase` 적용 |

`snake_case` 속성의 입력 호환은 제공하지 않는다. `camera.params`와
`camera.calibration`처럼 외부 카메라 구현에 전달하는 사용자 정의 object의 내부
key는 CaptureNode가 이름을 변환하지 않는다.

`config.example.json`은 설정 파일이 없는 최초 배포에서 사용하는 기본
템플릿이다. Picamera2와 metadata를 포함한 JPEG·DNG를 구성하고 예약 촬영과
Storage는 Supervisor가 소유하도록 `captures`와 `storages`를 생략한다. 이는 빈
root object나 개별 속성 생략 시 적용되는 Options 모델 기본값과는 별개다.

## 목차

- [Options 리소스](#options-리소스)
- [Camera 리소스](#camera-리소스)
- [Capture 리소스](#capture-리소스)
- [ImageFormat 리소스](#imageformat-리소스)
- [Storage 리소스](#storage-리소스)
- [Maintenance 리소스](#maintenance-리소스)
- [Calibration 리소스](#calibration-리소스)
- [교차 속성 검증](#교차-속성-검증)
- [시스템 시각](#시스템-시각)
- [Secret과 경로](#secret과-경로)
- [실행 중 변경](#실행-중-변경)
- [설정 검증](#설정-검증)

## 스키마 표기

| 표기 | 의미 |
| --- | --- |
| 필수 | 속성을 반드시 지정해야 한다. |
| 조건부 | 다른 속성이나 선택한 타입에 따라 필요하다. |
| 선택 | 생략하면 표에 표시된 기본값을 사용한다. |

`number`는 정수와 실수를 모두 허용하는 JSON 숫자이고, `integer`는 정수만
허용한다. `리소스 표현`의 `string`, `integer`, `Resource` 같은 값은 구조를
설명하는 타입 자리표시자이므로 실행 가능한 JSON이 아니다. `예제`의 `json`
코드 블록만 복사해 사용할 수 있다. JSON은 주석을 지원하지 않으며 속성 표가
필수 여부, 기본값과 제약을 포함한 전체 계약이다.

## Options 리소스

하나의 CaptureNode 프로세스가 사용하는 최상위 설정 리소스다.

### 리소스 표현

```
{
  "camera": Camera Resource,
  "captures": [
    Capture Resource
  ],
  "images": {
    "png": PNG ImageFormat Resource,
    "jpeg": JPEG ImageFormat Resource,
    "dng": DNG ImageFormat Resource
  },
  "storages": [
    Storage Resource
  ]
}
```

### 최소 실행 예제

```json
{
  "captures": [
    {
      "type": "interval",
      "interval": 60
    }
  ]
}
```

이 표현은 Picamera2 camera 0과 빈 camera calibration/parameter, PNG compression 7,
EXIF metadata 포함, DNG 비활성, Storage 없음과 보존 정책 없음을 모델
기본값으로 사용한다.

### 속성

| 속성 | 타입 | 필수 여부 | 기본값 | 설명 |
| --- | --- | --- | --- | --- |
| `camera` | [Camera](#camera-리소스) | 선택 | Picamera2 기본값 | 사용할 CSI 또는 USB/UVC camera다. |
| `captures` | [Capture](#capture-리소스) 배열 | 선택 | `[]` | 매일 반복할 자동 촬영 규칙들이다. 생략하거나 빈 배열이면 Preview와 API 촬영만 유지한다. |
| `images` | [ImageFormat](#imageformat-리소스) object | 선택 | PNG 기본값 | 확장자별 이미지 생성 정책이다. |
| `storages` | [Storage](#storage-리소스) 배열 | 선택 | `[]` | 저장 대상 목록이다. 생략하거나 빈 배열이면 파일을 저장하지 않는다. |

`schemaVersion`, `timezone`, `policies`, `capture`, `captureSchedules`는 현재 CaptureNode
`Options` 속성이 아니므로 지정하면 검증 오류가 발생한다.

## Camera 리소스

`camera.type`으로 구현을 선택한다. `camera` 자체를 생략하면 Picamera2 기본값을
사용하지만, `camera` object를 지정할 때는 `type`을 반드시 포함해야 한다.

| `type` | 용도 |
| --- | --- |
| `picamera2` | Raspberry Pi CSI camera |
| `v4l2` | USB/UVC V4L2 camera |

### Picamera2

#### 리소스 표현

```
{
  "type": "picamera2",
  "cameraNum": integer,
  "width": integer | null,
  "height": integer | null,
  "previewFps": number,
  "previewQuality": integer,
  "calibration": Calibration Resource,
  "params": {
    string: any
  }
}
```

#### 예제

```json
{
  "type": "picamera2",
  "cameraNum": 0,
  "width": 1920,
  "height": 1080,
  "previewFps": 10,
  "previewQuality": 75,
  "calibration": {},
  "params": {}
}
```

| 속성 | 타입 | 필수 여부 | 기본값 | 허용값과 설명 |
| --- | --- | --- | --- | --- |
| `type` | string | 필수 | 없음 | `picamera2` |
| `cameraNum` | integer | 선택 | `0` | `0` 이상의 camera index다. |
| `width` | integer 또는 `null` | 조건부 | `null` | 양의 정수다. `height`와 함께 지정하거나 둘 다 생략/`null`이어야 한다. 둘 다 없으면 센서 최대 너비를 사용한다. |
| `height` | integer 또는 `null` | 조건부 | `null` | 양의 정수다. `width`와 함께 지정하거나 둘 다 생략/`null`이어야 한다. 둘 다 없으면 센서 최대 높이를 사용한다. |
| `previewFps` | number | 선택 | `10` | `0`보다 큰 Preview frame rate다. |
| `previewQuality` | integer | 선택 | `75` | `1`부터 `100`까지의 Preview JPEG quality다. |
| `calibration` | [Calibration](#calibration-리소스) | 선택 | `{}` | 이 카메라의 calibration metadata다. |
| `params` | object | 선택 | `{}` | 이 카메라에 촬영 직전 적용할 control 이름과 값이다. |

`width`와 `height`가 모두 생략되었거나 `null`이면 CaptureNode가
Picamera2의 `sensor_resolution`을 조회해 처리 이미지와 Preview main stream에
최대 센서 해상도를 명시한다. 최대 해상도는 메모리, 인코딩 시간과 Preview
대역폭을 늘릴 수 있다. 특정 크기가 필요하면 두 값을 함께 지정한다.

V4L2의 지원 해상도는 장치 및 pixel format별로 다르므로 이 자동 최대
선택 규칙을 적용하지 않는다. V4L2에서 두 속성을 생략하면 기본
`1920×1080`을 사용한다.

### V4L2

#### 리소스 표현

```
{
  "type": "v4l2",
  "device": string,
  "width": integer,
  "height": integer,
  "frameRate": number,
  "startupTimeoutSeconds": number,
  "calibration": Calibration Resource,
  "params": {
    string: any
  }
}
```

#### 예제

```json
{
  "type": "v4l2",
  "device": "/dev/video0",
  "width": 1920,
  "height": 1080,
  "frameRate": 24,
  "startupTimeoutSeconds": 10,
  "calibration": {},
  "params": {}
}
```

| 속성 | 타입 | 필수 여부 | 기본값 | 허용값과 설명 |
| --- | --- | --- | --- | --- |
| `type` | string | 필수 | 없음 | `v4l2` |
| `device` | string | 선택 | `/dev/video0` | V4L2 장치 경로다. 재부팅 후에도 안정적인 `/dev/v4l/by-id/...` 경로를 권장한다. |
| `width` | integer | 선택 | `1920` | `0`보다 큰 capture width다. |
| `height` | integer | 선택 | `1080` | `0`보다 큰 capture height다. |
| `frameRate` | number | 선택 | `24` | `0`보다 큰 capture/Preview FPS다. |
| `startupTimeoutSeconds` | number | 선택 | `10` | `0`보다 큰 FFmpeg stream 시작 제한 시간이다. |
| `calibration` | [Calibration](#calibration-리소스) | 선택 | `{}` | 이 카메라의 calibration metadata다. |
| `params` | object | 선택 | `{}` | 이 카메라에 촬영 직전 적용할 control 이름과 값이다. |

V4L2 adapter에는 FFmpeg가 필요하다. `v4l2-ctl`은 장치 탐색과 control 조회·적용에
사용하며, 없어도 설정된 장치의 촬영과 Preview는 계속 사용할 수 있다.

## Capture 리소스

최상위 `captures`는 0개 이상의 Capture resource 배열이다. 모든 규칙은
날짜를 지정하지 않고 매일 반복된다. 동일한 시각에 여러 규칙이 만나면
촬영은 한 번만 실행한다.

### Daily Capture 리소스

```
{
  "type": "daily",
  "time": string
}
```

| 속성 | 타입 | 필수 여부 | 기본값 | 허용값과 설명 |
| --- | --- | --- | --- | --- |
| `type` | string | 필수 | 없음 | `daily` |
| `time` | string | 필수 | 없음 | OS local time으로 해석할 `HH:MM:SS` 시각이다. UTC offset을 포함하지 않는다. |

`daily`는 전체 운영 기간 중 한 번이 아니라, 지정한 wall-clock 시각에
매일 한 번 촬영한다.

#### 예제

```json
{
  "type": "daily",
  "time": "12:00:00"
}
```

### Interval Capture 리소스

`startTime`과 `endTime`을 모두 생략하면 실행 시점부터 일정 간격으로
계속 촬영한다. 두 속성을 모두 지정하면 그 시간 구간에서만 매일
반복한다.

#### 리소스 표현

```
{
  "type": "interval",
  "startTime": string,
  "endTime": string,
  "interval": number
}
```

| 속성 | 타입 | 필수 여부 | 기본값 | 허용값과 설명 |
| --- | --- | --- | --- | --- |
| `type` | string | 필수 | 없음 | `interval` |
| `startTime` | string | 조건부 | 없음 | 매일 구간의 첫 촬영 시각이다. `endTime`과 함께 지정한다. |
| `endTime` | string | 조건부 | 없음 | 매일 구간의 마지막 허용 시각이다. `startTime`보다 빠를 수 없다. |
| `interval` | number | 필수 | 없음 | `0`보다 큰 초 단위 간격이다. |

`startTime`과 `endTime`은 둘 다 지정하거나 둘 다 생략해야 한다.
시간 구간의 시작과, 간격에 정확히 맞는 경우의 종료 시각은 포함한다.
자정을 넘는 구간은 지원하지 않으며 다음 날에 다시 시작한다.

#### 매일 시간 구간 예제

```json
{
  "type": "interval",
  "startTime": "12:00:00",
  "endTime": "23:00:00",
  "interval": 60
}
```

#### 계속 간격 예제

```json
{
  "type": "interval",
  "interval": 60
}
```

### Camera parameters

`camera.params`는 연결된 camera가 런타임에 제공하는 동적 스키마다. 따라서 모든 장치에
공통인 고정 속성 목록은 없다. Web의 camera controls endpoint가 현재 장치의
이름, 타입, 최소값, 최대값, step, menu 항목과 inactive 상태를 반환한다.

Picamera2와 V4L2는 `horizontal_flip`, `vertical_flip` boolean을 지원한다.
V4L2에서 같은 이름의 hardware control이 없으면 CaptureNode가 software
fallback을 사용한다. `mirror`는 `horizontal_flip`과 같은 의미이므로 지원하지
않는다.

## ImageFormat 리소스

`images`는 파일 확장자를 속성 이름으로 사용하는 ImageFormat resource map이다.
허용 키는 `png`, `jpeg`, `dng`이며 키가 존재하면 해당 형식의 파일을 생성한다.
최소 한 형식이 필요하고 여러 형식을 동시에 지정할 수 있다. 같은 촬영의 모든
결과는 동일한 timestamp와 capture ID stem을 공유하며 `.png`, `.jpeg`, `.dng`
확장자로만 구분한다. JSON object이므로 같은 형식을 중복 정의할 수 없다.

### 리소스 표현

```
{
  "png": PNG ImageFormat Resource,
  "jpeg": JPEG ImageFormat Resource,
  "dng": DNG ImageFormat Resource
}
```

### 예제

```json
{
  "png": {
    "compression": 7,
    "embedMetadata": true
  },
  "jpeg": {
    "quality": 90,
    "embedMetadata": true
  },
  "dng": {
    "embedMetadata": true
  }
}
```

| 속성 | 타입 | 필수 여부 | 기본값 | 설명 |
| --- | --- | --- | --- | --- |
| `png` | [PNG ImageFormat](#png-imageformat) | 선택 | 없음 | 존재하면 PNG를 생성한다. |
| `jpeg` | [JPEG ImageFormat](#jpeg-imageformat) | 선택 | 없음 | 존재하면 JPEG를 생성한다. 확장자는 `.jpeg`다. |
| `dng` | [DNG ImageFormat](#dng-imageformat) | 선택 | 없음 | 존재하면 sensor RAW DNG를 생성한다. |

`images` 자체를 생략하면 `png`만 기본 활성화된다. `images: {}`와 `null` 값은
허용하지 않는다.

### PNG ImageFormat

#### 리소스 표현

```
{
  "compression": integer,
  "embedMetadata": boolean
}
```

#### 예제

```json
{
  "compression": 7,
  "embedMetadata": true
}
```

| 속성 | 타입 | 필수 여부 | 기본값 | 허용값과 설명 |
| --- | --- | --- | --- | --- |
| `compression` | integer | 선택 | `7` | `0`부터 `9`까지다. 값이 클수록 압축 작업과 압축률이 증가하지만 화질은 변하지 않는다. |
| `embedMetadata` | boolean | 선택 | `true` | EXIF capture metadata 포함 여부다. |

PNG에 `quality`를 지정하면 알 수 없는 속성으로 거절한다.

### JPEG ImageFormat

#### 리소스 표현

```
{
  "quality": integer,
  "embedMetadata": boolean
}
```

#### 예제

```json
{
  "quality": 90,
  "embedMetadata": true
}
```

| 속성 | 타입 | 필수 여부 | 기본값 | 허용값과 설명 |
| --- | --- | --- | --- | --- |
| `quality` | integer | 선택 | `90` | `1`부터 `100`까지의 JPEG quality다. |
| `embedMetadata` | boolean | 선택 | `true` | EXIF capture metadata 포함 여부다. |

JPEG에 `compression`을 지정하면 알 수 없는 속성으로 거절한다.

### DNG ImageFormat

#### 리소스 표현

```
{
  "embedMetadata": boolean
}
```

#### 예제

```json
{
  "embedMetadata": true
}
```

| 속성 | 타입 | 필수 여부 | 기본값 | 허용값과 설명 |
| --- | --- | --- | --- | --- |
| `embedMetadata` | boolean | 선택 | `true` | CaptureNode 사용자 정의 metadata 주입 정책이다. 현재 Picamera2 native DNG metadata는 이 값과 무관하게 항상 보존된다. |

`dng`는 Picamera2에서만 허용된다. V4L2 camera와 조합하면 전체 설정을 거절한다.
DNG는 다른 이미지와 같은 capture ID와 파일 stem을 사용하고 센서가 제공하는
실제 bit depth를 유지한다. 현재 파일은 Picamera2 `save_dng()` 결과를 그대로
저장하므로 sensor·노출 metadata는 포함되지만 `camera.calibration` 같은 CaptureNode
사용자 정의 metadata 주입은 아직 구현하지 않았다. `embedMetadata`는 그 후속
기능의 정책 필드로 예약한다.

## Storage 리소스

`storages`는 0개 이상의 Storage object 배열이다. 모든 Storage는 save, read,
paged list, delete를 제공하며 `name`은 배열 전체에서 고유해야 한다.

| `type` | 용도 |
| --- | --- |
| `file` | 로컬 filesystem |
| `sftp` | SSH 기반 원격 Storage |
| `ftps` | Explicit TLS 기반 FTP Storage |

모든 타입의 `layout`은 다음 값을 허용한다.

| 값 | 동작 |
| --- | --- |
| `daily` | 기본값. Capture 파일을 UTC 날짜 기준 `yyyyMMdd/<filename>`에 저장한다. |
| `flat` | 모든 Capture 파일을 configured directory 바로 아래에 저장한다. |

### File Storage

#### 리소스 표현

```
{
  "type": string,
  "name": string,
  "directory": string,
  "layout": string,
  "maintenance": Maintenance Resource
}
```

#### 예제

```json
{
  "type": "file",
  "name": "local",
  "directory": "./captures",
  "layout": "daily",
  "maintenance": {
    "intervalSeconds": 86400,
    "maxAgeDays": 7
  }
}
```

| 속성 | 타입 | 필수 여부 | 기본값 | 허용값과 설명 |
| --- | --- | --- | --- | --- |
| `type` | string | 필수 | 없음 | `file` |
| `name` | string | 필수 | 없음 | 비어 있지 않은 고유 Storage 이름이다. |
| `directory` | string | 필수 | 없음 | 로컬 저장 경로다. `~`를 확장하고 상대 경로는 프로세스 working directory 기준으로 해석한다. |
| `layout` | string | 선택 | `daily` | `daily` 또는 `flat` |
| `maintenance` | [Maintenance](#maintenance-리소스) 또는 `null` | 선택 | `null` | 이 Storage의 보존 점검과 삭제 정책이다. |

배포된 service의 working directory는 일반적으로 `~/capture-node`다.

### SFTP Storage

#### 리소스 표현

```
{
  "type": string,
  "name": string,
  "host": string,
  "port": integer,
  "username": string,
  "remoteDirectory": string,
  "privateKey": string | null,
  "passwordEnv": string | null,
  "privateKeyPassphraseEnv": string | null,
  "knownHosts": string,
  "connectTimeoutSeconds": number,
  "operationTimeoutSeconds": number,
  "layout": string,
  "maintenance": Maintenance Resource
}
```

#### 예제

```json
{
  "type": "sftp",
  "name": "archive",
  "host": "storage.example.local",
  "port": 22,
  "username": "capture-node",
  "remoteDirectory": "/captures",
  "privateKey": null,
  "passwordEnv": "CAPTURE_NODE_SFTP_PASSWORD",
  "privateKeyPassphraseEnv": null,
  "knownHosts": "~/.ssh/known_hosts",
  "connectTimeoutSeconds": 10,
  "operationTimeoutSeconds": 30,
  "layout": "daily"
}
```

| 속성 | 타입 | 필수 여부 | 기본값 | 허용값과 설명 |
| --- | --- | --- | --- | --- |
| `type` | string | 필수 | 없음 | `sftp` |
| `name` | string | 필수 | 없음 | 비어 있지 않은 고유 Storage 이름이다. |
| `host` | string | 필수 | 없음 | 비어 있지 않은 SSH server hostname 또는 주소다. |
| `port` | integer | 선택 | `22` | `1`부터 `65535`까지다. |
| `username` | string | 필수 | 없음 | 비어 있지 않은 SSH 사용자 이름이다. |
| `remoteDirectory` | string | 필수 | 없음 | 정규화된 절대 POSIX 경로다. 예: `/captures`. |
| `privateKey` | string 또는 `null` | 조건부 | `null` | SSH private key 경로다. `~`를 확장한다. |
| `passwordEnv` | string 또는 `null` | 조건부 | `null` | password가 들어 있는 환경변수 이름이다. |
| `privateKeyPassphraseEnv` | string 또는 `null` | 선택 | `null` | private key passphrase 환경변수 이름이다. `privateKey`가 있을 때만 허용된다. |
| `knownHosts` | string | 선택 | `~/.ssh/known_hosts` | host key 검증 파일 경로다. `~`를 확장한다. |
| `connectTimeoutSeconds` | number | 선택 | `10` | `0`보다 큰 연결·banner·인증 제한 시간이다. |
| `operationTimeoutSeconds` | number | 선택 | `30` | `0`보다 큰 SFTP channel 작업 제한 시간이다. |
| `layout` | string | 선택 | `daily` | `daily` 또는 `flat` |
| `maintenance` | [Maintenance](#maintenance-리소스) 또는 `null` | 선택 | `null` | 이 Storage의 보존 점검과 삭제 정책이다. |

`privateKey`, `passwordEnv` 중 하나 이상이 필요하며 둘을 함께 사용할 수도 있다.
`knownHosts` 검증은 항상 활성화된다. configured `remoteDirectory`는 서버에
존재해야 한다.

### FTPS Storage

#### 리소스 표현

```
{
  "type": string,
  "name": string,
  "host": string,
  "port": integer,
  "username": string,
  "remoteDirectory": string,
  "passwordEnv": string,
  "verifyTls": boolean,
  "caFile": string | null,
  "connectTimeoutSeconds": number,
  "operationTimeoutSeconds": number,
  "layout": string,
  "maintenance": Maintenance Resource
}
```

#### 예제

```json
{
  "type": "ftps",
  "name": "ftps-archive",
  "host": "storage.example.local",
  "port": 21,
  "username": "capture-node",
  "remoteDirectory": "/captures",
  "passwordEnv": "CAPTURE_NODE_FTPS_PASSWORD",
  "verifyTls": true,
  "caFile": "~/.config/capture-node/ftps-ca.pem",
  "connectTimeoutSeconds": 10,
  "operationTimeoutSeconds": 30,
  "layout": "daily"
}
```

| 속성 | 타입 | 필수 여부 | 기본값 | 허용값과 설명 |
| --- | --- | --- | --- | --- |
| `type` | string | 필수 | 없음 | `ftps` |
| `name` | string | 필수 | 없음 | 비어 있지 않은 고유 Storage 이름이다. |
| `host` | string | 필수 | 없음 | 비어 있지 않은 FTPS server hostname 또는 주소다. |
| `port` | integer | 선택 | `21` | `1`부터 `65535`까지다. |
| `username` | string | 필수 | 없음 | 비어 있지 않은 FTP 사용자 이름이다. |
| `remoteDirectory` | string | 필수 | 없음 | 정규화된 절대 POSIX 경로다. 예: `/captures`. |
| `passwordEnv` | string | 필수 | 없음 | password가 들어 있는 환경변수 이름이다. |
| `verifyTls` | boolean | 선택 | `true` | certificate chain과 hostname 검증 여부다. |
| `caFile` | string 또는 `null` | 선택 | `null` | 자체 서명 인증서 등에 사용할 PEM CA 경로다. `~`를 확장한다. |
| `connectTimeoutSeconds` | number | 선택 | `10` | `0`보다 큰 연결 제한 시간이다. |
| `operationTimeoutSeconds` | number | 선택 | `30` | `0`보다 큰 FTP 작업 제한 시간이다. |
| `layout` | string | 선택 | `daily` | `daily` 또는 `flat` |
| `maintenance` | [Maintenance](#maintenance-리소스) 또는 `null` | 선택 | `null` | 이 Storage의 보존 점검과 삭제 정책이다. |

FTPS는 port와 관계없이 explicit `AUTH TLS`와 보호된 data channel만 사용한다.
Implicit FTPS는 지원하지 않는다. `verifyTls: true`에서 `caFile`을 생략하면 OS
기본 trust store를 사용한다. `verifyTls: false`는 물리적으로 격리된 link에서만
명시적으로 사용해야 하며, 이때 `caFile`은 보존되지만 무시된다. 첫 save 때
권한이 있으면 누락된 `remoteDirectory`를 재귀적으로 생성한다.

## Maintenance 리소스

각 Storage가 자기 파일의 보존 기간과 점검 주기를 소유하는 정책이다.
`maintenance`를 생략하거나 `null`로 지정하면 해당 Storage의 자동 삭제를
실행하지 않는다.

### 리소스 표현

```
{
  "intervalSeconds": number,
  "maxAgeDays": integer
}
```

### 예제

```json
{
  "intervalSeconds": 86400,
  "maxAgeDays": 7
}
```

| 속성 | 타입 | 필수 여부 | 기본값 | 허용값과 설명 |
| --- | --- | --- | --- | --- |
| `intervalSeconds` | number | 선택 | `86400` | `0`보다 큰 보존 점검 간격이다. |
| `maxAgeDays` | integer | 필수 | 없음 | `0`보다 큰 보존 일수다. UTC modified time 기준으로 더 오래된 파일을 삭제한다. |

File, SFTP, FTPS는 모두 Maintenance resource를 사용할 수 있다. Maintenance가
설정된 Storage마다 독립 scheduler가 시작 즉시 한 번 실행되고 각자의
`intervalSeconds`마다 반복한다. Supervisor 운영 profile의
`config.example.json`에는 Storage와 Maintenance를 구성하지 않는다.

## Calibration 리소스

`camera.calibration`은 해당 카메라에 대해 사용자 또는 외부 도구가 만든
calibration metadata를 손실 없이 보존하기 위한 자유 형식 JSON object다.

### 리소스 표현

```
[@TODO]
```

### 예제

```json
[@TODO]
```

[@TODO] 미래를 위해 예약됨.

## 교차 속성 검증

개별 속성 범위가 맞더라도 다음 조합 규칙을 위반하면 전체 설정을 거절한다.

| 규칙 | 거절되는 예 |
| --- | --- |
| 알 수 없는 속성은 허용하지 않는다. | 최상위 `capture`, `timezone`, `policies`, PNG의 `quality`, SFTP의 평문 `password` |
| Picamera2의 `width`와 `height`는 함께 지정한다. | `width`만 지정 |
| Capture `type`에 맞는 속성만 사용한다. | `daily`에 `interval` 지정 |
| Interval window의 시작과 종료는 함께 지정한다. | `startTime`만 지정 |
| Interval window는 같은 날 안에서 종료한다. | `startTime: "23:00:00"`, `endTime: "01:00:00"` |
| Capture 규칙은 고유해야 한다. | 같은 `daily` resource를 두 번 지정 |
| 하나 이상의 이미지 형식이 필요하다. | `images: {}` |
| DNG는 Picamera2에서만 허용한다. | V4L2와 `images.dng` 조합 |
| Storage 이름은 고유해야 한다. | 같은 `name`을 가진 File과 SFTP |
| Maintenance 값은 양수여야 한다. | `intervalSeconds: 0`, `maxAgeDays: 0` |
| SFTP 인증 방법이 하나 이상 필요하다. | `privateKey`, `passwordEnv`가 모두 `null` |
| Secret 환경변수 이름은 shell identifier 형식이다. | `PASSWORD-NAME`, `123_PASSWORD` |
| 원격 디렉터리는 정규화된 절대 POSIX 경로다. | `captures`, `/a/../captures`, NUL 포함 문자열 |

`camera.params`와 `camera.calibration` 내부는 의도적으로 자유 형식이므로 알 수 없는
속성 금지 규칙의 예외다.

## 시스템 시각

`daily`와 시간 구간이 있는 `interval` schedule은 별도 timezone 설정 없이
OS local time을 그대로 사용한다.
시스템 timezone과 시각을 올바르게 구성하고 유지하는 책임은 설치자에게 있다.
CaptureNode와 배포 스크립트는 특정 timezone을 설정하거나 검증하지 않는다.

기존 `capture` resource, `capture.timezone`, 단일 `image`와 중첩 `raw`는
하위 호환하지 않는다. 설정 파일이나 API 요청에 보내면 알 수 없는
속성으로 거절하며 파일을 자동 변환하거나 다시 저장하지 않는다.

촬영 파일명, 저장 경로 날짜, event timestamp와 metadata에 사용하는 절대 시각은
기존과 같이 UTC를 유지한다. OS local time은 매일 wall-clock trigger 해석에만
사용한다. 시간 구간이 없는 `interval`은 monotonic clock을 사용한다.

## Secret과 경로

설정 파일에는 password나 private key passphrase 원문을 넣지 않는다. 대신
환경변수 이름을 저장한다.

```dotenv
CAPTURE_NODE_SFTP_PASSWORD=replace-me
CAPTURE_NODE_FTPS_PASSWORD=replace-me
```

환경변수 이름은 정규식 `[A-Za-z_][A-Za-z0-9_]*`을 만족해야 한다.

배포된 Web service는 공통 secret을 `~/capture-node/.env.local`에서 읽고,
`instances/cameraN.env`를 나중에 읽어 같은 이름을 instance별로 덮어쓴다. 두
파일의 권한은 `0600`이어야 한다. 수동으로 실행하는 Headless 또는 Web
프로세스는 참조된 값을 process environment로 직접 전달해야 하며,
애플리케이션 자체는 dotenv 파일을 해석하지 않는다.

| 속성 | 경로 처리 |
| --- | --- |
| File `directory` | `~` 확장 후 절대화한다. 상대 경로는 process working directory 기준이다. |
| SFTP `privateKey`, `knownHosts` | 사용할 때 `~`를 확장한다. |
| FTPS `caFile` | 사용할 때 `~`를 확장한다. |
| V4L2 `device` | 설정 문자열을 장치 경로로 사용한다. |
| `remoteDirectory` | 원격 POSIX 절대 경로이며 로컬 경로 확장을 하지 않는다. |

## 실행 중 변경

설정은 시작할 때 전체 검증된다. Web UI의 전용 endpoint를 통한 변경 범위는
다음과 같다.

| 영역 | 실행 중 변경 | 적용 방식 |
| --- | --- | --- |
| `captures` | 가능 | 설정을 먼저 저장한 뒤 scheduler에 적용한다. |
| `camera.params` | 가능 | 카메라를 재연결하지 않고 다음 촬영부터 적용한다. |
| `images` | 가능 | 다음 촬영부터 선택된 모든 형식과 새 encoding policy를 사용한다. |
| `camera` | 가능 | 전용 camera 교체 endpoint가 새 장치를 연결하며 실패 시 이전 구성을 복구한다. |
| `camera.calibration` | 가능 | 카메라를 재연결하지 않고 metadata만 저장하며 이미지 보정을 수행하지 않는다. |
| `storages` | 불가 | `maintenance`를 포함한 Storage 설정은 JSON을 편집하고 프로세스를 재시작해야 한다. |

후속 배포는 기존 instance 설정 파일을 덮어쓰지 않는다.

## 설정 검증

저장소 checkout에서는 다음 명령으로 특정 설정 파일을 실제 Options 모델에 대해
검증할 수 있다.

```bash
.venv/bin/python -c "from pathlib import Path; from capture_node.core.options import Options; Options.model_validate_json(Path('config.example.json').read_text()); print('valid')"
```

설정 파일이 없거나 JSON 문법이 잘못됐거나 root가 object가 아니거나 속성 검증에
실패하면 시작을 중단한다. 기본값이 있는 속성을 생략할 수는 있지만, 검증 후 Web이
설정을 저장하면 정규화된 기본값이 파일에 명시될 수 있다. 단, 비어 있는
`captures`와 `storages`는 Supervisor 운영 profile을 간결하게 유지하도록 속성
자체를 기록하지 않는다.
