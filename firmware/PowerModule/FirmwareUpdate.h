#pragma once

#include <esp_ota_ops.h>
#include <mbedtls/sha256.h>
#include <optional>
#include <string_view>
#include "Protocol.h"

namespace pm {

enum class UpdateOp { None, Info, Begin, Write, Finish, Abort, Confirm };
UpdateOp updateOp(std::string_view name);

struct UpdateOwner {
    Link link;
    uint32_t generation;
    uint32_t clientId;
};

class FirmwareUpdate {
public:
    void boot();
    void tick(uint32_t now);
    void describe(JsonObject data) const;
    const char* run(UpdateOp op, const UpdateOwner& owner, JsonObjectConst args, JsonObject data);
    bool owns(const UpdateOwner& owner) const;
    bool uploading() const { return target_ != nullptr; }
    bool finishing() const { return restartAt_.has_value(); }
    bool locksOutputs() const { return uploading() || finishing() || verifyAt_.has_value(); }
    bool pausesStreams() const { return uploading() || finishing(); }
    void abort();
    void disconnected(Link link, uint32_t generation);

private:
    const char* start(const UpdateOwner& owner, JsonObjectConst args, JsonObject data);
    const char* write(JsonObjectConst args, JsonObject data);
    const char* finish(JsonObject data);
    const char* confirm(JsonObjectConst args);
    void clearUpload();

    static constexpr size_t kChunkBytes = 600;
    const esp_partition_t* target_ = nullptr;
    esp_ota_handle_t handle_ = 0;
    mbedtls_sha256_context hash_{};
    std::array<uint8_t, 32> expectedHash_{};
    std::array<uint8_t, kChunkBytes> lastChunk_{};
    size_t lastLength_ = 0;
    size_t total_ = 0;
    size_t received_ = 0;
    uint32_t idleAt_ = 0;
    UpdateOwner owner_{};
    std::optional<uint32_t> restartAt_;
    std::optional<uint32_t> verifyAt_;
};

}  // namespace pm
