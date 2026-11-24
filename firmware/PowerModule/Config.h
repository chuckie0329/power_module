#pragma once

#include <stddef.h>
#include <stdint.h>

namespace pm {

constexpr int kRelayPins[] = {16, 17};
constexpr int kLinkEnable = 27;
constexpr int kSxRx = 19;
constexpr int kSxTx = 18;
constexpr int kUsbRx = 33;
constexpr int kUsbTx = 32;
constexpr unsigned kUsbBaud = 921600;
constexpr unsigned kDefaultSxBaud = 9600;
constexpr unsigned kFastSxBaud = 115200;
constexpr uint8_t kDefaultSxAddress = 1;

constexpr uint16_t kMinVoltageMv = 500;
constexpr uint16_t kMaxVoltageMv = 40000;
constexpr uint16_t kMaxCurrentMa = 8000;
constexpr uint16_t kMaxOvpMv = 42000;
constexpr uint16_t kMaxOcpMa = 8200;
constexpr uint16_t kStep = 10;
constexpr uint32_t kMaxOutputMw = 75000;
constexpr uint32_t kMaxOppMw = 100000;
constexpr uint16_t kOppStepMw = 100;
constexpr uint16_t kMinInputMv = 18000;
constexpr uint16_t kMaxInputMv = 22000;
constexpr uint8_t kMaxGroup = 10;

constexpr uint32_t kPollMs = 160;
constexpr uint32_t kStaleMs = 1000;
constexpr uint16_t kDefaultStreamMs = 500;
constexpr uint16_t kFastStreamMs = 33;
constexpr uint16_t kStandardStreamMinMs = 200;
constexpr uint16_t kLowMtuBleStreamMs = 2500;
constexpr uint16_t kFastBleMtu = 247;
constexpr uint32_t kBlePasskey = 112233;
constexpr uint32_t kBleSecurityTimeoutMs = 60000;
constexpr uint32_t kFragmentMs = 5000;
constexpr size_t kMaxLine = 1024;  // Includes LF.
constexpr char kFirmware[] = "1.2.3";
constexpr uint8_t kProtocol = 1;

constexpr char kService[]   = "4d505301-7e64-4d4a-9f80-021a56c11000";
constexpr char kCommand[]   = "4d505301-7e64-4d4a-9f80-021a56c11001";
constexpr char kResponse[]  = "4d505301-7e64-4d4a-9f80-021a56c11002";
constexpr char kState[]     = "4d505301-7e64-4d4a-9f80-021a56c11003";
constexpr char kInfo[]      = "4d505301-7e64-4d4a-9f80-021a56c11004";
constexpr char kHeartbeat[] = "4d505301-7e64-4d4a-9f80-021a56c11005";

// All intervals are much smaller than half the millis() counter range.
inline bool due(uint32_t now, uint32_t deadline) {
    return static_cast<int32_t>(now - deadline) >= 0;
}

}  // namespace pm
