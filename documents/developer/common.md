# Common Development Guidelines

이 문서는 언어, 보드, 서비스 종류와 무관하게 적용할 공통 개발 기준이다.

## 1. Boundaries and Responsibilities

외부 시스템과 직접 결합하지 않는다. 외부 API, transport, browser API, iframe, storage, host bridge, hardware, OS, filesystem, network는 adapter, service, handler, provider 같은 내부 contract 뒤에 둔다.
이 섹션은 무엇을 경계 뒤에 둘지와 책임을 어떻게 나눌지를 정한다. 경계 entry point와 cross-cutting policy를 어떻게 중앙화할지는 `Side Effects and Cross-Cutting Policy`에서 다룬다.

책임 분리는 기본적으로 SRP(Single Responsibility Principle)를 따른다.

- 한 모듈을 수정해야 하는 주된 이유가 하나인지 확인한다.
- storage mutation, runtime state, protocol response, view/rendering, transport, domain policy, hardware/external side effect처럼 서로 다른 정책 변화에 반응하는 책임은 분리한다.
- 작은 코드라도 수정 이유가 계속 갈라지면 같은 모듈에 묶지 않는다.
- abstraction, adapter, facade는 여러 하위 구현을 묶을 수 있지만, 그 모듈 자체의 변경 이유는 하나로 설명되어야 한다.
- intent 해석과 side effect 실행을 분리한다.
- 새 기능을 만들기 전에 어떤 boundary가 public contract인지 먼저 정한다.
- 같은 리소스/기능은 기존 folder/file 구성 규약을 따른다. 임의로 줄이거나 합치지 않는다.
- 이미 분리한 책임을 단기 편의 때문에 다시 상위 handler나 orchestrator로 되가져오지 않는다.
- 예외가 필요하면 구현 전에 왜 같은 변경 이유로 볼 수 있는지, 위험은 무엇인지, 언제 다시 분리할지 decision record, issue, 또는 review 기록에 남긴다.

## 2. Public Contracts and Types

public method, command, action, component API는 내부 구현 방식이 아니라 호출자의 의도를 표현해야 한다.
이 섹션은 외부에 드러나는 contract 표면을 다룬다. 함수 내부 의존성과 helper의 policy 독립성은 `Interfaces and Dependencies`에서 다룬다.

- public API 이름은 "무엇을 원하는가"를 드러낸다.
- 반복되는 내부 흐름은 private/internal helper로 모은다.
- 필터나 분기 의도는 모호한 boolean/option보다 이름이나 타입으로 드러낸다.
- state mutation은 named action, command, transition API처럼 의도가 드러나는 진입점을 통해 수행한다.
- derived state는 query, selector, read model처럼 의도가 드러나는 읽기 API를 통해 접근한다.
- external response, request option, file format, persisted metadata, event, error model, default value는 타입이나 명시적 schema로 고정한다.
- 타입은 편의가 아니라 contract 문서의 일부다.
- magic constant, endpoint, serializer option, fixed ID는 사용 지점에 흩뿌리지 않고 defaults/options/properties 계층으로 중앙화한다.
- 성공/실패 결과의 불변 조건은 가능한 한 타입으로 표현한다.

## 3. Interfaces and Dependencies

함수나 컴포넌트는 필요한 것만 요구해야 한다.
이 섹션은 구현 단위가 받는 의존성과 데이터의 모양을 다룬다. public contract 자체의 의도와 타입 규칙은 `Public Contracts and Types`에서 다룬다.

- 필요한 의존성과 데이터만 명시적으로 받는다.
- 호출자가 몰라도 되는 내부 상태, 전역 자원, 외부 시스템 세부사항을 요구하지 않는다.
- 큰 context object는 실제로 여러 호출이 같은 상태 묶음을 공유한다는 증거가 있을 때만 도입한다.
- 숨은 전역 상태나 ambient context에 의존하면 lifetime과 side effect가 흐려지므로 줄인다.
- low-level/high-performance code는 필요한 데이터 범위를 명시하는 narrow view나 slice를 선호할 수 있다.
- high-level code는 필수 field만 포함하는 narrow interface나 immutable view를 선호한다.
- helper는 caller의 business policy를 몰라야 한다. policy가 필요하면 caller가 명시적으로 선택하고, helper는 그 선택된 policy를 실행하는 좁은 mechanism으로 남는다.

## 4. State, Invariants, and Failure Models

중복 상태는 기본적으로 버그 원인으로 본다. 가능하면 제거하거나 단일 source-of-truth에서 파생한다.

- protocol-visible state, persisted metadata, runtime state가 같은 사실을 각각 저장하지 않게 한다.
- derived index, cache, lookup table은 source-of-truth가 아니다.
- cache나 lookup table은 원본 변경 시 갱신 조건이 명확해야 한다.
- 중복 상태는 논리적으로 분리된 runtime state를 추적해야 할 때만 예외로 허용하며, 이 경우 source-of-truth를 먼저 정한다.
- 허용되지 않는 상태 조합은 가능하면 타입, 상태 전이 API, assertion, fail-fast guard로 코드 레벨에서 막는다.
- invariant는 내부에서 검증한다. 테스트를 위해 내부 상태를 노출하지 않고, public API가 보장하는 결과와 side effect를 검증한다.
- 불가능한 상태, 필수 field 누락, 잘못된 state transition은 조용히 fallback하지 않는다.
- expected failure와 programmer error를 구분한다.
- expected failure는 result type이나 명시적 실패 모델로 표현하고, programmer error는 exception/assertion/fail-fast로 드러낸다.
- fallback의 핵심 policy는 중앙에서 일관되게 관리하되, 구체 implementation은 entry point의 protocol, runtime, hardware 제약에 맞게 위임한다.

## 5. Boundary Validation and Lifecycle

각 boundary는 자기 입력과 lifecycle을 책임진다.

- public entry point는 null, empty, invalid argument를 먼저 검증한다.
- invalid argument, unsupported state, closed/disposed state, expected failure는 가능한 한 빠르게 반환하거나 드러낸다.
- guard clause와 early return을 선호한다. 실패/예외/처리 불가 조건을 먼저 빠져나가게 하고, 정상 경로를 깊은 중첩 안에 숨기지 않는다.
- 검증과 실제 실행은 분리할 수 있다.
- async 실행 전에 동기적으로 인자 오류를 드러내야 할 수 있다.
- service, handler, adapter가 서로 다른 trust boundary라면 각자 입력 검증을 수행한다.
- cancellation, readiness, closed/disposed state를 고려한다.
- 준비되지 않은 객체 접근이나 cancellation 이후 callback은 명확히 처리한다.
- UI/view 이탈, host bridge disconnect, external process failure 같은 lifecycle boundary를 contract에 포함한다.

## 6. Testing Rules

테스트는 코드 문자열 감시보다 observable behavior와 contract를 검증해야 한다.

허용되는 테스트:

- public protocol, wire frame, file format, parser/encoder의 고정 contract 검증
- 계산 결과, boundary condition, reject case 검증
- state transition 후 public API, protocol response, persisted output, public diagnostic 같은 관찰 가능한 결과 검증
- 실제 장치나 integration path에서 contract response를 확인하는 validation test
- public contract로 정의된 golden output 검증
- hot path의 allocation count, timeout boundary, concurrency bottleneck 같은 non-functional contract 검증

피할 테스트:

- 함수명, 변수명, 특정 호출 문자열만 찾는 테스트
- 구현 파일 구조가 바뀌면 기대값도 같이 고쳐야 하는 1:1 mirror test
- public contract가 아닌 내부 구현 순서를 lock하는 테스트
- 의미 있는 contract가 아닌 로그 문구나 내부 디버그 문자열에 의존하는 테스트

상호작용 검증:

- 내부 도메인 객체 간 호출 순서를 mock으로 검증하지 않는다.
- hardware, OS, filesystem, network API처럼 시스템 경계를 넘어가는 I/O side effect는 public contract일 수 있다.
- 이 경우 test double, mock, spy를 사용해 전송 순서, payload, retry, timeout, failure handling을 검증할 수 있다.

실행 계층:

- 순수 도메인 로직의 deterministic unit test와 실제 장치/외부 인프라를 포함하는 integration/validation test는 분리해서 실행하고 관리한다.
- integration/validation test는 장치, 네트워크, timing 상태에 의존할 수 있으므로 flaky failure와 제품 failure를 구분할 evidence를 남긴다.

예외:

- protocol opcode, wire frame, file magic, ABI처럼 고정 contract 자체가 문자열, 상수, byte sequence인 경우는 직접 검증해도 된다.
- golden file이나 snapshot은 그 출력 자체가 public contract일 때만 사용한다.

## 7. Side Effects and Cross-Cutting Policy

공통 관심사와 external side effect는 entry point를 흩뜨리지 않는다.
이 섹션은 이미 경계 뒤에 둔 side effect와 cross-cutting policy를 어디서 관리할지 정한다. 무엇을 경계 뒤에 둘지는 `Boundaries and Responsibilities`에서 다룬다.

- HTTP 실행, handler acquisition, host bridge, filesystem, history, undo/redo, grouping, filtering 같은 정책은 한 곳에서 관리한다.
- 각 service/component가 직접 client, bridge, transport를 만들지 않는다.
- 선언적 table이나 provider를 통해 cross-cutting policy를 중앙화한다.
- side effect entry point는 public contract와 failure policy를 명확히 가져야 한다.

## 8. Documentation and Decision Records

문서는 결정, 근거, 제약, 기각한 대안, 나중에 바꿀 때의 주의사항을 남기는 용도다. 코드의 현재 상태를 길게 복제하지 않는다.

- 중요한 결정은 채택한 해결책뿐 아니라 거부한 대안과 거부 이유를 함께 기록한다.
- 문서는 source-of-truth를 명확히 해야 한다. 진행 기록, 상태판, 설계 문서를 같은 역할로 쓰지 않는다.
- 요구사항이나 설계가 크게 바뀌면 기존 기록을 조용히 덮어쓰지 않는다. 기존 판단을 superseded/obsolete로 닫고 새 결정 기록에서 참조한다.
- 사소한 보정은 기존 기록에 변경 이력을 추가해도 되지만, 문제 정의가 바뀌면 새 기록으로 분리한다.
- temporary work, unresolved decision, workaround는 숨기지 않는다.
- TODO/marker는 구체적이어야 하며, 가능하면 tracking item으로 승격한다.

## 9. Code Comments and API Documentation

코드는 이름과 구조로 의도를 드러내야 한다. 주석은 구현을 반복 설명하기보다 아직 코드로 표현되지 않은 제약, 결정, 미해결 지점을 표시한다.

- 주석을 달기 전에 먼저 이름, 타입, 함수 경계, 모듈 구조만으로 코드를 최대한 이해 가능하게 만든다. 주석은 불명확한 코드를 보상하는 수단이 아니다.
- public API 문서화 주석은 각 언어와 ecosystem의 표준 규약을 따른다.
  - Documentation comment form: API 문서로 추출되는 구조화 주석 형식. 예: `///`, `/** */`.
  - Tag vocabulary: 매개변수, 반환값, 예외, type parameter를 나누는 태그 규칙. 예: `<param>`, `<returns>`, `@param`, `@return`.
  - Phrasing convention: 멤버 종류별 첫 문장과 문체 규칙. 예: "Gets or sets ...", "Initializes a new instance ...".
- 언어에 표준 문서화 규약이 있으면 project-local 스타일을 새로 만들지 않는다.

## 10. Change Cost and ROI Rule

변경은 현재 문제를 해결하면서 미래 작업 비용을 낮추거나, 최소한 불필요하게 높이지 않아야 한다.

- production outage, device hang, data loss 같은 긴급 상황에서는 mitigation을 우선한다. 단, 책임 경계를 흐리는 workaround는 제거 조건과 후속 refactor tracking item을 함께 남긴다.
- 납기나 마감 압박은 품질 기준을 끄는 예외가 아니다. 시간이 부족하면 scope를 줄이고, 임시 기술 부채를 받아들일 때는 이유, 위험, 제거 조건을 tracking item에 남긴다.
- feature/refactor 작업에서는 즉시 증상을 가리는 수정이 책임 경계를 더 흐리면 먼저 구조 기준을 정한다.
- 모든 코드는 재사용 가능성을 염두에 두고 작성한다. 다만 미래 요구를 예측해 과도하게 추상화하지 않고, SRP를 지키는 범용 책임 단위로 분리한다.
- 새 abstraction은 관찰된 중복, external complexity, side effect, lifetime 문제, policy 분리, public contract clarity, external boundary substitutability를 개선할 때만 만든다.
- 입력이 충분히 모이기 전에 추상화를 확정하지 않는다.
- interface/contract는 external boundary나 교체 가능한 구현 경계에서 우선 정의한다. 순수 내부 구현에는 관찰된 비용을 줄일 때만 새 abstraction을 추가한다.
- 기존 규약을 이해하지 않고 새 패턴을 만들지 않는다.
- 시간/공간 복잡도 같은 baseline efficiency는 설계 단계에서 검토한다. 그 이상의 micro-optimization이나 system tuning은 measurement log, benchmark, profiling, resource ledger 같은 객관적 근거로 진행한다.
- 비가역적 결정일수록 더 높은 수준의 구조 검토와 합의를 요구한다. 공개 protocol/API, persisted format, storage layout, database schema처럼 되돌리기 어려운 변경은 internal implementation 변경보다 더 보수적으로 다룬다.
