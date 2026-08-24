# `config.json` 설정 안내

`config.json`은 Pi Node Supervisor가 실행할 일회성 작업, 반복 촬영·센서 수집,
전원 유지 구간과 CaptureNode 공통 설정을 정의한다. 장치 연결 정보는
`nodes.json`, API 토큰과 비밀번호는 환경 파일에 두며 이 문서에는 저장하지 않는다.

실행 가능한 예제는 [`examples/config.json`](examples/config.json), 최초 배포용
기본값은 [`examples/config.empty.json`](examples/config.empty.json)에서 확인할 수
있다. 현재 스키마는 아래에 정의되지 않은 속성을 거절한다.

## 전체 구조

```json
{
  "schema_version": 1,
  "timezone": "Asia/Seoul",
  "policies": {
    "boot-lead-seconds": 120,
    "shutdown-grace-seconds": 60,
    "merge-power-gaps-shorter-than-seconds": 300,
    "minimum-lead-seconds": 60,
    "clock-tolerance-seconds": 5,
    "recurrence-horizon-hours": 168
  },
  "activities": [],
  "capture_schedules": [],
  "sensor_schedules": [],
  "power_keep_schedules": [],
  "capture_node_settings": {
    "common": {},
    "nodes": {}
  }
}
```

| 속성 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `schema_version` | 선택 | `1` | 현재 `1`만 허용한다. |
| `timezone` | 필수 | 없음 | 일정 해석에 사용할 IANA 시간대다. 예: `Asia/Seoul`. |
| `policies` | 선택 | 아래 표 | 전원 계획과 반복 일정 계산 정책이다. |
| `activities` | 선택 | `[]` | 특정 날짜·시각에 실행할 일회성 작업이다. |
| `capture_schedules` | 선택 | `[]` | CaptureNode가 수행할 반복 촬영 일정이다. |
| `sensor_schedules` | 선택 | `[]` | Supervisor가 수행할 반복 센서 수집 일정이다. |
| `power_keep_schedules` | 선택 | `[]` | 작업과 관계없이 매일 전원을 유지할 구간이다. |
| `capture_node_settings` | 선택 | 빈 overlay | 관리 중인 CaptureNode 설정에 병합할 공통·개별 값이다. |

모든 작업과 일정의 `id`는 문서 전체에서 서로 달라야 한다. `id`, `node`,
`profile`은 영문·숫자로 시작하고 영문·숫자·점·밑줄·하이픈만 사용할 수 있다.

## 전원 계획 정책

| 속성 | 기본값 | 제약 | 설명 |
| --- | ---: | --- | --- |
| `boot-lead-seconds` | `120` | 0 이상 | 작업 전에 Pi 전원을 미리 켜는 시간이다. |
| `shutdown-grace-seconds` | `60` | 0 이상 | 마지막 작업 후 전원을 유지하는 시간이다. |
| `merge-power-gaps-shorter-than-seconds` | `300` | 0 이상 | 인접 전원 구간의 간격이 이 값보다 짧으면 합친다. |
| `minimum-lead-seconds` | `60` | 1 이상 | RTC 기동 예약에 필요한 최소 여유 시간이다. |
| `clock-tolerance-seconds` | `5` | 0 이상 | 시스템 시각과 RTC 시각의 허용 오차다. |
| `recurrence-horizon-hours` | `168` | 24~744 | 반복 일정을 전원 계획으로 미리 전개할 범위다. |

## 일회성 작업: `activities`

촬영과 센서 수집은 각각 `capture`, `sensor-collection` 타입을 사용한다.

```json
{
  "id": "morning-capture",
  "type": "capture",
  "node": "camera0",
  "profile": "default",
  "at": "2030-01-01T08:00:00",
  "duration_seconds": 10,
  "parameters": {}
}
```

유지보수 구간은 시작과 종료를 지정한다. `node`와 `profile`은 선택 속성이다.

```json
{
  "id": "remote-maintenance",
  "type": "maintenance-window",
  "start": "2030-01-01T13:00:00",
  "end": "2030-01-01T15:00:00"
}
```

UTC offset이 없는 날짜·시각은 최상위 `timezone`으로 해석한다. DST 때문에
존재하지 않거나 중복되는 지역 시각은 거절하므로 명시적인 offset을 사용해야 한다.

## 반복 촬영: `capture_schedules`

한 CaptureNode에는 하나의 반복 촬영 정의만 둘 수 있다.

```json
{
  "id": "camera0-daily",
  "node": "camera0",
  "profile": "default",
  "mode": "daily",
  "daily_times": ["05:00:00", "05:30:00", "06:00:00"],
  "timezone": "Asia/Seoul",
  "duration_seconds": 10,
  "parameters": {}
}
```

연속 간격 촬영은 양수 `interval_seconds`와 기준 시각 `anchor_at`이 필요하다.

```json
{
  "id": "camera0-interval",
  "node": "camera0",
  "profile": "default",
  "mode": "interval",
  "interval_seconds": 1800,
  "daily_times": [],
  "timezone": "Asia/Seoul",
  "anchor_at": "2030-01-01T00:00:00+09:00",
  "parameters": {}
}
```

- `daily`: `interval_seconds`를 사용하지 않는다.
- `interval`: `daily_times`가 비어 있어야 한다.
- `disabled`: `interval_seconds`와 `daily_times`를 모두 비운다.
- `daily_times`: 중복되지 않은 offset 없는 `HH:MM:SS` 목록이다.

## 반복 센서 수집: `sensor_schedules`

센서 노드 하나에는 하나의 정의만 둘 수 있으며 현재 `daily`, `disabled`를 지원한다.

```json
{
  "id": "environment0-temperature-humidity",
  "node": "environment0",
  "profile": "temperature-humidity",
  "mode": "daily",
  "daily_times": ["00:00:00", "00:30:00", "01:00:00"],
  "timezone": "Asia/Seoul",
  "duration_seconds": 5,
  "parameters": {}
}
```

`disabled`에서는 `daily_times`가 비어 있어야 한다. 센서 직렬 포트와 CSV 저장소는
`nodes.json`의 `sensor_serial`, `sensor_storage`에서 설정한다.

## 전원 유지: `power_keep_schedules`

```json
{
  "id": "morning-maintenance",
  "start": "08:50:00",
  "end": "09:30:00",
  "timezone": "Asia/Seoul"
}
```

매일 `start`부터 `end`까지 작업과 관계없이 전원을 유지한다. 시각은 offset 없는
`HH:MM:SS`이며 `end`는 `start`보다 늦어야 한다. 자정을 넘는 구간은 지원하지 않는다.

## CaptureNode overlay: `capture_node_settings`

`common`을 모든 카메라에 먼저 재귀 병합하고 `nodes.<node-id>`를 그 위에 병합한다.
object는 재귀 병합하며 배열과 scalar는 전체를 교체한다.

```json
{
  "capture_node_settings": {
    "common": {
      "storages": [
        {
          "type": "file",
          "name": "local",
          "directory": "captures/{camera}",
          "layout": "daily"
        }
      ]
    },
    "nodes": {
      "camera1": {
        "camera": {
          "params": {
            "horizontal_flip": true,
            "vertical_flip": true
          }
        }
      }
    }
  }
}
```

`{camera}`는 적용 대상 노드 ID로 치환된다. 비밀번호 값은 넣지 않고
`password_env` 같은 환경변수 이름만 기록한다. Overlay 속성은 설치된 CaptureNode
버전의 설정 계약을 따르므로 CaptureNode 업그레이드 시 함께 검토해야 한다.

## 검증과 적용

Web의 **설정 → 고급 설정 · JSON 직접 편집**에서 다음 순서로 적용한다.

1. `JSON 검증`으로 스키마와 파생 전원 계획을 확인한다.
2. `초안 저장`으로 Draft를 저장한다.
3. `JSON 적용`으로 새 revision을 활성화한다.

동일한 API 흐름은 `POST /api/plan/preview`, `PUT /api/config/draft`,
`POST /api/revisions/apply`다. 외부 설정 갱신이나 CaptureNode 재시작이 실패하면
기존 파일과 Active revision을 복구한다.
