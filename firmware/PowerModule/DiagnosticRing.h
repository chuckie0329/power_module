#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

namespace pm {

enum class DiagEvent : uint8_t {
    Boot = 1, AdvertisingInitial, Connect, Authentication, GapDisconnect,
    Termination, StaleHandle, AdvertisingRetry, SecurityStartFailed, DisconnectRequested,
    BleLastRssi = 11, BleConnectionParameters, BleTxFailures,
    BleNotifyFirstFailure = 14, BleNotifyLastFailure,
};

struct DiagnosticRecord {
    uint32_t seq;
    uint32_t ms;
    DiagEvent code;
    int32_t a;
    int32_t b;
};

// This value type is also tested on the host. The ESP32 wrapper serializes
// access; the bounded copies below do not allocate or perform I/O.
class DiagnosticRing {
public:
    static constexpr size_t kCapacity = 64;
    static constexpr size_t kPageSize = 8;

    struct Range { uint32_t oldest, next; };
    struct Page {
        std::array<DiagnosticRecord, kPageSize> events{};
        size_t count = 0;
        uint32_t next = 0;
        uint32_t lost = 0;
    };

    void append(uint32_t ms, DiagEvent code, int32_t a, int32_t b) {
        if (next_ == UINT32_MAX) return;  // Keep the exclusive cursor representable.
        records_[index(next_)] = {next_, ms, code, a, b};
        ++next_;
    }

    Range range() const {
        return {next_ > kCapacity ? uint32_t(next_ - kCapacity) : 1, next_};
    }

    bool read(uint32_t from, uint32_t until, Page& page) const {
        if (!from || from > until || until > next_) return false;
        const uint32_t oldest = range().oldest;
        const uint32_t start = std::min(until, std::max(from, oldest));
        const uint32_t count = std::min(uint32_t(kPageSize), until - start);
        page.next = start + count;
        page.lost = start - from;
        page.count = count;
        for (uint32_t i = 0; i < count; ++i) page.events[i] = records_[index(start + i)];
        return true;
    }

private:
    static constexpr size_t index(uint32_t seq) { return (seq - 1) % kCapacity; }
    std::array<DiagnosticRecord, kCapacity> records_{};
    uint32_t next_ = 1;
};

static_assert(sizeof(DiagnosticRing) <= 2048, "Diagnostic ring must stay below 2 KiB");

}  // namespace pm
