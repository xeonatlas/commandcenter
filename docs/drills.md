# Fire drills

## 2026-09-27: phase 1 (core stack)

| Path | Result |
|---|---|
| Critical page (emergency, through Do Not Disturb, repeats until acknowledged) | pass |
| Warning page (quiet) | pass |
| Resolved messages | pass |
| HomeConnectivityLost inhibits ProbeDown | pass |
| HostDown inhibits same-host alerts only | pass |
| Real HostDown (node-exporter stopped 4 min) | pass |
| Dead-man's switch (Alertmanager stopped 7 min) | pass |

Notes:

- Two earlier attempts that evening did not reach the phone. The first because the
  Pushover app was not yet installed (Alertmanager's sends succeeded); the second
  because the re-run reused the first run's labels, which Alertmanager treats as
  already notified until `repeat_interval`. `drill.sh` now labels each run afresh.
- The Pi rebooted unprompted at about 20:22, mid-drill. Supply read 5.18 V with no
  under-voltage since boot and `power_reset` 0 (a power-on reset); the journal is
  volatile, so the cause is unknown. Watch for a repeat.
- Later that night HomeConnectivityLost was widened to mute HostDown for the servers
  the Pi scrapes over wg-mon (they ride the same home line). `drill.sh inhibit` re-run
  after the deploy: HostDown on a remote host suppressed, and it still mutes its own
  host's alerts. Pass.

Run again after phase 4, and after any change to routing or receivers.
