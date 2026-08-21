#include "huintech/sp485en.h"

namespace huintech {

namespace {

const std::uint32_t default_transmit_timeout_ms = 1000U;

TransferResult normalized_write_result(
    TransferResult result,
    std::size_t requested_size)
{
    if (result.status == Status::ok && result.transferred != requested_size) {
        return TransferResult::failure(Status::partial, result.transferred);
    }
    return result;
}

}  // namespace

SP485EN::SP485EN(Uart& uart, Gpio& gpio, Pin direction_pin)
    : uart_(uart),
      gpio_(gpio),
      direction_pin_(direction_pin),
      initialized_(false),
      transmission_active_(false)
{
}

Status SP485EN::begin(std::uint32_t baud_rate)
{
    initialized_ = false;
    transmission_active_ = false;

    if (baud_rate == 0U) {
        return Status::invalid_argument;
    }

    if (gpio_.pin_mode(direction_pin_, PinMode::output) != Status::ok) {
        return Status::gpio_error;
    }

    if (enter_receive_mode() != Status::ok) {
        return Status::gpio_error;
    }

    const Status uart_status = uart_.begin(baud_rate);
    if (uart_status != Status::ok) {
        enter_receive_mode();
        return uart_status;
    }

    initialized_ = true;
    return Status::ok;
}

TransferResult SP485EN::write(const std::uint8_t* data, std::size_t size)
{
    if (!initialized_) {
        return TransferResult::failure(Status::not_initialized);
    }
    if (data == nullptr && size != 0U) {
        return TransferResult::failure(Status::invalid_argument);
    }
    if (size == 0U) {
        return TransferResult::success(0U);
    }

    if (transmission_active_) {
        return write_in_active_transmission(data, size);
    }

    const Status transmit_status = enter_transmit_mode();
    if (transmit_status != Status::ok) {
        enter_receive_mode();
        return TransferResult::failure(transmit_status);
    }

    const TransferResult write_result = uart_.write(data, size);
    return finish_single_write(write_result, size);
}

TransferResult SP485EN::read(std::uint8_t* data, std::size_t size)
{
    if (!initialized_) {
        return TransferResult::failure(Status::not_initialized);
    }
    if (transmission_active_) {
        return TransferResult::failure(Status::transaction_active);
    }
    if (data == nullptr && size != 0U) {
        return TransferResult::failure(Status::invalid_argument);
    }

    return uart_.read(data, size);
}

Status SP485EN::begin_transmission()
{
    if (!initialized_) {
        return Status::not_initialized;
    }
    if (transmission_active_) {
        return Status::transaction_active;
    }

    const Status status = enter_transmit_mode();
    if (status != Status::ok) {
        enter_receive_mode();
        return status;
    }

    transmission_active_ = true;
    return Status::ok;
}

Status SP485EN::end_transmission(std::uint32_t timeout_ms)
{
    if (!initialized_) {
        return Status::not_initialized;
    }
    if (!transmission_active_) {
        return Status::transaction_not_active;
    }

    const Status wait_status = uart_.wait_for_transmit(timeout_ms);
    const Status receive_status = enter_receive_mode();
    transmission_active_ = false;

    if (receive_status != Status::ok) {
        return receive_status;
    }
    return wait_status;
}

Status SP485EN::abort_transmission()
{
    if (!initialized_) {
        return Status::not_initialized;
    }
    if (!transmission_active_) {
        return Status::transaction_not_active;
    }

    const Status status = enter_receive_mode();
    transmission_active_ = false;
    return status;
}

Status SP485EN::enter_transmit_mode()
{
    const Status status = gpio_.pin_write(direction_pin_, true);
    return status == Status::ok ? Status::ok : Status::gpio_error;
}

Status SP485EN::enter_receive_mode()
{
    const Status status = gpio_.pin_write(direction_pin_, false);
    return status == Status::ok ? Status::ok : Status::gpio_error;
}

TransferResult SP485EN::write_in_active_transmission(
    const std::uint8_t* data,
    std::size_t size)
{
    TransferResult result = normalized_write_result(uart_.write(data, size), size);
    if (!result.ok()) {
        const Status receive_status = enter_receive_mode();
        transmission_active_ = false;
        if (receive_status != Status::ok) {
            return TransferResult::failure(receive_status, result.transferred);
        }
    }
    return result;
}

TransferResult SP485EN::finish_single_write(
    TransferResult write_result,
    std::size_t requested_size)
{
    TransferResult result = normalized_write_result(write_result, requested_size);

    if (result.ok()) {
        const Status wait_status =
            uart_.wait_for_transmit(default_transmit_timeout_ms);
        if (wait_status != Status::ok) {
            result = TransferResult::failure(wait_status, result.transferred);
        }
    }

    const Status receive_status = enter_receive_mode();
    if (receive_status != Status::ok) {
        return TransferResult::failure(receive_status, result.transferred);
    }
    return result;
}

}  // namespace huintech
