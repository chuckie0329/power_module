#pragma once

#include "DiagnosticRing.h"
#include "Protocol.h"

namespace pm {

inline void fillDiagInfo(JsonObject data, uint32_t bootId, DiagnosticRing::Range range) {
    data["schema"] = 1;
    data["boot_id"] = bootId;
    data["oldest"] = range.oldest;
    data["next"] = range.next;
    data["capacity"] = DiagnosticRing::kCapacity;
}

inline void fillDiagPage(JsonObject data, const DiagnosticRing::Page& page) {
    auto events = data["events"].to<JsonArray>();
    for (size_t i = 0; i < page.count; ++i) {
        const auto& event = page.events[i];
        auto item = events.add<JsonArray>();
        item.add(event.seq);
        item.add(event.ms);
        item.add(static_cast<uint8_t>(event.code));
        item.add(event.a);
        item.add(event.b);
    }
    data["next"] = page.next;
    data["lost"] = page.lost;
}

inline Message encodeDiagReply(uint32_t id, const char* code, const JsonDocument* data = nullptr) {
    JsonDocument doc;
    doc["v"] = kProtocol;
    doc["type"] = "reply";
    doc["id"] = id;
    doc["ok"] = code == nullptr;
    if (code) doc["fault"]["code"] = code;
    else if (data && (data->overflowed() || !doc["data"].set(data->as<JsonVariantConst>()))) return {};
    return doc.overflowed() ? Message{} : encode(doc);
}

}  // namespace pm
