#pragma once

#include <Arduino.h>
#include "Config.h"

namespace pm {

enum class SxError : uint8_t { Ok, Timeout, BadFrame, Exception, Cancelled };

// Service USB/BLE and link timeouts while an RTU request is in flight.
// Return false to suppress further regular requests after an urgent OFF.
using ServiceLinks = bool (*)();

uint16_t modbusCrc(const uint8_t* bytes, size_t size);

struct Target {
    uint16_t voltageMv = 0;
    uint16_t currentMa = 0;
};

inline bool sameTarget(Target a, Target b) {
    return a.voltageMv == b.voltageMv && a.currentMa == b.currentMa;
}

inline bool allowedTarget(Target t) {
    return t.voltageMv >= kMinVoltageMv && t.voltageMv <= kMaxVoltageMv &&
           t.currentMa <= kMaxCurrentMa &&
           uint32_t(t.voltageMv) * t.currentMa <= kMaxOutputMw * 1000;
}

struct SxSample {
    Target target;
    uint32_t sampledMs = 0;
    uint32_t outMv = 0;
    uint32_t outMa = 0;
    uint32_t outMw = 0;
    uint32_t inMv = 0;
    uint16_t protect = 0;
    uint16_t cvcc = 0;
    uint16_t model = 0;
    uint16_t version = 0;
    uint16_t group = 0;
    bool on = false;
};

struct SxProfile {
    uint16_t lvpRaw = 0;
    uint16_t oppRaw = 0;
    uint16_t powerOnRaw = 0;
    uint16_t ovpRaw = 0;
    uint16_t ocpRaw = 0;

    bool ready() const {
        return oppRaw > 0 && oppRaw <= kMaxOppMw / kOppStepMw && powerOnRaw == 0;
    }
};

class Sx150 {
public:
    static constexpr uint16_t kMvPerCount = 10;
    static constexpr uint16_t kDocumentedMaPerCount = 10;

    Sx150(HardwareSerial& port, ServiceLinks service) : port_(port), service_(service) {}
    void begin(unsigned baud, uint8_t address);
    SxError sample(SxSample& sample, bool urgent = false);
    SxError profile(uint8_t group, SxProfile& profile);
    SxError recallState(uint8_t group, bool& autoOn);
    SxError selectGroup(uint8_t group);
    bool canEncode(Target target) const;
    uint16_t currentSetMaPerCount() const { return currentSetMaPerCount_; }
    SxError setpoints(Target target);
    SxError setProtection(uint8_t group, uint16_t ovpRaw, uint16_t ocpRaw);
    SxError setOpp(uint8_t group, uint16_t oppRaw);
    SxError setVoltage(uint16_t mv);
    SxError setCurrent(uint16_t ma);
    SxError setOn(bool on, bool urgent = false);
    unsigned failedAttempts() const { return failures_; }
    uint8_t exceptionCode() const { return exception_; }

private:
    SxError read(uint16_t reg, uint16_t count, uint16_t* values, bool urgent = false);
    SxError write(uint16_t reg, uint16_t value, bool urgent = false, bool retry = true);
    SxError writePair(uint16_t reg, uint16_t first, uint16_t second);
    SxError exchange(uint8_t* frame, size_t size, uint16_t* values, bool urgent, bool retry);
    SxError attempt(const uint8_t* frame, size_t size, uint16_t* values, bool urgent);

    HardwareSerial& port_;
    ServiceLinks service_;
    uint8_t address_ = kDefaultSxAddress;
    uint16_t currentSetMaPerCount_ = kDocumentedMaPerCount;
    uint32_t gapUs_ = 5000;
    uint32_t lastActivityUs_ = 0;
    unsigned failures_ = 0;
    uint8_t exception_ = 0;
};

}  // namespace pm
