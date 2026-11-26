#pragma once

namespace pm {

enum class BlePeerState { Absent, Present, Unknown };

// Runs on the NimBLE host task, so a GAP callback cannot reuse a handle
// between checking the connection and acting on it.
template<class Link>
void recoverBleLink(Link& link) {
    if (!link.synced()) return;
    if (link.tracked()) {
        switch (link.peerState()) {
        case BlePeerState::Present:
            if (link.closing()) link.retryDisconnect();
            return;
        case BlePeerState::Unknown:
            return;
        case BlePeerState::Absent:
            link.forgetMissingPeer();
            break;
        }
    }
    // A pending or unexpected live peer still takes precedence over advertising.
    if (!link.anyPeer()) link.restoreAdvertising();
}

}  // namespace pm
