#include "Controller.h"
#include <algorithm>
#include <cstring>
#include <string_view>

namespace pm {
namespace {

std::string_view asText(JsonVariantConst value) {
    const auto text = value.as<JsonString>();
    return text.isNull() ? std::string_view{} : std::string_view(text.c_str(), text.size());
}

}  // namespace

void Controller::begin(const char* name, unsigned sxBaud, uint8_t sxAddress) {
    name_ = name;
    sxBaud_ = sxBaud;
    sxAddress_ = sxAddress;
    nextPoll_ = nextProfile_ = millis();
    update_.boot();
}

void Controller::bleChanged(uint32_t generation, uint16_t minPeriod) {
    bleMinPeriod_ = minPeriod;
    if (generation == bleGeneration_) return;
    bleGeneration_ = generation;
    update_.disconnected(Link::Ble, generation);
    if (pending_ && pending_->link == Link::Ble) pending_.reset();
    clients_[linkIndex(Link::Ble)] = {};
    streamMs_[linkIndex(Link::Ble)] = kDefaultStreamMs;
}

void Controller::wireError(Link link, const char* code) {
    send(link, 0, code);
}

uint16_t Controller::minStreamMs(Link link) const {
    const uint16_t sxMinPeriod = sxBaud_ == kFastSxBaud ? kFastStreamMs : kStandardStreamMinMs;
    return link == Link::Ble ? std::max(sxMinPeriod, bleMinPeriod_) : sxMinPeriod;
}

uint16_t Controller::streamMs(Link link) const {
    return std::max(streamMs_[linkIndex(link)], minStreamMs(link));
}

std::optional<uint32_t> Controller::streamSampleMs() const {
    if (!fresh() || stopPending_ || stopping_ || lastSxFault_) return std::nullopt;
    return sample_.sampledMs;
}

void Controller::receive(Link link, const Message& line) {
    JsonDocument doc;
    Message canonical;
    if (const char* error = decode(line, doc, canonical)) {
        wireError(link, error);
        return;
    }
    if (!doc["id"].is<uint32_t>() || doc["id"].as<uint32_t>() == 0) {
        wireError(link, "BAD_ID");
        return;
    }
    const uint32_t id = doc["id"].as<uint32_t>();
    if (!doc["v"].is<uint8_t>() || doc["v"].as<uint8_t>() != kProtocol) {
        send(link, id, "BAD_VERSION");
        return;
    }
    const auto args = doc["args"].as<JsonObjectConst>();
    if (args.isNull()) {
        send(link, id, "BAD_ARGS");
        return;
    }
    const auto op = asText(doc["op"]);
    const auto updateOperation = updateOp(op);
    if (op == "get_info") {
        JsonDocument data;
        fillInfo(data.to<JsonObject>());
        data["stream_period_ms"] = streamMs(link);
        data["min_stream_period_ms"] = minStreamMs(link);
        send(link, id, nullptr, &data);
        return;
    }
    if (op == "get_state") {
        send(link, id, nullptr);
        return;
    }
    if (updateOperation == UpdateOp::Info) {
        JsonDocument data;
        update_.describe(data.to<JsonObject>());
        data["outputs_off"] = outputsVerifiedOff();
        sendUpdate({link, generation(link), 0}, id, nullptr, &data);
        return;
    }
    if (op == "set_stream_period") {
        const uint16_t period = args["period_ms"].as<uint16_t>();
        const bool supported = period == kFastStreamMs || period == 50 ||
                               period == 100 || period == 200 || period == 500 ||
                               period == 1000 || period == 2000 ||
                               (link == Link::Ble && period == kLowMtuBleStreamMs);
        if (!args["period_ms"].is<uint16_t>() || period < minStreamMs(link) || !supported) {
            send(link, id, "BAD_STREAM_PERIOD");
            return;
        }
        auto& current = streamMs_[linkIndex(link)];
        if (period < current) nextPoll_ = millis();
        current = period;
        send(link, id, nullptr);
        return;
    }
    const uint32_t clientId = doc["client_id"].as<uint32_t>();
    if (!doc["client_id"].is<uint32_t>() || !clientId) {
        send(link, id, "BAD_CLIENT_ID");
        return;
    }
    if (op == "heartbeat") return;  // Older clients send this without expecting a reply.
    const UpdateOwner owner{link, generation(link), clientId};
    if (op == "disconnect") {
        if (update_.owns(owner)) update_.abort();
        if (streamMs_[linkIndex(link)] < kStandardStreamMinMs) {
            streamMs_[linkIndex(link)] = kDefaultStreamMs;
        }
        if (pending_ && pending_->link == link && pending_->clientId == clientId &&
            pending_->generation == owner.generation) {
            finish(*pending_, "CANCELLED");
            pending_.reset();
        }
        send(link, id, nullptr);
        return;
    }
    if (updateOperation != UpdateOp::None) {
        if (updateRequest_ || processingUpdate_ || pending_ || running_ || stopPending_ || stopping_) {
            sendUpdate(owner, id, "BUSY");
        } else {
            updateRequest_ = UpdateRequest{owner, id, updateOperation, std::move(doc)};
        }
        return;
    }
    const bool urgent = op == "all_off" || op == "output_off";
    if (!urgent && (update_.locksOutputs() || updateRequest_ || processingUpdate_)) {
        send(link, id, "OTA_BUSY");
        return;
    }
    auto& client = clients_[linkIndex(link)];
    if (client.id == clientId) {
        for (const auto& item : client.cache) {
            if (item.id != id) continue;
            if (item.canonical == canonical) reply_(link, item.response, generation(link));
            else send(link, id, "ID_REUSED");
            return;
        }
        for (const auto* inFlight : {&pending_, &running_, &stopReply_}) {
            if (!*inFlight || (*inFlight)->link != link || (*inFlight)->generation != generation(link) ||
                (*inFlight)->clientId != clientId || (*inFlight)->id != id) continue;
            if ((*inFlight)->canonical != canonical) send(link, id, "ID_REUSED");
            return;
        }
        if (id <= client.lastRequestId) {
            send(link, id, "OLD_ID");
            return;
        }
    }

    Request request{link, generation(link), id, clientId, std::move(canonical), Op::Save};
    if (urgent) {
        request.op = Op::Stop;
    } else if (op == "set_output") {
        if (!args["voltage_mV"].is<uint16_t>() || !args["current_mA"].is<uint16_t>() ||
            !args["enabled"].is<bool>() || !doc["expected_rev"].is<uint32_t>()) {
            send(link, id, "BAD_ARGS");
            return;
        }
        const uint16_t v = args["voltage_mV"].as<uint16_t>();
        const uint16_t i = args["current_mA"].as<uint16_t>();
        if (v < kMinVoltageMv || v > kMaxVoltageMv || i > kMaxCurrentMa) {
            send(link, id, "RANGE");
            return;
        }
        if (v % kStep || i % kStep) {
            send(link, id, "BAD_STEP");
            return;
        }
        if (!allowedTarget({v, i})) {
            send(link, id, "POWER_LIMIT");
            return;
        }
        request.op = Op::SetOutput;
        request.target = {v, i};
        request.enabled = args["enabled"].as<bool>();
        request.expectedRev = doc["expected_rev"].as<uint32_t>();
    } else if (op == "select_group") {
        if (!args["group"].is<uint8_t>() || args["group"].as<uint8_t>() > kMaxGroup ||
            !doc["expected_rev"].is<uint32_t>()) {
            send(link, id, "BAD_ARGS");
            return;
        }
        request.op = Op::SelectGroup;
        request.group = args["group"].as<uint8_t>();
        request.expectedRev = doc["expected_rev"].as<uint32_t>();
    } else if (op == "set_opp") {
        if (!args["opp_mW"].is<uint32_t>() || !doc["expected_rev"].is<uint32_t>()) {
            send(link, id, "BAD_ARGS");
            return;
        }
        const uint32_t powerMw = args["opp_mW"].as<uint32_t>();
        if (powerMw < kOppStepMw || powerMw > kMaxOppMw) {
            send(link, id, "OPP_RANGE");
            return;
        }
        if (powerMw % kOppStepMw) {
            send(link, id, "BAD_STEP");
            return;
        }
        request.op = Op::SetOpp;
        request.oppRaw = powerMw / kOppStepMw;
        request.expectedRev = doc["expected_rev"].as<uint32_t>();
    } else if (op == "set_protection" || op == "match_ocp") {
        const bool match = op == "match_ocp";
        if (!args["ocp_mA"].is<uint16_t>() || (!match && !args["ovp_mV"].is<uint16_t>()) ||
            !doc["expected_rev"].is<uint32_t>()) {
            send(link, id, "BAD_ARGS");
            return;
        }
        const uint16_t v = args["ovp_mV"].as<uint16_t>();
        const uint16_t i = args["ocp_mA"].as<uint16_t>();
        if (!i || i > kMaxOcpMa || (!match && (v < kMinVoltageMv || v > kMaxOvpMv))) {
            send(link, id, "RANGE");
            return;
        }
        if (!match && v % Sx150::kMvPerCount) {
            send(link, id, "BAD_STEP");
            return;
        }
        request.op = match ? Op::MatchOcp : Op::SetProtection;
        request.target = {v, i};
        request.expectedRev = doc["expected_rev"].as<uint32_t>();
    } else if (op == "set_relay") {
        const uint8_t channel = args["channel"].as<uint8_t>();
        if (!args["channel"].is<uint8_t>() || (channel != 1 && channel != 2) ||
            !args["enabled"].is<bool>()) {
            send(link, id, "BAD_ARGS");
            return;
        }
        request.op = Op::Relay;
        request.channel = channel - 1;
        request.enabled = args["enabled"].as<bool>();
    } else if (op != "save_config") {
        send(link, id, "BAD_OP");
        return;
    }

    if (client.id != clientId) {
        client = {};
        client.id = clientId;
    }
    if (urgent ? stopReply_.has_value() : (pending_ || running_ || stopReply_ || stopPending_ || stopping_)) {
        send(link, id, "BUSY");
        return;
    }
    client.lastRequestId = id;
    if (urgent) {
        stopReply_ = std::move(request);
        requestStop(op != "output_off");
        return;
    }
    pending_ = std::move(request);
}

void Controller::finish(Request& request, const char* code) {
    auto message = response(request.id, code);
    auto& client = clients_[linkIndex(request.link)];
    if (client.id == request.clientId && generation(request.link) == request.generation) {
        client.cache[client.nextCache] = {request.id, std::move(request.canonical), message};
        client.nextCache = (client.nextCache + 1) % client.cache.size();
    }
    reply_(request.link, std::move(message), request.generation);
}

void Controller::setRelay(uint8_t channel, bool enabled) {
    if (relays_[channel] == enabled) return;
    digitalWrite(kRelayPins[channel], enabled ? HIGH : LOW);
    relays_[channel] = enabled;
    ++stateRev_;
}

void Controller::requestStop(bool releaseRelays) {
    update_.abort();
    if (updateRequest_) {
        sendUpdate(updateRequest_->owner, updateRequest_->id, "CANCELLED");
        updateRequest_.reset();
    }
    if (releaseRelays) {
        setRelay(0, false);
        setRelay(1, false);
    }
    abortRegular_ = true;
    if (!stopping_) stopPending_ = true;
    if (pending_) {
        finish(*pending_, "CANCELLED");
        pending_.reset();
    }
}

void Controller::processStop() {
    stopping_ = true;
    stopPending_ = false;
    sx_.setOn(false, true);
    const SxError error = refresh(true);
    const char* code = sxFailure(error);
    if (!code && sample_.on) code = "READBACK_MISMATCH";
    if (!code) bootOffConfirmed_ = true;
    recoverOff_ = code != nullptr;
    recoverAt_ = millis() + kStaleMs;
    if (error != SxError::Ok) invalidateSample();
    if (code) lastSxFault_ = code;
    stopping_ = false;
    abortRegular_ = false;
    if (stopReply_) {
        finish(*stopReply_, code);
        stopReply_.reset();
    }
}

void Controller::runRequest() {
    running_ = std::move(pending_);
    pending_.reset();
    const char* code = nullptr;
    switch (running_->op) {
        case Op::SetOutput: code = executeSet(*running_); break;
        case Op::SetProtection:
        case Op::SetOpp:
        case Op::MatchOcp: code = executeProtection(*running_); break;
        case Op::SelectGroup: code = selectGroup(*running_); break;
        case Op::Relay: setRelay(running_->channel, running_->enabled); break;
        case Op::Save: code = saveConfig(); break;
        default: code = "BAD_OP"; break;
    }
    finish(*running_, code ? code : abortRegular_ ? "CANCELLED" : nullptr);
    running_.reset();
}

bool Controller::outputsVerifiedOff() const {
    return bootOffConfirmed_ && !restorePending_ && !stopPending_ && !stopping_ && !recoverOff_ &&
           fresh() && !sample_.on && !relays_[0] && !relays_[1];
}

void Controller::sendUpdate(const UpdateOwner& owner, uint32_t id, const char* code,
                            const JsonDocument* data) {
    if (generation(owner.link) != owner.generation) return;
    JsonDocument message;
    message["v"] = kProtocol;
    message["type"] = "reply";
    message["id"] = id;
    message["ok"] = code == nullptr;
    if (code) message["fault"]["code"] = code;
    if (data && !data->isNull()) message["data"].set(data->as<JsonVariantConst>());
    reply_(owner.link, encode(message), owner.generation);
}

void Controller::runUpdate() {
    auto request = std::move(*updateRequest_);
    updateRequest_.reset();
    if (generation(request.owner.link) != request.owner.generation) return;

    processingUpdate_ = true;
    const bool verifyOff = request.op == UpdateOp::Begin || request.op == UpdateOp::Finish ||
                           request.op == UpdateOp::Confirm;
    const char* code = nullptr;
    if (request.op != UpdateOp::Abort && !outputsVerifiedOff()) code = "OTA_OUTPUT";
    if (!code && verifyOff && (refresh() != SxError::Ok || !outputsVerifiedOff())) code = "OTA_OUTPUT";
    JsonDocument data;
    if (generation(request.owner.link) == request.owner.generation) {
        if (code) {
            update_.abort();
        } else {
            code = update_.run(request.op, request.owner, request.document["args"].as<JsonObjectConst>(),
                               data.to<JsonObject>());
        }
        sendUpdate(request.owner, request.id, code, &data);
    }
    processingUpdate_ = false;
}

const char* Controller::executeSet(const Request& request) {
    if (!bootOffConfirmed_ || recoverOff_) return "INITIALIZING";
    if (!fresh()) return "SX_TIMEOUT";
    if (request.expectedRev != stateRev_) return "REV_CONFLICT";
    if (const char* code = sxFailure(refresh())) return writeFailure(code);
    if (request.expectedRev != stateRev_) return "REV_CONFLICT";
    const uint8_t group = sample_.group;
    if (request.enabled && sample_.protect) return "MODULE_PROTECT";

    const bool live = sample_.on && request.enabled;
    if (live && !allowedTarget(sample_.target)) return writeFailure("RANGE");
    if (sample_.on && !request.enabled) {
        if (const char* code = sxFailure(sx_.setOn(false))) return writeFailure(code);
        if (const char* code = sxFailure(refresh())) return writeFailure(code);
        if (sample_.on || sample_.group != group) return writeFailure("READBACK_MISMATCH");
    }

    if (request.enabled) {
        if (const char* code = sxFailure(refreshProfile())) return writeFailure(code);
        if (const char* code = enableFault()) {
            return live ? writeFailure(code) : code;
        }
    }
    if (const char* code = changeTarget(request.target, live)) return writeFailure(code);
    if (live && !sample_.on && sample_.protect) return "MODULE_PROTECT";
    if (!sameTarget(request.target, sample_.target) || sample_.on != live || sample_.group != group) {
        return writeFailure("READBACK_MISMATCH");
    }
    if (request.enabled) {
        if (const char* code = enableFault()) return writeFailure(code);
        if (!live) {
            if (const char* code = sxFailure(sx_.setOn(true))) return writeFailure(code);
            if (const char* code = sxFailure(refresh())) return writeFailure(code);
            if (const char* code = enableFault()) return writeFailure(code);
            if (!sample_.on || sample_.group != group || !sameTarget(request.target, sample_.target)) {
                return writeFailure("READBACK_MISMATCH");
            }
        }
    }
    return nullptr;
}

const char* Controller::selectGroup(const Request& request) {
    if (!bootOffConfirmed_ || recoverOff_) return "INITIALIZING";
    if (request.expectedRev != stateRev_) return "REV_CONFLICT";
    if (const char* code = sxFailure(refresh())) return code;
    if (request.expectedRev != stateRev_) return "REV_CONFLICT";
    if (sample_.group == request.group) return nullptr;

    bool autoOn;
    if (const char* code = sxFailure(sx_.recallState(request.group, autoOn))) return code;
    if (autoOn) return "GROUP_RECALL_ON";
    if (sample_.on) {
        if (const char* code = sxFailure(sx_.setOn(false))) return writeFailure(code);
        if (const char* code = sxFailure(refresh())) return writeFailure(code);
        if (sample_.on) return writeFailure("READBACK_MISMATCH");
    }
    if (const char* code = sxFailure(sx_.selectGroup(request.group))) return writeFailure(code);
    for (unsigned attempt = 0; attempt < 4; ++attempt) {
        if (attempt) delay(35);
        if (const char* code = sxFailure(refresh())) return writeFailure(code);
        if (sample_.on) return writeFailure("READBACK_MISMATCH");
        if (sample_.group == request.group) return sxFailure(refreshProfile());
    }
    return writeFailure("READBACK_MISMATCH");
}

const char* Controller::executeProtection(const Request& request) {
    if (request.expectedRev != stateRev_) return "REV_CONFLICT";
    if (const char* code = sxFailure(refresh())) return code;
    const uint8_t group = sample_.group;
    if (request.op != Op::MatchOcp && sample_.on) return "OUTPUT_MUST_BE_OFF";
    if (const char* code = sxFailure(refreshProfile())) return code;
    if (request.expectedRev != stateRev_) return "REV_CONFLICT";
    if (abortRegular_) return "CANCELLED";

    const Target threshold = request.target;
    if (request.op == Op::MatchOcp) {
        uint16_t step;
        if (threshold.currentMa == profile_.ocpRaw) step = 1;
        else if (threshold.currentMa == uint32_t(profile_.ocpRaw) * Sx150::kDocumentedMaPerCount) {
            step = Sx150::kDocumentedMaPerCount;
        } else {
            return "OCP_UNIT_MISMATCH";
        }
        return saveOcpUnit(step) ? nullptr : "NVS_ERROR";
    }
    const bool setOpp = request.op == Op::SetOpp;
    SxError written;
    uint16_t ovpRaw = profile_.ovpRaw;
    uint16_t ocpRaw = profile_.ocpRaw;
    if (setOpp) {
        if (request.oppRaw == profile_.oppRaw) return nullptr;
        written = sx_.setOpp(group, request.oppRaw);
    } else {
        if (!ocpMaPerCount_) return "OCP_UNIT_UNKNOWN";
        if (threshold.currentMa % ocpMaPerCount_) return "BAD_STEP";
        if (!saveOcpUnit(ocpMaPerCount_)) return "NVS_ERROR";
        ovpRaw = threshold.voltageMv / Sx150::kMvPerCount;
        ocpRaw = threshold.currentMa / ocpMaPerCount_;
        if (ovpRaw == profile_.ovpRaw && ocpRaw == profile_.ocpRaw) return nullptr;
        written = sx_.setProtection(group, ovpRaw, ocpRaw);
    }
    if (const char* code = sxFailure(written)) {
        profileKnown_ = false;
        return writeFailure(code);
    }
    if (const char* code = sxFailure(refreshProfile())) return writeFailure(code);
    const bool verified = setOpp ? profile_.oppRaw == request.oppRaw
                                 : profile_.ovpRaw == ovpRaw && profile_.ocpRaw == ocpRaw;
    if (!verified) return writeFailure("READBACK_MISMATCH");
    if (const char* code = sxFailure(refresh())) return writeFailure(code);
    if (sample_.on || sample_.group != group) return writeFailure("READBACK_MISMATCH");
    return nullptr;
}

const char* Controller::changeTarget(Target target, bool live) {
    const Target old = sample_.target;
    if (sameTarget(old, target)) return nullptr;
    if (!live) {
        if (const char* code = sxFailure(sx_.setpoints(target))) return code;
    } else {
        if (target.currentMa < old.currentMa) {
            if (const char* code = sxFailure(sx_.setCurrent(target.currentMa))) return code;
        }
        if (target.voltageMv != old.voltageMv) {
            if (const char* code = sxFailure(sx_.setVoltage(target.voltageMv))) return code;
        }
        if (target.currentMa > old.currentMa) {
            if (const char* code = sxFailure(sx_.setCurrent(target.currentMa))) return code;
        }
    }
    return sxFailure(refresh());
}

const char* Controller::saveConfig() {
    if (const char* code = sxFailure(refresh())) return code;
    if (abortRegular_) return "CANCELLED";
    if (!fresh()) return "SX_TIMEOUT";
    if (sample_.group != 0) return "M0_REQUIRED";
    if (!allowedTarget(sample_.target)) return "RANGE";
    const uint32_t packed = (uint32_t(sample_.target.voltageMv) << 16) | sample_.target.currentMa;
    return preferences_.putUInt("setpoints_ma", packed) == sizeof(packed) ? nullptr : "NVS_ERROR";
}

void Controller::restoreConfig() {
    restorePending_ = false;
    if (!fresh() || sample_.on || sample_.group != 0) return;
    const uint32_t packed = preferences_.getUInt("setpoints_ma", 0);
    Target saved{uint16_t(packed >> 16), uint16_t(packed)};
    if (!packed) {
        // Recover the raw limit stored via the 1.0.0 conversion before
        // applying the connected module's current unit.
        const uint32_t legacy = preferences_.getUInt("setpoints", 0);
        const uint16_t oldMa = uint16_t(legacy);
        if (oldMa > kMaxCurrentMa || oldMa % Sx150::kDocumentedMaPerCount) return;
        const uint16_t raw = oldMa / Sx150::kDocumentedMaPerCount;
        saved = {uint16_t(legacy >> 16), uint16_t(raw * sx_.currentSetMaPerCount())};
    }
    if (!allowedTarget(saved) || !sx_.canEncode(saved)) return;
    if (const char* code = changeTarget(saved, false)) {
        writeFailure(code);
        return;
    }
    if (sample_.on || !sameTarget(saved, sample_.target)) writeFailure("READBACK_MISMATCH");
}

SxError Controller::refresh(bool urgent) {
    SxSample current;
    const SxError error = sx_.sample(current, urgent);
    if (error != SxError::Ok) {
        if (error != SxError::Cancelled) lastSxFault_ = sxFailure(error);
        return error;
    }
    const bool oldInputOkay = normalInput();
    if (hasSample_ && (sample_.group != current.group || sample_.model != current.model ||
                       sample_.version != current.version)) {
        profileKnown_ = false;
        nextProfile_ = millis();
        ++stateRev_;
    }
    if (!sampleValid_ || !sameTarget(sample_.target, current.target) || sample_.on != current.on ||
        sample_.protect != current.protect || sample_.group != current.group) ++stateRev_;
    sample_ = current;
    hasSample_ = sampleValid_ = true;
    if (oldInputOkay != normalInput()) ++stateRev_;
    lastSxFault_ = nullptr;
    sxException_ = 0;
    return SxError::Ok;
}

SxError Controller::refreshProfile() {
    SxProfile current;
    const SxError error = sx_.profile(sample_.group, current);
    if (error == SxError::Ok) {
        const uint16_t previousUnit = ocpMaPerCount_;
        resolveOcpUnit(current.ocpRaw);
        if (!profileKnown_ || profile_.oppRaw != current.oppRaw ||
            profile_.powerOnRaw != current.powerOnRaw || profile_.lvpRaw != current.lvpRaw ||
            profile_.ovpRaw != current.ovpRaw || profile_.ocpRaw != current.ocpRaw ||
            previousUnit != ocpMaPerCount_) ++stateRev_;
        profile_ = current;
        profileKnown_ = true;
    } else if (error != SxError::Cancelled && profileKnown_) {
        profileKnown_ = false;
        ++stateRev_;
    }
    return error;
}

void Controller::resolveOcpUnit(uint16_t raw) {
    const uint32_t identity = (uint32_t(sample_.model) << 16) | sample_.version;
    if (identity != ocpUnitIdentity_) {
        ocpUnitIdentity_ = identity;
        const uint64_t stored = preferences_.getULong64("sx_ocp_unit", 0);
        const uint8_t step = uint8_t(stored);
        ocpMaPerCount_ = (stored >> 8) == identity &&
                         (step == 1 || step == Sx150::kDocumentedMaPerCount) ? step : 0;
    }
    // A raw value above 820 exceeds the module's 8.2 A range at 10 mA/count.
    if (raw > kMaxOcpMa / Sx150::kDocumentedMaPerCount && raw <= kMaxOcpMa) ocpMaPerCount_ = 1;
}

bool Controller::saveOcpUnit(uint16_t maPerCount) {
    const uint64_t stored = (uint64_t(ocpUnitIdentity_) << 8) | maPerCount;
    if (preferences_.getULong64("sx_ocp_unit", 0) != stored &&
        preferences_.putULong64("sx_ocp_unit", stored) != sizeof(stored)) return false;
    if (ocpMaPerCount_ != maPerCount) ++stateRev_;
    ocpMaPerCount_ = maPerCount;
    return true;
}

void Controller::invalidateSample() {
    if (sampleValid_) ++stateRev_;
    sampleValid_ = false;
}

bool Controller::fresh() const {
    return sampleValid_ && millis() - sample_.sampledMs < kStaleMs;
}

bool Controller::normalInput() const {
    return sample_.inMv >= kMinInputMv && sample_.inMv <= kMaxInputMv &&
           (!profileKnown_ || sample_.inMv > uint32_t(profile_.lvpRaw) * 10);
}

const char* Controller::enableFault() const {
    if (!fresh()) return "SX_TIMEOUT";
    if (sample_.protect) return "MODULE_PROTECT";
    if (!normalInput()) return "INPUT_UNVERIFIED";
    if (!profileKnown_ || !profile_.ready()) return "SX_CONFIG";
    return nullptr;
}

const char* Controller::sxFailure(SxError error) {
    switch (error) {
        case SxError::Ok: return nullptr;
        case SxError::Timeout: return "SX_TIMEOUT";
        case SxError::BadFrame: return "SX_BAD_FRAME";
        case SxError::Exception:
            sxException_ = sx_.exceptionCode();
            return "SX_EXCEPTION";
        case SxError::Cancelled: return "CANCELLED";
    }
    return "SX_TIMEOUT";
}

const char* Controller::writeFailure(const char* code) {
    if (strcmp(code, "CANCELLED") != 0) {
        lastSxFault_ = code;
        // A write with a missing acknowledgement may already have taken effect.
        invalidateSample();
        requestStop(false);
    }
    return code;
}

void Controller::tick() {
    const uint32_t now = millis();
    update_.tick(now);
    if (sampleValid_ && !fresh()) {
        invalidateSample();
        if (!recoverOff_) requestStop(false);
    }
    if (stopPending_ || (recoverOff_ && due(now, recoverAt_))) {
        processStop();
        return;
    }
    if (bootOffConfirmed_ && restorePending_) {
        restoreConfig();
        return;
    }
    if (updateRequest_) {
        runUpdate();
        return;
    }
    if (pending_) {
        runRequest();
        return;
    }
    if (due(now, nextPoll_)) {
        // Poll slightly faster than the shortest push interval to tolerate scheduling jitter.
        const uint32_t streamPeriod = streamsPaused() ? kDefaultStreamMs
                                                    : std::min(streamMs(Link::Usb), streamMs(Link::Ble));
        const uint32_t period = std::min(kPollMs, streamPeriod * 4 / 5);
        nextPoll_ = now + period;
        const SxError error = refresh();
        if (sx_.failedAttempts() >= 3 && !recoverOff_) {
            invalidateSample();
            requestStop(false);
        }
        if (error == SxError::Ok && sample_.on &&
            (update_.locksOutputs() || !normalInput() || !allowedTarget(sample_.target) || sample_.protect ||
             !profileKnown_ || !profile_.ready()) && !recoverOff_) {
            requestStop(false);
        }
        if (error == SxError::Ok && due(now, nextProfile_) && !stopPending_) {
            nextProfile_ = now + 1500;
            refreshProfile();
        }
    }
}

void Controller::fillInfo(JsonObject info) const {
    info["v"] = kProtocol;
    info["firmware"] = kFirmware;
    info["device_id"] = name_;
    info["esp_module"] = "ESP32-WROOM-32E";
    info["sx_address"] = sxAddress_;
    info["sx_baud"] = sxBaud_;
    info["usb_baud"] = kUsbBaud;
    info["relays"] = 2;
    info["max_output_mW"] = kMaxOutputMw;
    info["min_voltage_mV"] = kMinVoltageMv;
    info["max_voltage_mV"] = kMaxVoltageMv;
    info["max_current_mA"] = kMaxCurrentMa;
    info["step"] = kStep;
    info["input_current_supported"] = false;
    info["input_power_supported"] = false;
    info["protection_write"] = true;
    info["opp_write"] = true;
    info["output_off"] = true;
    info["direct_control"] = true;
    info["group_select"] = true;
    info["stream_period"] = true;
    info["sx_model_raw"] = nullptr;
    info["sx_version_raw"] = nullptr;
    if (hasSample_) {
        info["sx_model_raw"] = sample_.model;
        info["sx_version_raw"] = sample_.version;
    }
}

void Controller::fillState(JsonDocument& doc) const {
    const bool valid = fresh();
    const bool verified = valid && !stopPending_ && !stopping_;
    doc["v"] = kProtocol;
    doc["state_rev"] = stateRev_;
    doc["uptime_ms"] = millis();
    doc["mode"] = !bootOffConfirmed_ ? "initializing" :
                  (stopPending_ || stopping_) ? "stopping" :
                  !valid ? "unknown" : sample_.protect ? "protect" : sample_.on ? "running" : "off";
    auto applied = doc["applied"].to<JsonObject>();
    applied["enabled"] = nullptr;
    if (verified) applied["enabled"] = sample_.on;
    applied["relay1"] = relays_[0];
    applied["relay2"] = relays_[1];
    applied["voltage_mV"] = nullptr;
    applied["current_mA"] = nullptr;
    applied["group"] = nullptr;
    if (hasSample_) {
        applied["voltage_mV"] = sample_.target.voltageMv;
        applied["current_mA"] = sample_.target.currentMa;
        applied["group"] = sample_.group;
    }
    auto measured = doc["measured"].to<JsonObject>();
    measured["fresh"] = valid;
    if (hasSample_) {
        measured["sampled_ms"] = sample_.sampledMs;
        measured["voltage_mV"] = sample_.outMv;
        measured["current_mA"] = sample_.outMa;
        measured["power_mW"] = sample_.outMw;
        measured["input_mV"] = sample_.inMv;
        measured["cvcc"] = sample_.cvcc == 0 ? "CV" : sample_.cvcc == 1 ? "CC" : "unknown";
    }
    if (profileKnown_) {
        auto protection = doc["protection"].to<JsonObject>();
        protection["ovp_mV"] = uint32_t(profile_.ovpRaw) * Sx150::kMvPerCount;
        protection["lvp_mV"] = uint32_t(profile_.lvpRaw) * Sx150::kMvPerCount;
        protection["opp_mW"] = uint32_t(profile_.oppRaw) * kOppStepMw;
        protection["power_on_raw"] = profile_.powerOnRaw;
        protection["ocp_mA"] = nullptr;
        protection["ocp_step_mA"] = nullptr;
        if (ocpMaPerCount_) {
            protection["ocp_mA"] = uint32_t(profile_.ocpRaw) * ocpMaPerCount_;
            protection["ocp_step_mA"] = ocpMaPerCount_;
        }
    }
    auto fault = doc["fault"].to<JsonObject>();
    const char* code = lastSxFault_ ? lastSxFault_ : valid ? enableFault() : "SX_TIMEOUT";
    fault["code"] = code;
    fault["sx_protect_raw"] = nullptr;
    if (hasSample_) fault["sx_protect_raw"] = sample_.protect;
    if (sxException_) fault["modbus_exception"] = sxException_;
}

Message Controller::response(uint32_t id, const char* code, const JsonDocument* data) const {
    JsonDocument doc;
    fillState(doc);
    doc["type"] = "reply";
    doc["id"] = nullptr;
    if (id) doc["id"] = id;
    doc["ok"] = code == nullptr;
    if (code) doc["fault"]["code"] = code;
    if (data) doc["data"].set(data->as<JsonVariantConst>());
    return encode(doc);
}

void Controller::send(Link link, uint32_t id, const char* code, const JsonDocument* data) {
    reply_(link, response(id, code, data), generation(link));
}

Message Controller::infoMessage() const {
    JsonDocument doc;
    fillInfo(doc.to<JsonObject>());
    return encode(doc);
}

Message Controller::stateMessage() const {
    JsonDocument doc;
    fillState(doc);
    doc["type"] = "state";
    return encode(doc);
}

}  // namespace pm
