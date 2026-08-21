#include "huintech/sp485en.h"

#include <cstddef>
#include <cstdint>
#include <cstdio>

namespace {

using huintech::Gpio;
using huintech::Pin;
using huintech::PinMode;
using huintech::SP485EN;
using huintech::Status;
using huintech::TransferResult;
using huintech::Uart;

struct TestState {
    int failures;
};

#define CHECK(state, condition)                                                  \
    do {                                                                         \
        if (!(condition)) {                                                       \
            std::fprintf(stderr, "%s:%d: check failed: %s\n",                    \
                         __FILE__, __LINE__, #condition);                         \
            ++(state).failures;                                                   \
        }                                                                         \
    } while (false)

class FakeGpio : public Gpio {
public:
    FakeGpio()
        : mode_status(Status::ok),
          write_status(Status::ok),
          mode_calls(0U),
          write_calls(0U),
          last_pin(0U),
          last_mode(PinMode::input),
          level(false)
    {
    }

    Status pin_mode(Pin pin, PinMode mode) override
    {
        ++mode_calls;
        last_pin = pin;
        last_mode = mode;
        return mode_status;
    }

    Status pin_write(Pin pin, bool high) override
    {
        ++write_calls;
        last_pin = pin;
        if (write_status == Status::ok) {
            level = high;
        }
        return write_status;
    }

    Status mode_status;
    Status write_status;
    std::size_t mode_calls;
    std::size_t write_calls;
    Pin last_pin;
    PinMode last_mode;
    bool level;
};

class FakeUart : public Uart {
public:
    FakeUart()
        : begin_status(Status::ok),
          write_status(Status::ok),
          read_status(Status::ok),
          wait_status(Status::ok),
          forced_write_size(no_forced_size),
          begin_calls(0U),
          write_calls(0U),
          read_calls(0U),
          wait_calls(0U),
          baud_rate(0U),
          last_timeout_ms(0U)
    {
    }

    Status begin(std::uint32_t baud) override
    {
        ++begin_calls;
        baud_rate = baud;
        return begin_status;
    }

    TransferResult write(const std::uint8_t*, std::size_t size) override
    {
        ++write_calls;
        const std::size_t transferred =
            forced_write_size == no_forced_size ? size : forced_write_size;
        return write_status == Status::ok
                   ? TransferResult::success(transferred)
                   : TransferResult::failure(write_status, transferred);
    }

    TransferResult read(std::uint8_t*, std::size_t size) override
    {
        ++read_calls;
        return read_status == Status::ok
                   ? TransferResult::success(size)
                   : TransferResult::failure(read_status);
    }

    Status wait_for_transmit(std::uint32_t timeout_ms) override
    {
        ++wait_calls;
        last_timeout_ms = timeout_ms;
        return wait_status;
    }

    static const std::size_t no_forced_size = static_cast<std::size_t>(-1);
    Status begin_status;
    Status write_status;
    Status read_status;
    Status wait_status;
    std::size_t forced_write_size;
    std::size_t begin_calls;
    std::size_t write_calls;
    std::size_t read_calls;
    std::size_t wait_calls;
    std::uint32_t baud_rate;
    std::uint32_t last_timeout_ms;
};

void begin_initializes_uart_and_receive_mode(TestState& state)
{
    FakeUart uart;
    FakeGpio gpio;
    SP485EN device(uart, gpio, 12U);

    CHECK(state, device.begin(9600U) == Status::ok);
    CHECK(state, device.initialized());
    CHECK(state, gpio.mode_calls == 1U);
    CHECK(state, gpio.last_pin == 12U);
    CHECK(state, gpio.last_mode == PinMode::output);
    CHECK(state, gpio.write_calls == 1U);
    CHECK(state, !gpio.level);
    CHECK(state, uart.begin_calls == 1U);
    CHECK(state, uart.baud_rate == 9600U);
}

void begin_rejects_invalid_baud_and_gpio_failure(TestState& state)
{
    FakeUart uart;
    FakeGpio gpio;
    SP485EN device(uart, gpio, 3U);

    CHECK(state, device.begin(0U) == Status::invalid_argument);
    CHECK(state, uart.begin_calls == 0U);

    gpio.mode_status = Status::io_error;
    CHECK(state, device.begin(9600U) == Status::gpio_error);
    CHECK(state, !device.initialized());
}

void single_write_toggles_direction_and_waits(TestState& state)
{
    FakeUart uart;
    FakeGpio gpio;
    SP485EN device(uart, gpio, 5U);
    const std::uint8_t data[] = {1U, 2U, 3U};

    CHECK(state, device.begin(19200U) == Status::ok);
    const TransferResult result = device.write(data, sizeof(data));

    CHECK(state, result.status == Status::ok);
    CHECK(state, result.transferred == sizeof(data));
    CHECK(state, uart.write_calls == 1U);
    CHECK(state, uart.wait_calls == 1U);
    CHECK(state, !gpio.level);
    CHECK(state, gpio.write_calls == 3U);
}

void transaction_keeps_transmit_mode_until_end(TestState& state)
{
    FakeUart uart;
    FakeGpio gpio;
    SP485EN device(uart, gpio, 7U);
    const std::uint8_t first[] = {1U, 2U};
    const std::uint8_t second[] = {3U};

    CHECK(state, device.begin(38400U) == Status::ok);
    CHECK(state, device.begin_transmission() == Status::ok);
    CHECK(state, gpio.level);
    CHECK(state, device.write(first, sizeof(first)).ok());
    CHECK(state, device.write(second, sizeof(second)).ok());
    CHECK(state, uart.wait_calls == 0U);
    CHECK(state, gpio.level);
    CHECK(state, device.end_transmission(25U) == Status::ok);
    CHECK(state, uart.last_timeout_ms == 25U);
    CHECK(state, !gpio.level);
    CHECK(state, !device.transmission_active());
}

void partial_write_aborts_transaction_to_receive(TestState& state)
{
    FakeUart uart;
    FakeGpio gpio;
    SP485EN device(uart, gpio, 8U);
    const std::uint8_t data[] = {1U, 2U, 3U};

    CHECK(state, device.begin(57600U) == Status::ok);
    CHECK(state, device.begin_transmission() == Status::ok);
    uart.forced_write_size = 1U;

    const TransferResult result = device.write(data, sizeof(data));
    CHECK(state, result.status == Status::partial);
    CHECK(state, result.transferred == 1U);
    CHECK(state, !device.transmission_active());
    CHECK(state, !gpio.level);
}

void timeout_and_io_failure_restore_receive_mode(TestState& state)
{
    FakeUart uart;
    FakeGpio gpio;
    SP485EN device(uart, gpio, 9U);
    const std::uint8_t data[] = {42U};

    CHECK(state, device.begin(115200U) == Status::ok);
    uart.wait_status = Status::timeout;
    TransferResult result = device.write(data, sizeof(data));
    CHECK(state, result.status == Status::timeout);
    CHECK(state, !gpio.level);

    uart.wait_status = Status::ok;
    uart.write_status = Status::io_error;
    result = device.write(data, sizeof(data));
    CHECK(state, result.status == Status::io_error);
    CHECK(state, !gpio.level);
}

void invalid_state_and_read_are_explicit(TestState& state)
{
    FakeUart uart;
    FakeGpio gpio;
    SP485EN device(uart, gpio, 10U);
    std::uint8_t data = 0U;

    CHECK(state, device.read(&data, 1U).status == Status::not_initialized);
    CHECK(state, device.begin(9600U) == Status::ok);
    CHECK(state, device.end_transmission(1U) == Status::transaction_not_active);
    CHECK(state, device.begin_transmission() == Status::ok);
    CHECK(state, device.read(&data, 1U).status == Status::transaction_active);
    CHECK(state, device.abort_transmission() == Status::ok);
    CHECK(state, device.read(&data, 1U).status == Status::ok);
}

}  // namespace

int main()
{
    TestState state = {0};

    begin_initializes_uart_and_receive_mode(state);
    begin_rejects_invalid_baud_and_gpio_failure(state);
    single_write_toggles_direction_and_waits(state);
    transaction_keeps_transmit_mode_until_end(state);
    partial_write_aborts_transaction_to_receive(state);
    timeout_and_io_failure_restore_receive_mode(state);
    invalid_state_and_read_are_explicit(state);

    if (state.failures != 0) {
        std::fprintf(stderr, "%d test checks failed\n", state.failures);
        return 1;
    }

    std::puts("SP485EN host tests passed");
    return 0;
}
