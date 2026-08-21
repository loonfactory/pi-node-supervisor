#ifndef HUINTECH_IO_H
#define HUINTECH_IO_H

#include <cstddef>
#include <cstdint>

namespace huintech {

enum class Status {
    ok,
    invalid_argument,
    not_initialized,
    transaction_active,
    transaction_not_active,
    partial,
    timeout,
    io_error,
    gpio_error
};

struct TransferResult {
    Status status;
    std::size_t transferred;

    static TransferResult success(std::size_t count)
    {
        TransferResult result = {Status::ok, count};
        return result;
    }

    static TransferResult failure(Status failure_status, std::size_t count = 0U)
    {
        TransferResult result = {failure_status, count};
        return result;
    }

    bool ok() const { return status == Status::ok; }
};

class Stream {
public:
    virtual ~Stream() {}

    virtual TransferResult write(const std::uint8_t* data, std::size_t size) = 0;
    virtual TransferResult read(std::uint8_t* data, std::size_t size) = 0;
};

class Uart : public Stream {
public:
    virtual Status begin(std::uint32_t baud_rate) = 0;
    virtual Status wait_for_transmit(std::uint32_t timeout_ms) = 0;
};

using Pin = std::uint32_t;

enum class PinMode {
    input,
    output
};

class Gpio {
public:
    virtual ~Gpio() {}

    virtual Status pin_mode(Pin pin, PinMode mode) = 0;
    virtual Status pin_write(Pin pin, bool high) = 0;
};

}  // namespace huintech

#endif
