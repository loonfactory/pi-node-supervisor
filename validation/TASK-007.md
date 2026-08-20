# TASK-007 actual-device validation

## Target

- Host: `huintech@192.168.12.6`
- Hardware: Raspberry Pi 5 (`aarch64`)
- OS: Debian GNU/Linux 13 (trixie), kernel `6.18.34+rpt-rpi-2712`
- Serial port: `/dev/ttyAMA0`
- Serial configuration: `4800,N,8,1`, timeout 1 second
- Python: 3.13.5
- pyserial: 3.5
- Initial and restored Slave ID: 1

The `huintech` account belongs to the `dialout` group and the serial port was
not observed to be held by another process before validation.

## Results

### Read-only protocol and measurements

All 13 read operations completed with valid Modbus RTU responses and CRCs.

| API | Observed value |
|---|---:|
| Batch humidity | 16.5 %RH |
| Batch temperature | 20.9 °C |
| Batch conductivity | 0 |
| Batch salinity | 0 |
| Batch TDS | 0 |
| Conductivity factor | 0.0 |
| Salinity factor | 0.55 |
| TDS factor | 0.5 |
| Temperature offset | 0.0 |
| Humidity offset | 0.0 |
| Conductivity offset | 0 |
| Slave ID | 1 |

The five individual measurement methods returned the same corresponding
values as the batch request. Conductivity, Salinity, and TDS returned zero,
which matches the Owner's prior observation that this installed basic sensor
variant only provides meaningful Temperature and Humidity values.

### Factor and Offset writes

The existing value of each Factor and Offset register was backed up before
writing. Each register was written with its existing value, its complete 0x06
echo was validated, the value was read back, and the original value was written
and verified again. All 30 operations passed; no setting changed from its
initial value.

### Slave ID write and recovery

The Slave ID was read as 1, changed to 2, and read back successfully at address
2. The recovery procedure then probed address 2, restored the ID to 1, and read
it back successfully at address 1. All six operations passed and the device was
left at its original Slave ID.

## Evidence

- `results/read-only.json`: 13 passed operations, 52 Stream-boundary TX/RX
  records.
- `results/settings.json`: 30 passed operations, 108 Stream-boundary TX/RX
  records.
- `results/slave-id.json`: 6 passed operations, 22 Stream-boundary TX/RX
  records.

The first validation-script revision populated `startedAt` when assembling the
report rather than when opening the serial port. For those three captured files,
the per-frame UTC timestamps are the authoritative execution times. The script
now records both the actual `startedAt` and `completedAt` for future runs.

## Data-sheet observation

The manual lists response time rather than a register refresh period:
Temperature up to 15 seconds, Humidity up to 4 seconds, and Conductivity up to
1 second. These values describe reaction to a physical environmental change;
they do not require an equivalent delay between Modbus register requests.

No Baud Rate register or injected Stream property was changed during this
validation.
