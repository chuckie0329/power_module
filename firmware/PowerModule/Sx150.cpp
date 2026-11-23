#include "Sx150.h"
#include <string.h>

namespace pm {
namespace {
constexpr uint16_t kVSet = 0x0000;
constexpr uint16_t kISet = 0x0001;
constexpr uint16_t kOnOff = 0x0012;
constexpr uint16_t kModel = 0x0016;
constexpr uint16_t kVersion = 0x0017;
constexpr uint16_t kGroup = 0x001D;
constexpr uint16_t kM0Lvp = 0x0052;
constexpr uint16_t kM0Ovp = 0x0053;
constexpr uint16_t kM0Ocp = 0x0054;
constexpr uint16_t kM0Opp = 0x0055;
constexpr uint16_t kM0Ini = 0x005D;
constexpr uint16_t kM0Flags = 0x005E;
constexpr uint16_t kRecallOn = 0x0100;
constexpr uint32_t kTimeoutMs = 200;

constexpr uint16_t groupOffset(uint8_t group) { return uint16_t(group) * 0x10; }

uint16_t be16(const uint8_t* bytes) {
    return (uint16_t(bytes[0]) << 8) | bytes[1];
}

void putBe16(uint8_t* bytes, uint16_t value) {
    bytes[0] = uint8_t(value >> 8);
    bytes[1] = uint8_t(value);
}
}  // namespace

uint16_t modbusCrc(const uint8_t* bytes, size_t size) {
    uint16_t crc = 0xFFFF;
    while (size--) {
        crc ^= *bytes++;
        for (unsigned bit = 0; bit < 8; ++bit) {
            crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : crc >> 1;
        }
    }
    return crc;
}

void Sx150::begin(unsigned baud, uint8_t address) {
    address_ = address;
    currentSetMaPerCount_ = kDocumentedMaPerCount;
    gapUs_ = baud <= 9600 ? 5000 : 1750;
    port_.setRxBufferSize(256);
    port_.begin(baud, SERIAL_8N1, kSxRx, kSxTx);
    port_.setRxFIFOFull(1);
    lastActivityUs_ = micros();
}

SxError Sx150::sample(SxSample& sample, bool urgent) {
    uint16_t registers[kGroup + 1];
    const SxError error = read(kVSet, kGroup + 1, registers, urgent);
    if (error != SxError::Ok) return error;
    if (registers[kOnOff] > 1 || registers[kGroup] > kMaxGroup) return SxError::BadFrame;

    const uint16_t model = registers[kModel];
    const uint16_t version = registers[kVersion];
    // On the 4008/100 unit, the limit shown as 2.000 A reads as 2000 in I-SET.
    const uint16_t maPerCount = model == 4008 && version == 100 ? 1 : kDocumentedMaPerCount;
    if (registers[kVSet] > UINT16_MAX / kMvPerCount ||
        registers[kISet] > UINT16_MAX / maPerCount) {
        return SxError::BadFrame;
    }
    currentSetMaPerCount_ = maPerCount;
    sample.target = {uint16_t(registers[kVSet] * kMvPerCount),
                     uint16_t(registers[kISet] * maPerCount)};
    sample.outMv = uint32_t(registers[0x0002]) * kMvPerCount;
    sample.outMa = uint32_t(registers[0x0003]) * kDocumentedMaPerCount;
    sample.outMw = uint32_t(registers[0x0004]) * 100;
    sample.inMv = uint32_t(registers[0x0005]) * 10;
    sample.protect = registers[0x0010];
    sample.cvcc = registers[0x0011];
    sample.on = registers[kOnOff] == 1;
    sample.model = model;
    sample.version = version;
    sample.group = registers[kGroup];
    sample.sampledMs = millis();
    return SxError::Ok;
}

SxError Sx150::profile(uint8_t group, SxProfile& profile) {
    uint16_t registers[kM0Ini - kM0Lvp + 1];
    const SxError error = read(kM0Lvp + groupOffset(group), kM0Ini - kM0Lvp + 1, registers);
    if (error == SxError::Ok) {
        profile = {registers[0], registers[kM0Opp - kM0Lvp], registers[kM0Ini - kM0Lvp],
                   registers[kM0Ovp - kM0Lvp], registers[kM0Ocp - kM0Lvp]};
    }
    return error;
}

SxError Sx150::recallState(uint8_t group, bool& autoOn) {
    uint16_t flags;
    const SxError error = read(kM0Flags + groupOffset(group), 1, &flags);
    if (error == SxError::Ok) autoOn = (flags & kRecallOn) != 0;
    return error;
}

SxError Sx150::selectGroup(uint8_t group) {
    return write(kGroup, group, false, false);
}

bool Sx150::canEncode(Target target) const {
    return target.voltageMv % kMvPerCount == 0 &&
           target.currentMa % currentSetMaPerCount_ == 0;
}

SxError Sx150::setpoints(Target target) {
    return writePair(kVSet, target.voltageMv / kMvPerCount,
                     target.currentMa / currentSetMaPerCount_);
}

SxError Sx150::setProtection(uint8_t group, uint16_t ovpRaw, uint16_t ocpRaw) {
    return writePair(kM0Ovp + groupOffset(group), ovpRaw, ocpRaw);
}

SxError Sx150::setOpp(uint8_t group, uint16_t oppRaw) {
    return write(kM0Opp + groupOffset(group), oppRaw);
}

SxError Sx150::setVoltage(uint16_t mv) { return write(kVSet, mv / kMvPerCount); }
SxError Sx150::setCurrent(uint16_t ma) { return write(kISet, ma / currentSetMaPerCount_); }

SxError Sx150::setOn(bool on, bool urgent) {
    return write(kOnOff, on ? 1 : 0, urgent, !on);
}

SxError Sx150::read(uint16_t reg, uint16_t count, uint16_t* values, bool urgent) {
    uint8_t frame[8] = {address_, 0x03};
    putBe16(frame + 2, reg);
    putBe16(frame + 4, count);
    return exchange(frame, sizeof(frame), values, urgent, true);
}

SxError Sx150::write(uint16_t reg, uint16_t value, bool urgent, bool retry) {
    uint8_t frame[8] = {address_, 0x06};
    putBe16(frame + 2, reg);
    putBe16(frame + 4, value);
    return exchange(frame, sizeof(frame), nullptr, urgent, retry);
}

SxError Sx150::writePair(uint16_t reg, uint16_t first, uint16_t second) {
    uint8_t frame[13] = {address_, 0x10, 0, 0, 0, 2, 4};
    putBe16(frame + 2, reg);
    putBe16(frame + 7, first);
    putBe16(frame + 9, second);
    return exchange(frame, sizeof(frame), nullptr, false, true);
}

SxError Sx150::exchange(uint8_t* frame, size_t size, uint16_t* values, bool urgent, bool retry) {
    const uint16_t crc = modbusCrc(frame, size - 2);
    frame[size - 2] = uint8_t(crc);
    frame[size - 1] = uint8_t(crc >> 8);
    exception_ = 0;

    SxError error;
    do {
        error = attempt(frame, size, values, urgent);
        if (error == SxError::Ok || error == SxError::Exception) failures_ = 0;
        if (error != SxError::Timeout && error != SxError::BadFrame) return error;
        if (failures_ < 255) ++failures_;
        if (!urgent && !service_()) return SxError::Cancelled;
        if (!retry) return error;
        retry = false;
    } while (true);
}

SxError Sx150::attempt(const uint8_t* frame, size_t size, uint16_t* values, bool urgent) {
    // Wait for a quiet bus, including after a malformed response or a timeout.
    const uint32_t guardStart = millis();
    for (;;) {
        if (!service_() && !urgent) return SxError::Cancelled;
        for (unsigned n = 0; n < 128 && port_.read() >= 0; ++n) lastActivityUs_ = micros();
        if (micros() - lastActivityUs_ >= gapUs_) break;
        if (millis() - guardStart >= kTimeoutMs) return SxError::BadFrame;
        delay(1);
    }

    const size_t expected = values ? 5 + 2 * be16(frame + 4) : 8;
    uint8_t response[69];  // 32 registers plus address, function, count and CRC.
    size_t received = 0;
    const uint32_t started = millis();
    port_.write(frame, size);
    lastActivityUs_ = micros();
    bool overflow = false;

    // An urgent request can cancel the next exchange, while the current RTU
    // response is drained so it cannot be mistaken for the OFF readback.
    while (millis() - started < kTimeoutMs) {
        service_();
        for (unsigned n = 0; n < 128; ++n) {
            const int byte = port_.read();
            if (byte < 0) break;
            lastActivityUs_ = micros();
            if (received < sizeof(response)) response[received++] = uint8_t(byte);
            else overflow = true;
        }
        if (received && micros() - lastActivityUs_ >= gapUs_) break;
        delay(1);
    }
    if (!received) return SxError::Timeout;
    if (overflow || received < 5 || response[0] != frame[0] ||
        modbusCrc(response, received) != 0) {
        return SxError::BadFrame;
    }
    if (received == 5 && response[1] == (frame[1] | 0x80)) {
        exception_ = response[2];
        return SxError::Exception;
    }
    if (received != expected || response[1] != frame[1]) return SxError::BadFrame;
    if (values) {
        if (response[2] != expected - 5) return SxError::BadFrame;
        for (size_t i = 0; i < response[2] / 2; ++i) values[i] = be16(response + 3 + i * 2);
    } else if (memcmp(frame, response, 6) != 0) {
        return SxError::BadFrame;
    }
    return SxError::Ok;
}

}  // namespace pm
