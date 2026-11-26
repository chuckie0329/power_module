#include <Arduino.h>
#include <algorithm>
#include <driver/gpio.h>
#include <esp_mac.h>
#include <esp_random.h>
#include <esp_system.h>
#include "BleLink.h"
#include "Controller.h"

namespace {
using namespace pm;

bool serviceLinks();
void sendReply(Link link, Message message, uint32_t bleGeneration);
void sendDiagnostic(Message message);

Preferences storage;
DiagnosticLog diagnostics;
BleLink ble(diagnostics);
Sx150 sx(Serial2, serviceLinks);
Controller controller(sx, storage, sendReply, [](unsigned& count) { return ble.clearBonds(count); },
                      diagnostics, sendDiagnostic);
LineReader usbInput;
Outbox usbReplies;
Message usbDiagnostic;
Message usbTx;
size_t usbOffset = 0;
uint32_t lastUsbChunk = 0;
bool usbSeen = false;
bool linksReady = false;
uint32_t bleGeneration = 0;
char maintenance[48];
size_t maintenanceLength = 0;
bool maintenanceOverflow = false;

struct StateStream {
    uint32_t scheduledAt = 0;
    std::optional<uint32_t> lastSample;
    uint32_t lastRevision = 0;

    bool ready(Link link) {
        const uint32_t now = micros();
        const uint16_t periodMs = controller.streamMs(link);
        const uint32_t period = periodMs == kFastStreamMs ? 1000000 / 30 : periodMs * 1000u;
        const uint32_t elapsed = now - scheduledAt;
        if (elapsed < period) return false;
        const auto sample = controller.streamSampleMs();
        const uint32_t revision = controller.stateRevision();
        // A pending Modbus read can publish its sample as soon as it completes.
        if (sample && sample == lastSample && revision == lastRevision) return false;
        scheduledAt = now - elapsed % period;
        lastSample = sample;
        lastRevision = revision;
        return true;
    }
};

StateStream usbStream;
StateStream bleStream;

void sendReply(Link link, Message message, uint32_t generation) {
    if (link == Link::Ble) {
        ble.reply(std::move(message), generation);
    } else if (!usbReplies.push(std::move(message))) {
        Serial.println("UART1 reply queue full");
    }
}

void sendDiagnostic(Message message) {
    // Diagnostics use one low-priority slot, never the regular control queue.
    if (usbDiagnostic.empty()) usbDiagnostic = std::move(message);
}

void pumpUsbTx() {
    if (usbTx.empty()) {
        usbOffset = 0;
        if (!usbReplies.empty()) {
            usbTx = usbReplies.front();
            usbReplies.pop();
        } else if (!usbDiagnostic.empty()) {
            usbTx.swap(usbDiagnostic);
        } else if (usbSeen && !controller.streamsPaused() && usbStream.ready(Link::Usb)) {
            usbTx = controller.stateMessage();
        }
    }
    if (usbTx.empty()) return;
    const uint32_t now = micros();
    // Leave time for the USB bridge to drain its receive buffer between bursts.
    if (now - lastUsbChunk < 900) return;
    const size_t ready = Serial1.availableForWrite();
    const size_t count = std::min({ready, usbTx.size() - usbOffset, size_t(32)});
    if (!count) return;
    lastUsbChunk = now;
    usbOffset += Serial1.write(reinterpret_cast<const uint8_t*>(usbTx.data()) + usbOffset, count);
    if (usbOffset == usbTx.size()) usbTx.clear();
}

void maintain(const char* line) {
    if (strcmp(line, "info") == 0) {
        Serial.print(controller.infoMessage().c_str());
    } else if (strncmp(line, "sx ", 3) == 0) {
        unsigned address, baud;
        char extra;
        if (sscanf(line + 3, "%u %u %c", &address, &baud, &extra) != 2 ||
            address == 0 || address > 255 || (baud != kDefaultSxBaud && baud != kFastSxBaud)) {
            Serial.println("sx <address 1-255> <9600|115200>");
        } else {
            const uint32_t packed = address | (uint32_t(baud == kFastSxBaud) << 8);
            Serial.println(storage.putUInt("sx_uart", packed) == sizeof(packed)
                               ? "Saved; applies after reset" : "NVS_ERROR");
        }
    } else {
        Serial.println("Commands: info | sx <address> <baud>");
    }
}

void pumpMaintenance() {
    for (unsigned n = 0; n < 48; ++n) {
        const int byte = Serial.read();
        if (byte < 0) break;
        if (byte == '\n' || byte == '\r') {
            if (maintenanceLength && !maintenanceOverflow) {
                maintenance[maintenanceLength] = '\0';
                maintain(maintenance);
            }
            maintenanceLength = 0;
            maintenanceOverflow = false;
        } else if (maintenanceLength < sizeof(maintenance) - 1 && byte >= 32 && byte < 127) {
            maintenance[maintenanceLength++] = char(byte);
        } else {
            maintenanceOverflow = true;
        }
    }
}

bool serviceLinks() {
    if (!linksReady) return true;
    ble.tick(!controller.streamsPaused());
    const uint32_t generation = ble.generation();
    controller.bleChanged(generation, ble.minStreamMs());
    if (generation != bleGeneration) {
        bleGeneration = generation;
        bleStream = {micros(), std::nullopt, 0};
    }
    Message line;
    const auto bleResult = ble.receive(line);
    if (generation && generation == ble.generation()) {
        if (bleResult == Receive::Complete) controller.receive(Link::Ble, line);
        if (bleResult == Receive::Bad) controller.wireError(Link::Ble, "BAD_FRAGMENT");
    }
    for (unsigned n = 0; n < 128; ++n) {
        const int byte = Serial1.read();
        if (byte < 0) break;
        const auto result = usbInput.push(char(byte), line);
        if (result == Receive::Complete) {
            if (!usbSeen) usbStream.scheduledAt = micros();
            usbSeen = true;
            controller.receive(Link::Usb, line);
            break;
        }
        if (result == Receive::Bad) {
            controller.wireError(Link::Usb, "LINE_TOO_LONG");
            break;
        }
    }
    pumpUsbTx();
    if (!controller.streamsPaused() && ble.wantsState() && bleStream.ready(Link::Ble)) {
        ble.offerState(controller.stateMessage());
    }
    pumpMaintenance();
    return controller.allowRegularRtu();
}
}  // namespace

void setup() {
    for (int pin : {kRelayPins[0], kRelayPins[1], kLinkEnable}) {
        gpio_set_level(static_cast<gpio_num_t>(pin), 0);
        pinMode(pin, OUTPUT);
        digitalWrite(pin, LOW);
    }
    Serial.begin(115200);
    diagnostics.begin(esp_random(), millis(), static_cast<int32_t>(esp_reset_reason()));
    if (!storage.begin("power-module", false)) Serial.println("NVS_ERROR");
    const uint32_t uart = storage.getUInt("sx_uart", kDefaultSxAddress);
    const uint8_t address = uint8_t(uart) ? uint8_t(uart) : kDefaultSxAddress;
    const unsigned baud = (uart & 0x100) ? kFastSxBaud : kDefaultSxBaud;

    Serial1.setRxBufferSize(2048);
    Serial1.setTxBufferSize(2048);
    Serial1.begin(kUsbBaud, SERIAL_8N1, kUsbRx, kUsbTx);
    sx.begin(baud, address);
    digitalWrite(kLinkEnable, HIGH);

    uint8_t mac[6] = {};
    esp_efuse_mac_get_default(mac);
    char name[8];
    char deviceId[16];
    snprintf(name, sizeof(name), "PM-%02X%02X", mac[4], mac[5]);
    snprintf(deviceId, sizeof(deviceId), "PM-%02X%02X%02X%02X%02X%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    controller.begin(deviceId, baud, address);
    controller.tick();  // Attempt and read back SX OFF before advertising.
    const bool bleReady = ble.begin(name, controller.infoMessage());
    diagnostics.append(millis(), DiagEvent::AdvertisingInitial, bleReady);
    linksReady = true;
    Serial.printf("%s firmware %s, SX %u/%u, BLE %s\n",
                  name, kFirmware, unsigned(address), baud, bleReady ? "ready" : "unavailable");
}

void loop() {
    serviceLinks();
    controller.tick();
    delay(1);
}
