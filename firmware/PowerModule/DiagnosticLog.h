#pragma once

#include <freertos/FreeRTOS.h>
#include "DiagnosticRing.h"

namespace pm {

class DiagnosticLog {
public:
    void begin(uint32_t bootId, uint32_t ms, int32_t resetReason) {
        // Called once, before USB or BLE can consume/produce diagnostic events.
        bootId_ = bootId ? bootId : 1;
        append(ms, DiagEvent::Boot, resetReason);
    }

    uint32_t bootId() const { return bootId_; }

    void append(uint32_t ms, DiagEvent code, int32_t a = 0, int32_t b = 0) {
        portENTER_CRITICAL(&mutex_);
        ring_.append(ms, code, a, b);
        portEXIT_CRITICAL(&mutex_);
    }

    DiagnosticRing::Range range() {
        portENTER_CRITICAL(&mutex_);
        const auto data = ring_.range();
        portEXIT_CRITICAL(&mutex_);
        return data;
    }

    bool read(uint32_t from, uint32_t until, DiagnosticRing::Page& page) {
        portENTER_CRITICAL(&mutex_);
        const bool ok = ring_.read(from, until, page);
        portEXIT_CRITICAL(&mutex_);
        return ok;
    }

private:
    DiagnosticRing ring_;
    uint32_t bootId_ = 0;
    portMUX_TYPE mutex_ = portMUX_INITIALIZER_UNLOCKED;
};

}  // namespace pm
