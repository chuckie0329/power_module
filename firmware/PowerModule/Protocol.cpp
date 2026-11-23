#include "Protocol.h"
#include <algorithm>
#include <cstdio>
#include <cstring>

namespace pm {
namespace {

uint16_t le16(const uint8_t* bytes) {
    return bytes[0] | (uint16_t(bytes[1]) << 8);
}

uint32_t jsonCrc(const Message& bytes) {
    uint32_t crc = 0xFFFFFFFF;
    for (uint8_t byte : bytes) {
        crc ^= byte;
        for (unsigned bit = 0; bit < 8; ++bit) {
            crc = (crc & 1) ? (crc >> 1) ^ 0xEDB88320u : crc >> 1;
        }
    }
    return ~crc;
}

bool canonicalCopy(JsonVariantConst source, JsonVariant destination, bool root = false) {
    if (source.is<JsonObjectConst>()) {
        auto object = destination.to<JsonObject>();
        const char* keys[32];
        size_t count = 0;
        for (JsonPairConst pair : source.as<JsonObjectConst>()) {
            const char* key = pair.key().c_str();
            if (root && strcmp(key, "crc32") == 0) continue;
            if (count == sizeof(keys) / sizeof(keys[0])) return false;
            keys[count++] = key;
        }
        std::sort(keys, keys + count, [](const char* a, const char* b) { return strcmp(a, b) < 0; });
        for (size_t i = 0; i < count; ++i) {
            auto value = object[keys[i]].to<JsonVariant>();
            if (!canonicalCopy(source[keys[i]], value)) return false;
        }
        return true;
    }
    // The protocol uses integers, booleans, strings and null.
    if (source.isNull() || source.is<bool>() || source.is<uint32_t>() ||
        source.is<int32_t>() || source.is<const char*>()) {
        return destination.set(source);
    }
    return false;
}

bool canonicalJson(JsonVariantConst source, Message& output) {
    JsonDocument sorted;
    if (!canonicalCopy(source, sorted.to<JsonVariant>(), true) || sorted.overflowed()) return false;
    serializeJson(sorted, output);
    return !output.empty();
}

struct LineInput {
    const Message& data;
    size_t position = 0;

    int read() {
        return position == data.size() ? -1 : static_cast<uint8_t>(data[position++]);
    }
    size_t readBytes(char* buffer, size_t size) {
        size = std::min(size, data.size() - position);
        memcpy(buffer, data.data() + position, size);
        position += size;
        return size;
    }
};

}  // namespace

Receive LineReader::push(char byte, Message& line) {
    if (byte == '\n') {
        const bool bad = overflow_;
        if (!bad) {
            bytes_ += byte;
            line.swap(bytes_);
        }
        bytes_.clear();
        overflow_ = false;
        return bad ? Receive::Bad : Receive::Complete;
    }
    if (bytes_.size() == kMaxLine - 1) overflow_ = true;
    if (!overflow_) bytes_ += byte;
    return Receive::Incomplete;
}

size_t gattValueLimit(uint16_t mtu) {
    return std::min(kMaxGattValue, size_t((mtu < 23 ? 23 : mtu) - 3));
}

size_t writeFragment(const Message& message, uint16_t number, size_t stride, uint8_t index,
                     uint8_t* bytes) {
    const size_t offset = size_t(index) * stride;
    const size_t total = message.size();
    const size_t length = std::min(stride, total - offset);
    bytes[0] = kProtocol;
    bytes[1] = 0;
    bytes[2] = uint8_t(number);
    bytes[3] = uint8_t(number >> 8);
    bytes[4] = index;
    bytes[5] = uint8_t((total + stride - 1) / stride);
    bytes[6] = uint8_t(total);
    bytes[7] = uint8_t(total >> 8);
    memcpy(bytes + kFragmentHeader, message.data() + offset, length);
    return kFragmentHeader + length;
}

Receive FragmentReader::push(const uint8_t* bytes, size_t length, uint16_t mtu, uint32_t now,
                             Message& line) {
    expire(now);
    if (length <= kFragmentHeader || length > gattValueLimit(mtu) ||
        bytes[0] != kProtocol || bytes[1] != 0) return bad();

    const uint16_t number = le16(bytes + 2);
    const uint8_t index = bytes[4];
    const uint8_t count = bytes[5];
    const uint16_t total = le16(bytes + 6);
    const size_t payload = length - kFragmentHeader;
    if (total < 2 || total > kMaxLine || !count || index >= count) return bad();
    if (index == 0) {
        if (next_ || payload > total || count != (total + payload - 1) / payload) return bad();
        number_ = number;
        total_ = total;
        count_ = count;
        stride_ = payload;
        deadline_ = now + kFragmentMs;
    }
    if (number != number_ || total != total_ || count != count_ || index != next_ ||
        payload != std::min(size_t(stride_), size_t(total_ - bytes_.size()))) return bad();

    bytes_.append(reinterpret_cast<const char*>(bytes + kFragmentHeader), payload);
    ++next_;
    if (next_ != count_) return Receive::Incomplete;
    if (bytes_.size() != total_ || bytes_.back() != '\n' ||
        bytes_.find('\n') != bytes_.size() - 1) return bad();
    line.swap(bytes_);
    reset();
    return Receive::Complete;
}

bool FragmentReader::expire(uint32_t now) {
    if (!next_ || !due(now, deadline_)) return false;
    reset();
    return true;
}

void FragmentReader::reset() {
    bytes_.clear();
    next_ = 0;
    count_ = 0;
}

Receive FragmentReader::bad() {
    reset();
    return Receive::Bad;
}

const char* decode(const Message& line, JsonDocument& doc, Message& canonical) {
    if (line.size() < 2 || line.size() > kMaxLine || line.back() != '\n' ||
        line.find('\0') != Message::npos) return "BAD_JSON";
    LineInput input{line};
    if (deserializeJson(doc, input, DeserializationOption::NestingLimit(4)) ||
        !doc.is<JsonObject>()) return "BAD_JSON";
    while (input.position < line.size()) {
        const char byte = line[input.position++];
        if (byte != ' ' && byte != '\t' && byte != '\r' && byte != '\n') return "BAD_JSON";
    }
    auto crcField = doc["crc32"].as<JsonString>();
    if (crcField.isNull() || crcField.size() != 8) return "BAD_CRC";
    uint32_t received = 0;
    for (size_t i = 0; i < crcField.size(); ++i) {
        const char c = crcField.c_str()[i];
        received <<= 4;
        if (c >= '0' && c <= '9') received |= c - '0';
        else if (c >= 'a' && c <= 'f') received |= c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') received |= c - 'A' + 10;
        else return "BAD_CRC";
    }
    if (!canonicalJson(doc.as<JsonVariantConst>(), canonical)) return "BAD_JSON";
    return jsonCrc(canonical) == received ? nullptr : "BAD_CRC";
}

Message encode(JsonDocument& doc) {
    Message canonical;
    if (!canonicalJson(doc.as<JsonVariantConst>(), canonical)) return {};
    char crc[9];
    snprintf(crc, sizeof(crc), "%08lx", static_cast<unsigned long>(jsonCrc(canonical)));
    doc["crc32"] = Message(crc);
    Message line;
    serializeJson(doc, line);
    line += '\n';
    if (line.size() > kMaxLine) return {};
    return line;
}

bool Outbox::push(Message message) {
    if (message.empty() || size_ == lines_.size()) return false;
    lines_[(first_ + size_) % lines_.size()] = std::move(message);
    ++size_;
    return true;
}

void Outbox::pop() {
    lines_[first_].clear();
    first_ = (first_ + 1) % lines_.size();
    --size_;
}

void Outbox::clear() {
    while (!empty()) pop();
}

}  // namespace pm
