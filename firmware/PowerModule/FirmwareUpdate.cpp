#include "FirmwareUpdate.h"

#include <Arduino.h>
#include <cstring>
#include <esp_app_desc.h>
#include <esp_app_format.h>
#include <mbedtls/base64.h>

extern "C" bool verifyRollbackLater() {
    return true;
}

namespace pm {
namespace {

constexpr uint32_t kUpdateIdleMs = 45000;
constexpr uint32_t kVerifyBootMs = 120000;
constexpr uint32_t kRestartMs = 1000;
constexpr size_t kIdentityOffset = sizeof(esp_image_header_t) + sizeof(esp_image_segment_header_t) +
                                   sizeof(esp_app_desc_t);

struct ImageIdentity {
    char project[20] = "PowerModule-ESP32";
    char version[16]{};

    constexpr ImageIdentity() {
        static_assert(sizeof(kFirmware) <= sizeof(version));
        for (size_t i = 0; i < sizeof(kFirmware); ++i) version[i] = kFirmware[i];
    }
};

__attribute__((used, retain, section(".rodata_custom_desc")))
const ImageIdentity imageIdentity;

bool isProjectImage(const uint8_t* data, size_t length) {
    // The ESP32 image header carries its chip ID at offset 12.
    return length >= kIdentityOffset + sizeof(ImageIdentity) &&
           data[0] == ESP_IMAGE_HEADER_MAGIC && data[12] == 0 && data[13] == 0 &&
           std::memcmp(data + kIdentityOffset, imageIdentity.project, sizeof(imageIdentity.project)) == 0;
}

int hexDigit(char value) {
    if (value >= '0' && value <= '9') return value - '0';
    if (value >= 'a' && value <= 'f') return value - 'a' + 10;
    return -1;
}

bool readHash(JsonString text, std::array<uint8_t, 32>& hash) {
    if (text.isNull() || text.size() != hash.size() * 2) return false;
    for (size_t i = 0; i < hash.size(); ++i) {
        const int high = hexDigit(text.c_str()[i * 2]);
        const int low = hexDigit(text.c_str()[i * 2 + 1]);
        if (high < 0 || low < 0) return false;
        hash[i] = uint8_t(high * 16 + low);
    }
    return true;
}

}  // namespace

UpdateOp updateOp(std::string_view name) {
    if (name == "ota_info") return UpdateOp::Info;
    if (name == "ota_begin") return UpdateOp::Begin;
    if (name == "ota_write") return UpdateOp::Write;
    if (name == "ota_finish") return UpdateOp::Finish;
    if (name == "ota_abort") return UpdateOp::Abort;
    if (name == "ota_confirm") return UpdateOp::Confirm;
    return UpdateOp::None;
}

void FirmwareUpdate::boot() {
    const auto* running = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    if (running && esp_ota_get_state_partition(running, &state) == ESP_OK &&
        state == ESP_OTA_IMG_PENDING_VERIFY) verifyAt_ = millis() + kVerifyBootMs;
}

void FirmwareUpdate::tick(uint32_t now) {
    if (uploading() && due(now, idleAt_)) abort();
    if (restartAt_ && due(now, *restartAt_)) ESP.restart();
    if (verifyAt_ && due(now, *verifyAt_)) {
        verifyAt_ = now + kVerifyBootMs;
        esp_ota_mark_app_invalid_rollback_and_reboot();
    }
}

void FirmwareUpdate::describe(JsonObject data) const {
    const auto* running = esp_ota_get_running_partition();
    const auto* next = esp_ota_get_next_update_partition(running);
    if (running) data["slot"] = running->label;
    if (next) data["next_slot"] = next->label;
    data["max_size"] = next ? next->size : 0;
    data["max_chunk"] = kChunkBytes;
    data["pending_verify"] = verifyAt_.has_value();
    data["busy"] = uploading() || finishing();
}

bool FirmwareUpdate::owns(const UpdateOwner& owner) const {
    return uploading() && owner_.link == owner.link && owner_.generation == owner.generation &&
           owner_.clientId == owner.clientId;
}

void FirmwareUpdate::clearUpload() {
    target_ = nullptr;
    mbedtls_sha256_free(&hash_);
}

void FirmwareUpdate::abort() {
    if (!uploading()) return;
    esp_ota_abort(handle_);
    clearUpload();
}

void FirmwareUpdate::disconnected(Link link, uint32_t generation) {
    if (uploading() && owner_.link == link && owner_.generation != generation) abort();
}

const char* FirmwareUpdate::run(UpdateOp op, const UpdateOwner& owner, JsonObjectConst args,
                                JsonObject data) {
    if (op == UpdateOp::Begin) return start(owner, args, data);
    if (op == UpdateOp::Confirm) return confirm(args);
    if (op == UpdateOp::Abort && !uploading() && !finishing()) return nullptr;
    if (!uploading()) return "OTA_INACTIVE";
    if (!owns(owner)) return "OTA_BUSY";
    if (op == UpdateOp::Abort) {
        abort();
        return nullptr;
    }
    if (op == UpdateOp::Write) return write(args, data);
    if (op == UpdateOp::Finish) return finish(data);
    return "BAD_OP";
}

const char* FirmwareUpdate::start(const UpdateOwner& owner, JsonObjectConst args, JsonObject data) {
    if (locksOutputs()) return "OTA_BUSY";
    if (!args["size"].is<uint32_t>() || !readHash(args["sha256"].as<JsonString>(), expectedHash_))
        return "BAD_ARGS";
    const auto* running = esp_ota_get_running_partition();
    const auto* next = esp_ota_get_next_update_partition(running);
    if (!running || !next || next->address == running->address) return "OTA_NO_SLOT";
    const size_t size = args["size"].as<uint32_t>();
    if (size < kIdentityOffset + sizeof(ImageIdentity) || size > next->size) return "OTA_BAD_IMAGE";
    if (esp_ota_begin(next, OTA_WITH_SEQUENTIAL_WRITES, &handle_) != ESP_OK) return "OTA_FLASH";
    target_ = next;
    mbedtls_sha256_init(&hash_);
    if (mbedtls_sha256_starts(&hash_, 0)) {
        abort();
        return "OTA_FLASH";
    }
    owner_ = owner;
    total_ = size;
    received_ = lastLength_ = 0;
    idleAt_ = millis() + kUpdateIdleMs;
    data["offset"] = 0;
    return nullptr;
}

const char* FirmwareUpdate::write(JsonObjectConst args, JsonObject data) {
    const auto text = args["data"].as<JsonString>();
    if (!args["offset"].is<uint32_t>() || text.isNull()) return "BAD_ARGS";
    const size_t offset = args["offset"].as<uint32_t>();
    std::array<uint8_t, kChunkBytes> chunk;
    size_t length = 0;
    if (mbedtls_base64_decode(chunk.data(), chunk.size(), &length,
                              reinterpret_cast<const uint8_t*>(text.c_str()), text.size()) || !length)
        return "BAD_ARGS";
    if (offset == received_) {
        if (length > total_ - received_) return "OTA_BAD_OFFSET";
        if (!offset && !isProjectImage(chunk.data(), length)) {
            abort();
            return "OTA_BAD_IMAGE";
        }
        if (esp_ota_write(handle_, chunk.data(), length) != ESP_OK ||
            mbedtls_sha256_update(&hash_, chunk.data(), length)) {
            abort();
            return "OTA_FLASH";
        }
        received_ += length;
        lastLength_ = length;
        std::memcpy(lastChunk_.data(), chunk.data(), length);
    } else if (offset != received_ - lastLength_ || length != lastLength_ ||
               std::memcmp(lastChunk_.data(), chunk.data(), length) != 0) {
        return "OTA_BAD_OFFSET";
    }
    idleAt_ = millis() + kUpdateIdleMs;
    data["offset"] = received_;
    return nullptr;
}

const char* FirmwareUpdate::finish(JsonObject data) {
    if (received_ != total_) return "OTA_INCOMPLETE";
    std::array<uint8_t, 32> digest;
    if (mbedtls_sha256_finish(&hash_, digest.data()) || digest != expectedHash_) {
        abort();
        return "OTA_CHECKSUM";
    }
    const auto* partition = target_;
    const auto result = esp_ota_end(handle_);
    clearUpload();
    if (result != ESP_OK) return "OTA_BAD_IMAGE";
    if (esp_ota_set_boot_partition(partition) != ESP_OK) return "OTA_FLASH";
    restartAt_ = millis() + kRestartMs;
    data["slot"] = partition->label;
    return nullptr;
}

const char* FirmwareUpdate::confirm(JsonObjectConst args) {
    const auto* running = esp_ota_get_running_partition();
    const char* slot = args["slot"].as<const char*>();
    if (!running || !slot || std::strcmp(slot, running->label) != 0) return "OTA_BOOT";
    if (verifyAt_) {
        if (esp_ota_mark_app_valid_cancel_rollback() != ESP_OK) return "OTA_BOOT";
        verifyAt_.reset();
    }
    return nullptr;
}

}  // namespace pm
