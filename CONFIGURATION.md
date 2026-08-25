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
