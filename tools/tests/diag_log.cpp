#include "DiagnosticProtocol.h"
#include "BleQuality.h"
#include <cassert>
#include <climits>
#include <iostream>
#include <string_view>

using namespace pm;

static void checkWire(const Message& line, JsonDocument& decoded) {
    assert(!line.empty() && line.size() <= kMaxLine && line.back() == '\n');
    Message canonical;
    assert(!decode(line, decoded, canonical));
}

static void emitFixture() {
    DiagnosticRing ring;
    ring.append(1, DiagEvent::Boot, 1, 0);
    ring.append(20, DiagEvent::AdvertisingInitial, 1, 0);
    ring.append(100, DiagEvent::Connect, 3, 0);
    ring.append(202, DiagEvent::Authentication, 3, 7);
    ring.append(2000, DiagEvent::DisconnectRequested, 6, 1);
    ring.append(2030, DiagEvent::Termination, 6, 0);
    BleQuality quality;
    quality.begin(1);
    quality.sample(2020, -66);
    quality.txFailed(1, false);
    quality.txFailed(1, false);
    quality.txFailed(1, true);
    quality.summarize(2400, 24, 2, 600, [&](DiagEvent code, int32_t a, int32_t b) {
        ring.append(2400, code, a, b);
    });
    ring.append(2400, DiagEvent::GapDisconnect, 3, 520);
    ring.append(2500, DiagEvent::AdvertisingRetry, 0, 0);
    ring.append(3000, DiagEvent::AdvertisingRetry, 1, 0);
    const auto range = ring.range();
    JsonDocument info;
    fillDiagInfo(info.to<JsonObject>(), 4242, range);
    std::cout << encodeDiagReply(1, nullptr, &info);
    DiagnosticRing::Page page;
    uint32_t cursor = range.oldest;
    for (uint32_t id = 2; cursor < range.next; ++id) {
        assert(ring.read(cursor, range.next, page));
        JsonDocument data;
        fillDiagPage(data.to<JsonObject>(), page);
        std::cout << encodeDiagReply(id, nullptr, &data);
        cursor = page.next;
    }
}

int main(int argc, char** argv) {
    if (argc > 1 && std::string_view(argv[1]) == "--wire-fixture") {
        emitFixture();
        return 0;
    }
    BleQuality quality;
    quality.begin(2);
    quality.sample(UINT32_MAX - 9, -80);
    quality.sample(4, 127);
    quality.txFailed(1, false);
    quality.txFailed(2, true);
    quality.finish();
    quality.txFailed(2, true);
    unsigned summaries = 0;
    quality.summarize(10, 24, 3, 600, [&](DiagEvent code, int32_t a, int32_t b) {
        ++summaries;
        if (code == DiagEvent::BleLastRssi) assert(a == -80 && b == 20);
        else if (code == DiagEvent::BleConnectionParameters) assert(a == 24 && b == 3 * 65536 + 600);
        else { assert(code == DiagEvent::BleTxFailures && a == 0 && b == 1); }
    });
    assert(summaries == 3);
    quality.begin(3);
    quality.summarize(50, 12, 0, 200, [](DiagEvent code, int32_t a, int32_t b) {
        if (code == DiagEvent::BleLastRssi) assert(a == 127 && b == -1);
        if (code == DiagEvent::BleTxFailures) assert(a == 0 && b == 0);
    });
    DiagnosticRing ring;
    DiagnosticRing::Page page;
    assert(ring.range().oldest == 1 && ring.range().next == 1);
    assert(ring.read(1, 1, page) && page.count == 0 && page.next == 1 && page.lost == 0);
    assert(!ring.read(0, 1, page));
    assert(!ring.read(2, 1, page));
    assert(!ring.read(1, 2, page));
    for (unsigned i = 0; i < 10; ++i) ring.append(i, DiagEvent::GapDisconnect, 1, -1);
    const auto snapshot = ring.range();
    assert(snapshot.oldest == 1 && snapshot.next == 11);
    ring.append(10, DiagEvent::AdvertisingRetry, 1, 0);
    assert(ring.read(1, snapshot.next, page) && page.count == 8 && page.next == 9 && !page.lost);
    assert(page.events[0].seq == 1 && page.events[0].ms == 0 && page.events[0].b == -1);
    assert(ring.read(page.next, snapshot.next, page) && page.count == 2 && page.next == snapshot.next);
    for (unsigned i = 11; i < 74; ++i) ring.append(i, DiagEvent::GapDisconnect, INT32_MIN, INT32_MAX);
    const auto full = ring.range();
    assert(full.oldest == 11 && full.next == 75);
    assert(ring.read(1, snapshot.next, page) && page.count == 0 && page.next == snapshot.next && page.lost == 10);
    assert(ring.read(1, full.next, page) && page.count == 8 && page.next == 19 && page.lost == 10);
    assert(page.events[0].seq == 11 && page.events[7].seq == 18);
    assert(ring.read(67, full.next, page) && page.events[7].seq == 74 && page.next == 75);
    JsonDocument info, data, decoded;
    fillDiagInfo(info.to<JsonObject>(), UINT32_MAX, full);
    checkWire(encodeDiagReply(UINT32_MAX, nullptr, &info), decoded);
    assert(decoded["data"]["schema"] == 1 && decoded["data"]["capacity"] == 64);
    assert(decoded["data"]["boot_id"].as<uint32_t>() == UINT32_MAX);
    assert(decoded["data"]["oldest"] == 11 && decoded["data"]["next"] == 75);
    for (size_t i = 0; i < page.count; ++i) {
        page.events[i].seq = page.events[i].ms = UINT32_MAX - 8 + i;
        page.events[i].a = page.events[i].b = INT32_MIN;
    }
    page.next = UINT32_MAX;
    fillDiagPage(data.to<JsonObject>(), page);
    checkWire(encodeDiagReply(UINT32_MAX, nullptr, &data), decoded);
    assert(decoded["data"]["events"].size() == DiagnosticRing::kPageSize);
    assert(decoded["data"]["events"][7][3].as<int32_t>() == INT32_MIN);
    assert(decoded["data"]["next"].as<uint32_t>() == UINT32_MAX);
    checkWire(encodeDiagReply(1, "USB_ONLY"), decoded);
    assert(decoded["ok"] == false && decoded["fault"]["code"] == "USB_ONLY");
    assert(decoded["data"].isNull());
}
