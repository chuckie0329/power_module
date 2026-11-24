// Compile with Protocol.cpp and ArduinoJson 7.4.2. Feed a full reply on stdin.
#include "Protocol.h"
#include <iostream>
#include <iterator>
#include <string>

int main(int argc, char** argv) {
    const std::string input{std::istreambuf_iterator<char>(std::cin), {}};
    JsonDocument doc;
    if (deserializeJson(doc, input)) return 2;
    std::cout << (argc > 1 && std::string(argv[1]) == "legacy" ? pm::encode(doc) : pm::encodeInfoReply(doc));
}
