# CoinPoker VM #1 build checklist

End state: a Win11 VM that boots, connects to a pinned VPN exit, runs CoinPoker
with the patched `PBClient.dll`, and can be driven from the host (or from
within itself) by `tools/coinpoker_open_practice.py` and the gauntlet.

This is the **first** VM. Don't build #2 until #1 has run unattended for
at least a week with no detection signals.

---

## 1. Host

**Recommended: Proxmox** on a spare machine. Reasons:
- Headless, can run 24/7 without competing with PoE / desktop work
- Per-VM bridged networking → clean per-VM VPN routing
- Snapshots before risky changes (patcher deploys, CoinPoker updates)
- VNC/SPICE console for the rare times you actually need to look at the table

**Acceptable fallback: Hyper-V on this box.** Lower friction to start, but
Windows-on-Windows nesting + sharing GPU/CPU with PoE is suboptimal.

---

## 2. Guest VM specs

| Resource | Min | Recommended | Why |
|---|---|---|---|
| OS | Win 11 22H2+ | Win 11 23H2 | CoinPoker only ships Win client |
| vCPU | 2 | 4 | Unity table + Electron lobby + Python advisor |
| RAM | 6 GB | 8 GB | Electron is hungry; Python loads YOLO + equity NN |
| Disk | 60 GB | 80 GB | Win11 baseline + CoinPoker (~3GB) + frames log growth |
| GPU | none | none | Unity table renders fine on software path |
| Network | virtio bridge | virtio bridge | Routed through host's VPN, not direct |

Don't overprovision — for VM #2+ you'll want to fit several on one host.

---

## 3. Fingerprint hardening (do this BEFORE first CoinPoker login)

CoinPoker's anti-bot ML pulls hardware/OS fingerprint. Identical fingerprints
across "different" accounts = collusion ban. Per VM:

- [ ] **Random hostname** — not `DESKTOP-XXXXX` default. Pick something
      mundane and human-looking (`simons-laptop`, `home-pc`, etc.)
- [ ] **New Windows SID** — if you cloned the disk, run `sysprep /generalize`
      first. Never boot a clone with the parent SID.
- [ ] **Random MAC** on the virtio NIC (Proxmox: regenerate in VM hardware tab)
- [ ] **Unique screen resolution** — don't mirror your host's. 1600×900 or
      1366×768 are believable laptop sizes; avoid the giveaway 1920×1080 on
      every VM.
- [ ] **Timezone matches the VPN exit country** — VM's clock TZ ≠ exit IP TZ
      is a strong signal
- [ ] **Locale matches the VPN exit country** (en-CA for a Toronto exit, etc.)
- [ ] **No browser extensions, no logged-in Microsoft account** on the guest
- [ ] **Fresh Windows install image, not the same ISO/file across VMs** — at
      least re-download per VM so file mtimes/serials differ

---

## 4. VPN

**Mullvad Wireguard at the Proxmox host level**, routing only the VM's bridge
through the tunnel. This way:
- Kill switch is automatic — if Wireguard drops, the VM has no internet
- The guest doesn't have VPN credentials, so a guest compromise can't leak them
- You can rotate exits without touching the guest

Per VM:
- [ ] Generate a new Mullvad device + Wireguard config
- [ ] Pin a single exit (city + relay), not "any" — consistent IP per account
- [ ] Different exits per VM, no overlap
- [ ] Verify exit IP from inside the guest matches expected before installing CoinPoker
- [ ] DNS goes through the VPN (Mullvad DNS, not your ISP / Google)

---

## 5. Software install order

Do these **in order**, inside the guest:

1. [ ] Windows 11 fresh install (skip MS account, local user only)
2. [ ] Apply fingerprint hardening from §3
3. [ ] Confirm VPN is active (check `https://am.i.mullvad.net`)
4. [ ] Install Python 3.12 (same minor version as dev box for binary compat)
5. [ ] `pip install websocket-client pythonnet` (versions matching dev)
6. [ ] Install CoinPoker via the official installer
7. [ ] Create CoinPoker account, phone verify, **deposit a small amount of
      USDT to a fresh wallet that has never touched any other CoinPoker
      account on the host's chain history**
8. [ ] Play 5–10 hands manually on the practice table to look like a normal
      first-day user
9. [ ] Copy `C:\Users\Simon\coinpoker_patcher\` from dev box to the VM
10. [ ] Verify `PBClient.dll.orig` sha256 in the VM matches the dev box's
       backup hash. If CoinPoker updated between dev-box patch and VM install,
       re-patch from the VM's own `.orig`.
11. [ ] `python deploy.py uninstall && python deploy.py install` (elevated)
12. [ ] Open CoinPoker, sit in at practice — verify
       `C:\Users\Simon\coinpoker_frames.jsonl` is being written
13. [ ] Copy / `git clone` the `poker-research` repo
14. [ ] Run all CoinPoker test suites — must be green
15. [ ] `python tools/coinpoker_open_practice.py` — verify it works on the VM
16. [ ] `python tools/phase2_gauntlet.py --target-rounds 50 --mode periodic
       --period-ms 500 --ignore-staleness` → expect 50/50 PASS
17. [ ] **Operator-supervised** single-hand FOLD test using
       `coinpoker_live_FOLD.flag` (the same checkpoint we keep deferring
       on the dev box — do it here instead, on the VM, so the dev box
       stays a clean development env)
18. [ ] Wire `vision/humanizer.py` into the clicker before any 50-hand run
19. [ ] 50-hand auto-clicked dry run on practice
20. [ ] Only then: real-money 1-hand-with-supervision test on the VM

---

## 6. Per-VM artifacts to track (separate file, never the same place)

For each VM, record:

- VM name + Proxmox VMID
- VPN exit (provider + city + relay)
- CoinPoker username + email
- Recovery email + phone number
- Fresh USDT deposit wallet address (TRC20 / ERC20 — pick one and stick to it)
- Hostname inside the guest
- Hardware fingerprint summary (screen res, locale, TZ)
- Patcher build hash deployed

**Do not store these in the poker-research repo.** Encrypted notes, password
manager, or `~/.coinpoker/vm_inventory.gpg`. If git history ever leaks, you
don't want a connecting graph between accounts in plaintext.

---

## 7. Kill switch (build BEFORE adding VM #2)

A single host-level command that:
- Kills `python.exe` + `CoinPoker.exe` inside every VM (via `qm guest exec` on
  Proxmox, or PowerShell remoting)
- Touches every VM's `.autoplay_pause` flag
- Logs the kill reason and timestamp
- Sends a notification (Telegram bot, Signal-cli, whatever)

Trigger conditions:
- Any clicker reports a "click failed" verification (the JSONL `game.seat`
  event didn't appear within 2s of fire)
- Any session loses more than `STOP_LOSS_BB_PER_HUNDRED` over a rolling window
- Any VM reports a CoinPoker "account suspended" / "logged out" notification
- Manual trigger from the host

You want this **before** the first unattended overnight run, not after the
first overnight loss.

---

## 8. Anti-correlation rules (always-on)

- [ ] No two VMs ever sit at the same table (enforce in the table-selection
      logic on the host orchestrator, even if the orchestrator doesn't exist
      yet — at minimum, document the table IDs each VM is allowed to play)
- [ ] Stagger session start times by ≥30 min between VMs
- [ ] Stagger session lengths (4–10h, randomized per shift)
- [ ] Different stake levels per VM where possible
- [ ] Withdrawal pattern: never to the same address as another VM, never
      with correlated timing
- [ ] No VM ever runs the gauntlet or test suites against the production
      account — gauntlet uses a separate practice-only account

---

## Open questions for VM #1 setup session

- Proxmox host location: existing box or stand up fresh?
- Mullvad device count cap — verify how many simultaneous Wireguard configs
  the current Mullvad subscription allows
- Phone numbers: SMS-receive service, eSIM provider, or burner SIMs? Each has
  different cost/reliability profiles
- USDT wallet hygiene: which chain (TRC20 cheapest, ERC20 most universal),
  and how to source clean coins per account without on-chain linkage
