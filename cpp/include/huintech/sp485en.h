#ifndef HUINTECH_SP485EN_H
#define HUINTECH_SP485EN_H

#include "huintech/io.h"

namespace huintech {

class SP485EN : public Stream {
public:
    SP485EN(Uart& uart, Gpio& gpio, Pin direction_pin);

    Status begin(std::uint32_t baud_rate);

    TransferResult write(const std::uint8_t* data, std::size_t size) override;
    TransferResult read(std::uint8_t* data, std::size_t size) override;

    Status begin_transmission();
    Status end_transmission(std::uint32_t timeout_ms);
    Status abort_transmission();

    bool initialized() const { return initialized_; }
    bool transmission_active() const { return transmission_active_; }

private:
    Status enter_transmit_mode();
    Status enter_receive_mode();
    TransferResult write_in_active_transmission(
        const std::uint8_t* data,
        std::size_t size);
    TransferResult finish_single_write(
        TransferResult write_result,
        std::size_t requested_size);

    Uart& uart_;
    Gpio& gpio_;
    Pin direction_pin_;
    bool initialized_;
    bool transmission_active_;
};

}  // namespace huintech

#endif
