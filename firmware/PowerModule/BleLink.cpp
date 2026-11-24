#include "BleLink.h"
#include "BondRecovery.h"
#include <Arduino.h>
#include <cstring>

namespace pm {

bool BleLink::begin(const char* name, const Message& info) {
    packets_ = xQueueCreate(6, sizeof(Packet));
    if (!packets_ || !NimBLEDevice::init(name)) return false;
    NimBLEDevice::setMTU(kFastBleMtu);
    NimBLEDevice::setSecurityIOCap(BLE_HS_IO_DISPLAY_ONLY);
    NimBLEDevice::setSecurityAuth(true, true, true);
    NimBLEDevice::setSecurityPasskey(kBlePasskey);

    server_ = NimBLEDevice::createServer();
    server_->setCallbacks(this, false);
    server_->advertiseOnDisconnect(true);
    auto service = server_->createService(kService);
    auto cmd = service->createCharacteristic(kCommand, NIMBLE_PROPERTY::WRITE |
                                            NIMBLE_PROPERTY::WRITE_ENC, kMaxGattValue);
    rsp_ = service->createCharacteristic(kResponse, NIMBLE_PROPERTY::INDICATE, kMaxGattValue);
    state_ = service->createCharacteristic(kState, NIMBLE_PROPERTY::NOTIFY, kMaxGattValue);
    auto infoCharacteristic = service->createCharacteristic(kInfo, NIMBLE_PROPERTY::READ);
    // Older clients still write this characteristic.
    service->createCharacteristic(kHeartbeat, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_ENC, 4);
    cmd->setCallbacks(this);
    rsp_->setCallbacks(this);
    state_->setCallbacks(this);
    infoCharacteristic->setValue(info);

    if (!service->start()) return false;
    server_->start();
    auto advertising = NimBLEDevice::getAdvertising();
    advertising->addServiceUUID(kService);
    advertising->setName(name);
    ready_ = advertising->start();
    return ready_;
}

const char* BleLink::clearBonds(unsigned& cleared) {
    if (!ready_) return "BLE_UNAVAILABLE";
#if MYNEWT_VAL(BLE_STORE_MAX_BONDS)
    struct Bonds {
        std::atomic<uint16_t>& handle;
        NimBLEAdvertising* adv;
        ble_addr_t peers[MYNEWT_VAL(BLE_STORE_MAX_BONDS)]{};
        bool active() { return handle.load() != BLE_HS_CONN_HANDLE_NONE; }
        bool advertising() { return adv->isAdvertising(); }
        bool stopAdvertising() { return adv->stop(); }
        bool startAdvertising() { return adv->start(); }
        bool enumerate(int& count) {
            return ble_store_util_bonded_peers(peers, &count, MYNEWT_VAL(BLE_STORE_MAX_BONDS)) == 0;
        }
        bool connected(int i) { return ble_gap_conn_find_by_addr(&peers[i], nullptr) == 0; }
        // Unlike 2.3.8 deleteAllBonds(), unpair updates the controller resolving list.
        bool remove(int i) { return NimBLEDevice::deleteBond(NimBLEAddress(peers[i])); }
    } bonds{handle_, NimBLEDevice::getAdvertising()};
    return recoverBonds(bonds, cleared);
#else
    return "BLE_UNAVAILABLE";
#endif
}

void BleLink::onConnect(NimBLEServer* server, NimBLEConnInfo& info) {
    const auto handle = info.getConnHandle();
    if (handle_.load() != BLE_HS_CONN_HANDLE_NONE) {
        server->disconnect(handle);
        return;
    }
    secure_.store(false);
    rspSubscribed_.store(false);
    stateSubscribed_.store(false);
    mtu_.store(23);
    handle_.store(handle);
    securityUntil_.store(millis() + kBleSecurityTimeoutMs);
    if (++nextGeneration_ == 0) ++nextGeneration_;
    generation_.store(nextGeneration_);
    NimBLEDevice::startSecurity(handle);
}

void BleLink::onDisconnect(NimBLEServer*, NimBLEConnInfo& info, int) {
    if (info.getConnHandle() != handle_.load()) return;
    secure_.store(false);
    generation_.store(0);
    handle_.store(BLE_HS_CONN_HANDLE_NONE);
}

void BleLink::onAuthenticationComplete(NimBLEConnInfo& info) {
    if (info.getConnHandle() != handle_.load() || !generation_.load()) return;
    if (!info.isEncrypted() || !info.isAuthenticated() || !info.isBonded()) {
        disconnect();
        return;
    }
    secure_.store(true);
}

void BleLink::onMTUChange(uint16_t mtu, NimBLEConnInfo& info) {
    if (info.getConnHandle() == handle_.load()) mtu_.store(mtu);
}

void BleLink::onSubscribe(NimBLECharacteristic* c, NimBLEConnInfo& info, uint16_t value) {
    if (info.getConnHandle() != handle_.load()) return;
    if (c == rsp_) rspSubscribed_.store(value & 2);
    if (c == state_) stateSubscribed_.store(value & 1);
}

void BleLink::onWrite(NimBLECharacteristic* c, NimBLEConnInfo& info) {
    const auto generation = generation_.load();
    if (!generation || info.getConnHandle() != handle_.load() ||
        !secure_.load() || !info.isEncrypted() || !rspSubscribed_.load()) return;
    const auto value = c->getValue();
    Packet packet{};
    packet.generation = generation;
    packet.receivedAt = millis();
    packet.mtu = info.getMTU();
    if (value.size() <= sizeof(packet.bytes)) {
        packet.size = value.size();
        memcpy(packet.bytes, value.data(), packet.size);
    }
    if (xQueueSend(packets_, &packet, 0) != pdTRUE) disconnect();
}

void BleLink::onStatus(NimBLECharacteristic* c, int code) {
    // NimBLE only calls this for an indication after confirmation or failure.
    if (c == rsp_) indicationStatus_.store(code == BLE_HS_EDONE ? 1 : -1);
}

void BleLink::disconnect() {
    secure_.store(false);
    generation_.store(0);
    const auto handle = handle_.load();
    if (handle != BLE_HS_CONN_HANDLE_NONE) server_->disconnect(handle);
}

void BleLink::tick() {
    const auto now = millis();
    const auto generation = generation_.load();
    if (generation != lastGeneration_) {
        incoming_.reset();
        replies_.clear();
        stateMessage_.clear();
        replyCursor_ = {};
        stateCursor_ = {};
        indicating_ = false;
        lastGeneration_ = generation;
    }
    if (!generation) return;
    if (!secure_.load()) {
        if (due(now, securityUntil_.load())) disconnect();
        return;
    }
    sendOne(now);
}

Receive BleLink::receive(Message& line) {
    if (!packets_) return Receive::Incomplete;
    Packet packet;
    while (xQueueReceive(packets_, &packet, 0) == pdTRUE) {
        if (packet.generation != lastGeneration_ || packet.generation != generation_.load()) continue;
        const auto result = incoming_.push(packet.bytes, packet.size, packet.mtu, packet.receivedAt, line);
        if (result != Receive::Incomplete) return result;
    }
    return incoming_.expire(millis()) ? Receive::Bad : Receive::Incomplete;
}

void BleLink::reply(Message message, uint32_t generation) {
    if (generation && generation == generation_.load() && !replies_.push(std::move(message))) {
        disconnect();
    }
}

bool BleLink::wantsState() const {
    return secure_.load() && stateSubscribed_.load() && stateMessage_.empty();
}

uint16_t BleLink::minStreamMs() const {
    const uint16_t mtu = mtu_.load();
    if (mtu < 100) return kLowMtuBleStreamMs;
    return mtu >= kFastBleMtu ? kFastStreamMs : kStandardStreamMinMs;
}

void BleLink::offerState(Message message) {
    if (!wantsState()) return;
    stateMessage_ = std::move(message);
    stateCursor_ = {};
    stateUntil_ = millis() + kFragmentMs;
    nextNotify_ = millis();
}

size_t BleLink::fragment(SendCursor& cursor, const Message& message, uint8_t* frame) {
    if (!cursor.stride) {
        cursor.stride = gattValueLimit(mtu_.load()) - kFragmentHeader;
        cursor.number = ++nextMessage_;
    }
    return writeFragment(message, cursor.number, cursor.stride, cursor.index, frame);
}

bool BleLink::advance(SendCursor& cursor, const Message& message) {
    return size_t(++cursor.index) * cursor.stride >= message.size();
}

void BleLink::sendOne(uint32_t now) {
    const auto handle = handle_.load();
    if (handle == BLE_HS_CONN_HANDLE_NONE) return;
    if (!stateMessage_.empty() && (due(now, stateUntil_) || !stateSubscribed_.load())) {
        stateMessage_.clear();
    }
    if (indicating_) {
        const int status = indicationStatus_.exchange(0);
        if (status == 0 && !due(now, indicateUntil_)) return;
        if (status != 1) {
            disconnect();
            return;
        }
        indicating_ = false;
        if (advance(replyCursor_, replies_.front())) {
            replies_.pop();
            replyCursor_ = {};
        }
    }

    uint8_t frame[kMaxGattValue];
    if (!replies_.empty()) {
        if (!rspSubscribed_.load()) {
            disconnect();
            return;
        }
        const size_t size = fragment(replyCursor_, replies_.front(), frame);
        indicationStatus_.store(0);
        if (!rsp_->indicate(frame, size, handle)) {
            disconnect();
            return;
        }
        indicating_ = true;
        indicateUntil_ = now + 1500;
    } else if (!stateMessage_.empty() && due(now, nextNotify_)) {
        const size_t size = fragment(stateCursor_, stateMessage_, frame);
        nextNotify_ = now + (mtu_.load() >= kFastBleMtu ? 10 : 20);
        if (state_->notify(frame, size, handle) && advance(stateCursor_, stateMessage_)) {
            stateMessage_.clear();
        }
    }
}

}  // namespace pm
