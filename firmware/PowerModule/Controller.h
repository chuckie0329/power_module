#pragma once

#include <Preferences.h>
#include <optional>
#include "FirmwareUpdate.h"
#include "Protocol.h"
#include "Sx150.h"

namespace pm {

using ReplySink = void (*)(Link, Message, uint32_t);

class Controller {
public:
    Controller(Sx150& sx, Preferences& preferences, ReplySink reply)
        : sx_(sx), preferences_(preferences), reply_(reply) {}

    void begin(const char* name, unsigned sxBaud, uint8_t sxAddress);
    void bleChanged(uint32_t generation, uint16_t minPeriod);
    void receive(Link link, const Message& line);
    void wireError(Link link, const char* code);
    void tick();
    bool allowRegularRtu() const { return !abortRegular_; }
    bool streamsPaused() const { return update_.pausesStreams(); }
    Message infoMessage() const;
    Message stateMessage() const;
    uint16_t streamMs(Link link) const;
    std::optional<uint32_t> streamSampleMs() const;
    uint32_t stateRevision() const { return stateRev_; }

private:
    enum class Op { SetOutput, SetProtection, SetOpp, MatchOcp, SelectGroup, Relay, Stop, Save };

    struct Request {
        Link link;
        uint32_t generation;
        uint32_t id;
        uint32_t clientId;
        Message canonical;
        Op op;
        Target target{};
        uint32_t expectedRev = 0;
        uint16_t oppRaw = 0;
        uint8_t channel = 0;
        uint8_t group = 0;
        bool enabled = false;
    };

    struct Cached {
        uint32_t id = 0;
        Message canonical;
        Message response;
    };

    struct UpdateRequest {
        UpdateOwner owner;
        uint32_t id;
        UpdateOp op;
        JsonDocument document;
    };

    struct RemoteClient {
        uint32_t id = 0;
        uint32_t lastRequestId = 0;
        size_t nextCache = 0;
        std::array<Cached, 6> cache;
    };

    void requestStop(bool releaseRelays);
    void processStop();
    void runRequest();
    void runUpdate();
    bool outputsVerifiedOff() const;
    void sendUpdate(const UpdateOwner& owner, uint32_t id, const char* code,
                    const JsonDocument* data = nullptr);
    const char* executeSet(const Request& request);
    const char* selectGroup(const Request& request);
    const char* executeProtection(const Request& request);
    bool saveOcpUnit(uint16_t maPerCount);
    void resolveOcpUnit(uint16_t raw);
    const char* changeTarget(Target target, bool live);
    const char* saveConfig();
    void restoreConfig();
    void setRelay(uint8_t channel, bool enabled);
    SxError refresh(bool urgent = false);
    SxError refreshProfile();
    void invalidateSample();
    bool fresh() const;
    bool normalInput() const;
    const char* enableFault() const;
    const char* sxFailure(SxError error);
    const char* writeFailure(const char* code);
    void fillState(JsonDocument& doc) const;
    void fillInfo(JsonObject info) const;
    Message response(uint32_t id, const char* code, const JsonDocument* data = nullptr) const;
    void send(Link link, uint32_t id, const char* code, const JsonDocument* data = nullptr);
    void finish(Request& request, const char* code);
    uint16_t minStreamMs(Link link) const;
    static size_t linkIndex(Link link) { return link == Link::Usb ? 0 : 1; }
    uint32_t generation(Link link) const { return link == Link::Ble ? bleGeneration_ : 0; }

    Sx150& sx_;
    Preferences& preferences_;
    ReplySink reply_;
    Message name_;
    unsigned sxBaud_ = kDefaultSxBaud;
    uint8_t sxAddress_ = kDefaultSxAddress;
    uint32_t bleGeneration_ = 0;
    uint16_t bleMinPeriod_ = kStandardStreamMinMs;
    std::array<RemoteClient, 2> clients_;
    uint16_t streamMs_[2] = {kDefaultStreamMs, kDefaultStreamMs};
    std::optional<Request> pending_;
    std::optional<Request> running_;
    std::optional<Request> stopReply_;
    FirmwareUpdate update_;
    std::optional<UpdateRequest> updateRequest_;
    bool processingUpdate_ = false;

    SxSample sample_{};
    SxProfile profile_{};
    uint32_t ocpUnitIdentity_ = 0;
    uint16_t ocpMaPerCount_ = 0;
    bool hasSample_ = false;
    bool sampleValid_ = false;
    bool profileKnown_ = false;
    bool bootOffConfirmed_ = false;
    bool stopPending_ = true;
    bool stopping_ = false;
    bool recoverOff_ = false;
    bool abortRegular_ = false;
    bool restorePending_ = true;
    bool relays_[2] = {};
    uint32_t stateRev_ = 1;
    uint32_t nextPoll_ = 0;
    uint32_t nextProfile_ = 0;
    uint32_t recoverAt_ = 0;
    uint8_t sxException_ = 0;
    const char* lastSxFault_ = nullptr;
};

}  // namespace pm
