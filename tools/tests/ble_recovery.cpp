#include "BleRecovery.h"
#include <cassert>

using pm::BlePeerState;
using pm::recoverBleLink;

struct Link {
    bool hostSynced = true;
    bool hasHandle = true;
    bool disconnectRequested = false;
    bool otherPeer = false;
    bool advertising = false;
    bool startSucceeds = true;
    bool recoveryAllowed = true;
    BlePeerState peer = BlePeerState::Present;
    unsigned disconnects = 0;
    unsigned clears = 0;
    unsigned starts = 0;
    bool synced() { return hostSynced; }
    bool tracked() { return hasHandle; }
    BlePeerState peerState() { return peer; }
    bool closing() { return disconnectRequested; }
    void retryDisconnect() { ++disconnects; }
    void forgetMissingPeer() { ++clears; hasHandle = false; }
    bool anyPeer() { return otherPeer; }
    void restoreAdvertising() {
        if (recoveryAllowed && !advertising) {
            ++starts;
            advertising = startSucceeds;
        }
    }
};

int main() {
    Link healthy;
    recoverBleLink(healthy);
    assert(!healthy.disconnects && !healthy.clears && !healthy.starts);

    Link closing;
    closing.disconnectRequested = true;
    recoverBleLink(closing);  // Terminate fails (or no callback yet): still in GAP.
    recoverBleLink(closing);
    assert(closing.disconnects == 2 && !closing.clears && !closing.starts);
    closing.peer = BlePeerState::Absent;  // Actual disconnect, callback missing.
    recoverBleLink(closing);
    assert(closing.disconnects == 2 && closing.clears == 1 && closing.starts == 1);
    recoverBleLink(closing);
    assert(closing.clears == 1 && closing.starts == 1);

    Link missedCallback;
    missedCallback.peer = BlePeerState::Absent;  // Involuntary disconnect, no request.
    recoverBleLink(missedCallback);
    assert(!missedCallback.disconnects && missedCallback.clears == 1 && missedCallback.advertising);

    Link idle;
    idle.hasHandle = false;  // Ordinary GAP callback; NimBLE failed to restart.
    idle.startSucceeds = false;
    recoverBleLink(idle);
    assert(idle.starts == 1 && !idle.advertising);
    idle.startSucceeds = true;
    recoverBleLink(idle);
    assert(idle.starts == 2 && idle.advertising);

    Link replacing;
    replacing.peer = BlePeerState::Absent;
    replacing.otherPeer = true;
    recoverBleLink(replacing);
    assert(replacing.clears == 1 && !replacing.starts && !replacing.disconnects);

    Link unsure;
    unsure.disconnectRequested = true;
    unsure.peer = BlePeerState::Unknown;
    recoverBleLink(unsure);
    assert(!unsure.clears && !unsure.starts && !unsure.disconnects);
    unsure.peer = BlePeerState::Present;
    unsure.hostSynced = false;
    recoverBleLink(unsure);
    assert(!unsure.clears && !unsure.starts && !unsure.disconnects);

    Link updating;
    updating.hasHandle = false;
    updating.recoveryAllowed = false;
    recoverBleLink(updating);
    assert(!updating.starts);
}
