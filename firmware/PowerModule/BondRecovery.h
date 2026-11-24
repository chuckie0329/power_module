#pragma once

namespace pm {
template<class Bonds>
const char* recoverBonds(Bonds& bonds, unsigned& cleared) {
    cleared = 0;
    if (bonds.active()) return "BLE_ACTIVE";
    const bool advertised = bonds.advertising();
    if (advertised && !bonds.stopAdvertising()) return "BLE_BONDS_FAILED";

    const char* error = nullptr;
    int count = 0;
    if (bonds.active()) error = "BLE_ACTIVE";
    else if (!bonds.enumerate(count)) error = "BLE_BONDS_FAILED";
    for (int i = 0; !error && i < count; ++i) {
        if (bonds.active() || bonds.connected(i)) error = "BLE_ACTIVE";
        else if (!bonds.remove(i)) error = "BLE_BONDS_FAILED";
        else ++cleared;
    }
    if (!error && (!bonds.enumerate(count) || count != 0)) error = "BLE_BONDS_FAILED";
    if (advertised && !bonds.active() && !bonds.startAdvertising() && !error)
        error = "BLE_ADVERTISING_FAILED";
    return error;
}
}  // namespace pm
