#pragma once

#include <ArduinoJson.h>
#include <array>
#include <string>
#include "Config.h"

namespace pm {

using Message = std::string;

enum class Link : uint8_t { Usb, Ble };
enum class Receive : uint8_t { Incomplete, Complete, Bad };

class LineReader {
public:
    Receive push(char byte, Message& line);

private:
    Message bytes_;
    bool overflow_ = false;
};

constexpr size_t kFragmentHeader = 8;
constexpr size_t kMaxGattValue = 512;
size_t gattValueLimit(uint16_t mtu);
size_t writeFragment(const Message& message, uint16_t number, size_t stride, uint8_t index,
                     uint8_t* bytes);

class FragmentReader {
public:
    Receive push(const uint8_t* bytes, size_t length, uint16_t mtu, uint32_t now, Message& line);
    bool expire(uint32_t now);
    void reset();

private:
    Receive bad();

    Message bytes_;
    uint32_t deadline_ = 0;
    uint16_t number_ = 0;
    uint16_t total_ = 0;
    uint16_t stride_ = 0;
    uint8_t count_ = 0;
    uint8_t next_ = 0;
};

const char* decode(const Message& line, JsonDocument& doc, Message& canonical);
Message encode(JsonDocument& doc);
Message encodeInfoReply(JsonDocument& doc);

class Outbox {
public:
    bool push(Message message);
    const Message& front() const { return lines_[first_]; }
    void pop();
    void clear();
    bool empty() const { return size_ == 0; }

private:
    std::array<Message, 4> lines_;
    size_t first_ = 0;
    size_t size_ = 0;
};

}  // namespace pm
