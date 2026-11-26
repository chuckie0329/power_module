#pragma once

#include <cstdint>
#include "DiagnosticRing.h"

namespace pm {

// The NimBLE host owns RSSI samples. The application loop tags TX failures
// with their session; its caller serializes begin/count/finish with a short lock.
class BleQuality {
public:
    void begin(uint32_t session) {
        session_ = session;
        notifyFailures_ = indicateFailures_ = 0;
        haveRssi_ = false;
    }

    void sample(uint32_t now, int8_t dbm) {
        if (dbm < -127 || dbm > 20) return;  // HCI uses 127 for unavailable.
        rssiAt_ = now;
        rssiDbm_ = dbm;
        haveRssi_ = true;
    }

    void txFailed(uint32_t session, bool indication) {
        if (!session || session != session_) return;
        auto& count = indication ? indicateFailures_ : notifyFailures_;
        if (count < INT32_MAX) ++count;
    }

    void finish() { session_ = 0; }

    template<class Sink>
    void summarize(uint32_t now, uint16_t interval, uint16_t latency, uint16_t timeout, Sink sink) const {
        const auto age = now - rssiAt_;
        const bool have = haveRssi_ && age <= INT32_MAX;
        sink(DiagEvent::BleLastRssi, have ? rssiDbm_ : 127, have ? static_cast<int32_t>(age) : -1);
        // Part of the existing five-integer event format; no protocol change.
        const auto packed = int32_t(latency & 0x7fffu) * 65536 + timeout;
        sink(DiagEvent::BleConnectionParameters, interval, packed);
        sink(DiagEvent::BleTxFailures, notifyFailures_, indicateFailures_);
    }

private:
    uint32_t session_ = 0;
    uint32_t rssiAt_ = 0;
    int32_t notifyFailures_ = 0;
    int32_t indicateFailures_ = 0;
    int8_t rssiDbm_ = 0;
    bool haveRssi_ = false;
};

}  // namespace pm
