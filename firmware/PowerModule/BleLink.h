#pragma once

#include <NimBLEDevice.h>
#include <atomic>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include "Protocol.h"

namespace pm {

class BleLink final : private NimBLEServerCallbacks, private NimBLECharacteristicCallbacks {
public:
    bool begin(const char* name, const Message& info);
    void tick();
    const char* clearBonds(unsigned& cleared);
    uint32_t generation() const { return generation_.load(); }
    Receive receive(Message& message);
    void reply(Message message, uint32_t generation);
    bool wantsState() const;
    void offerState(Message message);
    uint16_t minStreamMs() const;

private:
    struct Packet {
        uint32_t generation;
        uint32_t receivedAt;
        uint16_t mtu;
        uint16_t size;
        uint8_t bytes[kMaxGattValue];
    };
    struct SendCursor {
        uint16_t number = 0;
        size_t stride = 0;
        uint8_t index = 0;
    };

    void onConnect(NimBLEServer*, NimBLEConnInfo& info) override;
    void onDisconnect(NimBLEServer*, NimBLEConnInfo& info, int reason) override;
    void onAuthenticationComplete(NimBLEConnInfo& info) override;
    void onMTUChange(uint16_t mtu, NimBLEConnInfo& info) override;
    void onSubscribe(NimBLECharacteristic* characteristic, NimBLEConnInfo& info,
                     uint16_t value) override;
    void onWrite(NimBLECharacteristic* characteristic, NimBLEConnInfo& info) override;
    void onStatus(NimBLECharacteristic* characteristic, int code) override;
    void disconnect();
    void sendOne(uint32_t now);
    size_t fragment(SendCursor& cursor, const Message& message, uint8_t* frame);
    bool advance(SendCursor& cursor, const Message& message);

    bool ready_ = false;
    NimBLEServer* server_ = nullptr;
    NimBLECharacteristic* rsp_ = nullptr;
    NimBLECharacteristic* state_ = nullptr;
    QueueHandle_t packets_ = nullptr;
    std::atomic<uint32_t> generation_{0};
    std::atomic<uint16_t> handle_{BLE_HS_CONN_HANDLE_NONE};
    std::atomic<uint16_t> mtu_{23};
    std::atomic<bool> secure_{false};
    std::atomic<bool> rspSubscribed_{false};
    std::atomic<bool> stateSubscribed_{false};
    std::atomic<uint32_t> securityUntil_{0};
    std::atomic<int> indicationStatus_{0};
    uint32_t lastGeneration_ = 0;
    uint32_t nextGeneration_ = 0;  // NimBLE host task only.
    uint16_t nextMessage_ = 0;
    FragmentReader incoming_;
    Outbox replies_;
    Message stateMessage_;
    SendCursor replyCursor_;
    SendCursor stateCursor_;
    bool indicating_ = false;
    uint32_t indicateUntil_ = 0;
    uint32_t nextNotify_ = 0;
    uint32_t stateUntil_ = 0;
};

}  // namespace pm
