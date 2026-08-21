# SP485EN C++ core

이 디렉터리는 MCU SDK와 분리된 SP485EN 제어 로직을 제공한다. C++11을
지원하며 동적 메모리 할당, 예외, RTTI에 의존하지 않는다.

## Integration contract

애플리케이션은 다음 경계를 대상 MCU에 맞게 구현한다.

- `huintech::Uart`: UART 초기화, 송수신, 전송 완료 대기
- `huintech::Gpio`: `pin_mode`, `pin_write`

핀 mux와 시스템 클록 설정은 애플리케이션의 보드 초기화 책임이다. DE와
`/RE`는 하나의 방향 제어 핀에 결합되어 있다고 가정한다. 낮은 레벨은 수신,
높은 레벨은 송신이다.

```cpp
#include "huintech/sp485en.h"

BoardUart uart;
BoardGpio gpio;
huintech::SP485EN rs485(uart, gpio, direction_pin);

if (rs485.begin(9600U) == huintech::Status::ok) {
    const std::uint8_t request[] = {0x01U, 0x03U};
    const huintech::TransferResult result =
        rs485.write(request, sizeof(request));
}
```

여러 write를 한 송신 구간으로 묶으려면 `begin_transmission()`과
`end_transmission(timeout_ms)`를 사용한다. 부분 전송이나 I/O 실패가 발생하면
트랜잭션은 중단되고 수신 모드로 복귀한다.

## Host build and test

```sh
cmake -S cpp -B cpp/build
cmake --build cpp/build
ctest --test-dir cpp/build --output-on-failure
```

실제 MCU UART/GPIO 어댑터와 보드 통합 시험은 이 모듈의 범위 밖이다.
