네이밍
- 키는 camelCase, 시간 값은 …Seconds 접미사
- 값 중 식별자(driver.name 등)는 정책 대상 아님

시각
- 모든 시각은 시스템 로컬 시간 (로더는 시작 시 타임존·현재 시각을 로그)
- interval: start + k × intervalSeconds, k ≥ 0, ≤ end (양끝 포함)
- start/end 생략 시 00:00:00 / 23:59:59
- end <= start 이면 end는 익일
- end 비교는 예정 시각 기준, 실제 실행 시각 아님

병합
- section.defaults → 모든 devices 항목에 암묵 deep merge
- extends → 해당 위치에 JSON Pointer 대상을 deep merge, #/storages/* 만 허용
- 우선순위: defaults < extends 대상 < 디바이스 명시값
- object 재귀, scalar/array 교체
- defaults 금지 키: cameraNum, port, unitId, storage.path

기타
- basePath + path 결합 규칙
- site 블록은 추후 추가 (순수 추가라 버전 유지)

## 최상위 구조

`config.json`은 `schemaVersion`, `policies`, `schedules`, `storages`,
`cameras`, `sensors`, `power`로 구성한다. 카메라와 센서는 별도 registry가
없으며 `devices`의 키가 Supervisor 내부 장치 ID가 된다.

```json
{
  "schemaVersion": 2,
  "policies": {
    "clockToleranceSeconds": 10
  },
  "schedules": {},
  "storages": {},
  "cameras": { "defaults": {}, "devices": {} },
  "sensors": { "defaults": {}, "devices": {} },
  "power": {
    "bootLeadSeconds": 45,
    "shutdownGraceSeconds": 30,
    "minOffSeconds": 300,
    "windows": []
  }
}
```

## 스케줄

하나의 preset은 규칙 배열이다. `interval`은 선택적인 시작·종료 시각과
초 단위 간격을 사용하고, `daily`는 하루 한 번의 시각을 사용한다.

```json
{
  "schedules": {
    "daytime": [
      {
        "type": "interval",
        "startTime": "05:00:00",
        "endTime": "20:00:00",
        "intervalSeconds": 1800
      }
    ],
    "noon": [{ "type": "daily", "time": "12:00:00" }]
  }
}
```

장치는 `"schedule": "daytime"`처럼 preset 이름을 참조한다. 여러 장치가
같은 preset을 사용해도 실행과 실패 경계는 장치별로 독립적이다.

## 카메라 소프트웨어 동기화

카메라가 2대 이상이면 항상 Picamera2 소프트웨어 동기화로 실행한다. 별도의
활성화 스위치는 없다. 서로 다른 `cameraNum`과 동일한 촬영 스케줄이 필요하며,
V4L2를 포함한 다중 카메라 구성은 거절한다. 카메라가 0~1대이면 기존 단일 촬영
동작을 유지한다.

`cameras.synchronization`을 생략하면 장치 ID 정렬상 첫 카메라가 server,
나머지는 client이고, 센서 프레임 속도는 5 FPS다. 필요한 경우 다음 값만 조정한다.

```json
"synchronization": {
  "server": "noir",
  "frameRate": 5,
  "leadSeconds": 2,
  "maxSkewMilliseconds": 10
}
```

이 객체는 `cameras` 아래에 둔다. `leadSeconds`는 요청 전달에 필요한 여유이고,
`maxSkewMilliseconds`는 측정된 시간 차이를 판정하는 기준이지 정밀도 보장이 아니다.
프레임 속도는 두 센서의 전체 해상도 최대 FPS보다 낮게 선택한다. `previewFps`는
JPEG 전달 속도의 상한이며 센서 프레임 속도를 바꾸지 않는다. 프레임 타이밍
control은 동기화가 소유하므로 `camera.params`에서 덮어쓸 수 없다.

프리뷰와 DNG는 계속 실행되는 전체 해상도 센서 스트림을 공유한다. DNG 때문에
카메라를 정지·재시작하지 않는다. RAW 버퍼를 상시 유지하므로 메모리와 전력
사용량이 늘어난다. libcamera의 초기 동기화에는 기본 100 프레임(5 FPS에서 약
20초)이 필요하다. 그 전에 전체 촬영을 요청하거나 목표 프레임을 놓치면 실패로
기록한다. 단일 카메라 즉시 촬영은 준비된 다음 프레임을 기다린다(최대 30초).

전체 수동 촬영과 같은 예약 시각의 촬영에는 공통 `captureAtNs`를 전달한다.
이는 같은 Pi의 `CLOCK_BOOTTIME` 나노초 값이며 다른 호스트에서 재사용할 수 없다.
결과의 `sensorTimestampNs`, `syncReady`, `captureAtNs`와
`camera.synchronization` 이벤트의 실제 `skewMilliseconds`를 함께 확인한다.
이벤트는 `aligned`, `excessiveSkew`, `incomplete`로 판정하며 원본 촬영·저장
성공 여부와 구분한다. RGB/NoIR처럼 모델이 다른 경우, 노출 시간과 rolling shutter
차이까지 없어지는 것은 아니다. 스테레오 치수 측정에는 별도 보정이 필요하다.

NoIR IMX708(Camera Module 3)의 연속 자동초점은 해당 장치의
`captureNode.camera.params`에 `"AfMode": 2`를 설정한다. `AfTrigger`는 필요하지
않다. 예제의 NoIR 장치에는 이 값을 포함하며, 자동초점 모터가 없는 RGB IMX219에는
설정하지 않는다. 초점 이동은 카메라 보정값에도 영향을 줄 수 있으므로 정밀한
스테레오 보정·측정 시에는 초점을 맞춘 뒤 고정하는 별도 운용이 필요하다.

## 저장소

로컬 저장소는 `basePath`, 원격 저장소는 원격 루트인 `basePath`를 가진다.
장치의 `storage` 또는 `storages` 항목에서 이를 확장하고 상대 `path`를
더한다. 여러 대상은 선언 순서대로 시도하며 한 대상의 실패가 다음 대상을
막지 않는다.

```json
{
  "storages": {
    "local": {
      "type": "local",
      "basePath": "/data/capture"
    },
    "nasSftp": {
      "type": "sftp",
      "host": "nas.local",
      "port": 22,
      "username": "collector",
      "passwordEnv": "SUPERVISOR_NAS_PASSWORD",
      "basePath": "/volume1/field"
    },
    "nasFtps": {
      "type": "ftps",
      "host": "nas.local",
      "port": 21,
      "username": "collector",
      "passwordEnv": "SUPERVISOR_NAS_PASSWORD",
      "basePath": "/field",
      "tlsMode": "explicit",
      "verifyTls": true
    }
  }
}
```

SFTP/FTPS 계정은 대상 파일에 대한 읽기·수정·rename 권한이 필요하다.
센서 CSV는 해당 날짜 파일을 읽고 마지막 행을 추가한 임시 파일을 올린 뒤
rename한다. 원격 저장에 실패해도 retry queue나 sync API는 만들지 않는다.

## 카메라와 CaptureNode

`captureNode`는 Supervisor 필드가 아니라 CaptureNode 네이티브 설정 문서의
경계다. defaults와 장치 설정을 deep merge한 뒤 카메라마다 별도 JSON을
생성하고 CaptureNode를 `--config`로 시작한다. 생성 문서에서는
`captures`와 `storages`를 항상 제거한다.

```json
{
  "cameras": {
    "defaults": {
      "captureNode": {
        "camera": {
          "previewFps": 10,
          "previewQuality": 75
        },
        "images": {
          "dng": { "embedMetadata": true }
        }
      },
      "schedule": "daytime",
      "storages": [
        { "extends": "#/storages/local" },
        { "extends": "#/storages/nasSftp" }
      ]
    },
    "devices": {
      "rgb": {
        "captureNode": { "type": "picamera2", "cameraNum": 0 },
        "storages": [
          { "extends": "#/storages/local", "path": "rgb" }
        ]
      }
    }
  }
}
```

일반 촬영 요청은 `formats`를 보내지 않으므로 위 `images`가 적용된다.
노출·flip 같은 카메라 고유 설정은 `captureNode` 안에 두며, 수동 촬영의
`parameters`는 해당 요청에만 적용된다.

## 센서

```json
{
  "sensors": {
    "defaults": {
      "schedule": "soilAllDay",
      "storages": [{ "extends": "#/storages/nasSftp", "path": "soil" }]
    },
    "devices": {
      "soil": {
        "driver": {
          "name": "cwt_soil_thc_s",
          "port": "/dev/ttyAMA0",
          "baudRate": 4800,
          "unitId": 1
        }
      }
    }
  }
}
```

센서 파일은 `YYYY/MM/YYYY-MM-DD.csv`이며 취득 시각, 온도, 습도를 append한다.
같은 serial port는 bus lock을 공유하고 센서별 저장 append는 직렬화한다.

## 전원 유지 구간

```json
{
  "power": {
    "windows": [
      { "startTime": "00:30:00", "endTime": "01:30:00" },
      { "startTime": "09:00:00", "endTime": "09:30:00" }
    ]
  }
}
```

촬영·센서 스케줄에서 계산한 작업 구간과 위 고정 구간을 합쳐
pi-power-manager 설정을 생성한다. 촬영, 센서 취득, 저장 중에는 종료 준비를
시작하지 않는다.


## 프리뷰 형식 선택

`<img src="/api/camera/noir">`는 기본적으로 PNG 프레임 스트림을 표시한다.
요청마다 형식을 선택할 수 있으며 카메라 재설정이나 URL 주기 갱신은 필요하지 않다.

- `/api/camera/noir?format=png`: 무손실 PNG를 명시적으로 요청한다.
- `/api/camera/noir?format=jpeg`: 기존 `previewQuality`를 적용한 JPEG를 요청한다.
- `format` 생략 시 `Accept: image/png` 또는 `Accept: image/jpeg`와 `q` 우선순위로
  프레임 형식을 협상한다. 같은 우선순위, 와일드카드, 헤더 생략은 PNG를 우선한다.
- 명시적인 `format`이 `Accept`보다 우선한다. 지원하지 않는 `format`은 422,
  수용 가능한 형식이 없는 `Accept`는 406, 잘못된 `q` 값은 400을 반환한다.

CaptureNode `/api/preview`와 기존 `/api/nodes/{deviceId}/preview[/stream]`에도
동일하게 적용한다. 응답 외부 형식은 `multipart/x-mixed-replace`이며 각 프레임의
`Content-Type`이 `image/png` 또는 `image/jpeg`다. Supervisor는 형식 요청을
전달하고 응답 바이트를 재인코딩 없이 전달한다. 기존 Bearer/프리뷰 쿠키 인증은 유지한다.

Picamera2 PNG는 현재 main 스트림 픽셀에서 바로 인코딩하며 해상도를 줄이지 않는다.
ISP 처리 이후의 이미지이므로 센서 RAW/DNG와는 다르다. PNG 압축 레벨은 1이며
`previewQuality`는 JPEG에만 적용된다. PNG는 JPEG보다 CPU·대역폭 사용이 커질 수
있고 `previewFps`는 목표 상한이므로 실제 전송 FPS가 낮아질 수 있다.
V4L2 입력은 기존 MJPEG이므로 PNG를 선택해도 입력 단계의 JPEG 손실은 복구되지 않는다.


## 촬영 파일의 보정 메타데이터

보정값은 `cameras.devices.<deviceId>.captureNode.camera.calibration`에 카메라별로
직접 설정한다. `cameras.defaults`에 보정값을 넣으면 검증에서 거부한다. Supervisor가
각 CaptureNode 설정으로 분리하므로 촬영 메타데이터에는 해당 카메라의 보정값만 들어간다.

`examples/config.json`에는 사용자 요청으로 현재 NoIR/RGB 보정값을 사용 예시로 넣었다.
NoIR을 기준으로 R은 단위행렬, T_mm은 [0, 0, 0]이며 RGB에는 그 기준에 대한 R/T를 넣는다.
왜곡 계수는 k1/k2/p1/p2/k3 이름 객체이고, NoIR 초점은 보정 조건에 맞춘 수동 5.6이다.
이 수치는 해당 장착 상태의 예시이므로 다른 장치에서는 각 카메라의 보정값으로 교체한다.
장치별 실제 설정은 Git에서 제외한 `config/devices/<주소>.json`에 보관할 수 있다.
현재 장치의 로컬 설정은 `config/devices/192.168.12.11.json`이다. 장치에서 읽은 설정에
NoIR/RGB 각각의 보정값만 결합했으며 이 파일 작성 자체가 원격 장치에 적용되지는 않는다.
배포 도구는 `--config FILE`로 파일을 선택한다. 기존 장치 설정을 교체하려는 경우에는
별도로 `--replace-config`가 필요하며 일반 코드 배포는 기존 활성 설정을 유지한다.

`embedMetadata: true`일 때 촬영 파일의 `calibration`에는 다음 값만 남긴다.

- `image_width`, `image_height`: 보정 기준 해상도
- `camera_matrix`: 해당 카메라의 내부 행렬
- `distortion_coefficients`: `k1`, `k2`, `p1`, `p2`, `k3`를 키로 갖는 숫자 객체
- `R`, `T_mm`: 공통 장치 좌표계에서 이 카메라 좌표계로의 회전과 이동

저장 형식은 OpenCV pinhole 5계수 모델로 고정한다. 좌표 변환 규칙은
`X_camera = R @ X_rig + T_mm`이며 이동 단위는 mm다. 두 카메라에는 같은 장치
기준 좌표계로 계산된 R/T를 각각 설정한다. 기준 카메라 위치를 원점으로 선택하면
그 카메라의 R/T는 단위행렬/영벡터다. 저장 경로와 촬영 묶음의 카메라 구분을 사용하며
camera_id, group_id, reference_camera, model, transform_convention과 계수 순서
설명은 파일마다 반복하지 않는다. 입력의 기존 배열은 명시된 계수 순서(생략 시
k1, k2, p1, p2, k3)를 사용해 이름 객체로 변환한다. 새 설정은 이름 객체를 사용한다.

샘플 수, 오차 목록, 체크보드 정보, 경로, 검증 근거도 메타데이터에 넣지 않는다.
baseline과 카메라 중심은 R/T에서 계산할 수 있어 중복 저장하지 않는다.
`captureSettings`에는 실제 main 이미지 크기, 좌우·상하 반전 설정과
`configuredControls`(임시 요청까지 병합한 촬영 설정)를 기록한다. 관측된
`LensPosition`, `SensorTimestamp`, `SyncReady` 등은 기존 위치에 유지한다.
보정 해상도와 실제 크기는 구분하며 행렬 스케일링이나 픽셀 보정을 자동 적용하지 않는다.
DNG 현상 시에도 보정 당시 main 이미지의 크롭·방향·해상도에 맞춰야 한다.

저장 위치는 JPEG/PNG EXIF `UserComment` JSON, DNG IFD0 `ImageDescription` JSON이다.
DNG는 원시 픽셀과 기존 태그 값·오프셋을 보존한다. DNG `embedMetadata: false`는
카메라 원본 그대로 반환하며 RAW 해석에 필수인 DNG 태그를 제거하지 않는다.
프리뷰 프레임에는 촬영 메타데이터를 넣지 않는다.
