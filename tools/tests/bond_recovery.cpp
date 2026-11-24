// g++ -std=c++17 -Wall -Wextra -Ifirmware/PowerModule tools/tests/bond_recovery.cpp
#include "BondRecovery.h"
#include <cassert>
#include <cstring>

struct Fake {
    bool live = false, advertisingEnabled = true, failsResume = false;
    bool sticky = false, connectsDuringPause = false, connectedPeer = false;
    int peers = 2, stopped = 0, restored = 0, removed = 0;
    bool active() { return live; }
    bool advertising() { return advertisingEnabled; }
    bool stopAdvertising() { ++stopped; live = connectsDuringPause; return true; }
    bool startAdvertising() { ++restored; return !failsResume; }
    bool enumerate(int& count) { count = peers; return true; }
    bool connected(int) { return connectedPeer; }
    bool remove(int) { ++removed; if (!sticky) --peers; return true; }
};

int main() {
    using pm::recoverBonds;
    unsigned count;
    {
        Fake f; f.live = true;
        assert(std::strcmp(recoverBonds(f, count), "BLE_ACTIVE") == 0);
        assert(count == 0 && f.stopped == 0 && f.removed == 0);
    }
    {
        Fake f; f.connectsDuringPause = true;
        assert(std::strcmp(recoverBonds(f, count), "BLE_ACTIVE") == 0);
        assert(count == 0 && f.stopped == 1 && f.removed == 0 && f.restored == 0);
    }
    {
        Fake f; f.connectedPeer = true;
        assert(std::strcmp(recoverBonds(f, count), "BLE_ACTIVE") == 0);
        assert(count == 0 && f.removed == 0 && f.restored == 1);
    }
    {
        Fake f;
        assert(recoverBonds(f, count) == nullptr);
        assert(count == 2 && f.stopped == 1 && f.restored == 1);
        assert(recoverBonds(f, count) == nullptr && count == 0);
    }
    {
        Fake f; f.advertisingEnabled = false;
        assert(recoverBonds(f, count) == nullptr && count == 2);
        assert(f.stopped == 0 && f.restored == 0);
    }
    {
        Fake f; f.sticky = true;
        assert(std::strcmp(recoverBonds(f, count), "BLE_BONDS_FAILED") == 0);
        assert(f.peers == 2 && f.restored == 1);
    }
    {
        Fake f; f.failsResume = true;
        assert(std::strcmp(recoverBonds(f, count), "BLE_ADVERTISING_FAILED") == 0);
        assert(count == 2 && f.peers == 0);
    }
}
