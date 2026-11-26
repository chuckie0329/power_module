#pragma once

#include <NimBLEDevice.h>
#include <atomic>
#ifdef CONFIG_NIMBLE_CPP_IDF
#include <nimble/nimble_port.h>
#else
#include <nimble/porting/nimble/include/nimble/nimble_port.h>
#endif
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include "DiagnosticLog.h"
#include "BleQuality.h"
#include "Protocol.h"

namespace pm {

class BleLink final : private NimBLEServerCallbacks, private NimBLECharacteristicCallbacks {
public:
    explicit BleLink(DiagnosticLog& diagnostics) : diagnostics_(diagnostics) {}
    bool begin(const char* name, const Message& info);
    void tick(bool allowAdvertisingRecovery);
    const char* clearBonds(unsigned& cleared);
    uint32_t generation() const;
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
    enum class CloseReason : uint32_t {
        SecurityRejected = 1, RxOverflow, SecurityTimeout, ReplyOverflow,
        IndicationFailed, IndicationTimeout, NotSubscribed
    };
    void requestDisconnect(uint32_t generation, CloseReason reason);
    void clearConnection();
    static void runRecovery(ble_npl_event* event);
    void recover();
    void noteIndicationFailure(uint32_t generation);
    void noteNotificationFailure(uint32_t generation, uint32_t when, int rc);
    static constexpr uint32_t kReasonBits = 3;
    static constexpr uint32_t kReasonMask = (1u << kReasonBits) - 1;
    static constexpr uint32_t kRecoveryPeriodMs = 500;
    void sendOne(uint32_t now, uint32_t generation);
    size_t fragment(SendCursor& cursor, const Message& message, uint8_t* frame);
    bool advance(SendCursor& cursor, const Message& message);

    DiagnosticLog& diagnostics_;
    BleQuality bleQuality_;
    portMUX_TYPE bleQualityLock_ = portMUX_INITIALIZER_UNLOCKED;
    uint32_t nextRssi_ = 0;  // NimBLE host task only.
    bool ready_ = false;
    NimBLEServer* server_ = nullptr;
    NimBLECharacteristic* rsp_ = nullptr;
    NimBLECharacteristic* state_ = nullptr;
    QueueHandle_t packets_ = nullptr;
    std::atomic<uint32_t> generation_{0};
    std::atomic<uint32_t> closeRequest_{0};  // Connection generation and CloseReason.
    std::atomic<bool> allowAdvertisingRecovery_{false};
    std::atomic<bool> advertisingBusy_{false};
    std::atomic<bool> recoveryQueued_{false};
    ble_npl_event recoveryEvent_{};
    uint32_t nextRecovery_ = 0;  // Loop task only.
    uint32_t loggedRequest_ = 0;  // NimBLE host task only.
    int loggedResult_ = -1;
    bool advertisingFailed_ = false;
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
