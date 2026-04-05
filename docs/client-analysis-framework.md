# Poker Client Analysis Framework

## Purpose
Reverse-engineer poker client software to understand their anti-bot detection, then:
1. Build equivalent detection into our system
2. Harden our bot against each detection vector
3. Test bot evasion before deploying on real clients

## Clients to Analyze
- [x] GGPoker (desktop, Adobe AIR + native DLLs)
- [ ] PokerStars (browser, Cocos2d canvas)
- [ ] 888poker (desktop + browser)
- [ ] PartyPoker (desktop + browser)
- [ ] WPN/ACR (desktop)
- [ ] ClubGG / PPPoker (mobile-first, desktop wrapper)

---

## Analysis Methodology

### Step 1: File System Analysis
- Locate installation directory
- Map all executables, DLLs, config files
- Identify anti-cheat modules by name/size

### Step 2: PE Import Analysis (pefile)
For each DLL/EXE, extract Windows API imports to reveal:
- **Process monitoring**: CreateToolhelp32Snapshot, Process32Next, OpenProcess
- **Window scanning**: EnumWindows, FindWindowEx, GetWindowText
- **Input tracking**: GetCursorPos, GetKeyboardLayout, SetWindowsHookEx
- **Hardware fingerprinting**: SetupDiEnumDeviceInfo, DeviceIoControl
- **Registry scanning**: RegEnumKeyEx, RegEnumValue, RegQueryValueEx
- **Session monitoring**: WTSEnumerateSessions, OpenProcessToken
- **Anti-debug**: IsDebuggerPresent, CheckRemoteDebuggerPresent
- **Screen capture**: PrintWindow, BitBlt, D3D11CreateDevice
- **Network**: WSAEnumNetworkEvents, getaddrinfo

### Step 3: Runtime Analysis
- Run client with Process Monitor (procmon)
- Log all file/registry/network access
- Identify telemetry endpoints
- Monitor what processes the client inspects

### Step 4: Network Analysis
- Capture traffic with Wireshark
- Identify telemetry/detection payloads
- Check if behavioral data is sent server-side
- Map API endpoints

### Step 5: Detection Signal Mapping
For each client, document:
- What signals they collect
- What thresholds trigger detection
- How they respond (warning, ban, silent flag)
- Cross-platform data sharing

---

## GGPoker Analysis Results

### Architecture
```
launcher.exe (32-bit)
  └─ GGnet.exe (64-bit, Adobe AIR)
       ├─ IronANE.dll (native extension — process/window/mouse monitoring)
       ├─ gc-sdk-clnt.dll (game SDK — hardware/registry/service scanning)
       ├─ Loai.dll (anti-cheat, packed/obfuscated)
       ├─ Loki.dll (behavioral monitor, packed/obfuscated)
       ├─ libagora_screen_capture_extension.dll (screen/window enumeration)
       ├─ CppGeoLocs.dll (geolocation via mkodo)
       └─ LockneANE.dll (embedded Chromium/CEF)
```

### Detection Vectors Found

#### Process Monitoring
- `CreateToolhelp32Snapshot` + `Process32FirstW/NextW` — enumerates ALL processes
- `K32GetProcessImageFileNameW` / `K32GetModuleFileNameExW` — reads process paths
- `OpenProcess` + `GetProcessId` — inspects other processes
- Found in: IronANE.dll, gc-sdk-clnt.dll

#### Window Scanning  
- `EnumWindows` + `EnumChildWindows` — lists ALL windows
- `GetWindowTextW` — reads window titles
- `FindWindowExW` — searches for specific windows
- `GetForegroundWindow` — tracks active window
- `WindowFromPoint` — identifies window under cursor
- `PrintWindow` — can screenshot any window
- Found in: IronANE.dll, libagora_screen_capture_extension.dll

#### Input Tracking
- `GetCursorPos` — reads mouse position (continuous tracking)
- `GetKeyboardLayout` — keyboard locale fingerprinting
- Found in: IronANE.dll, gc-sdk-clnt.dll

#### Hardware Fingerprinting
- `SetupDiEnumDeviceInfo` + `SetupDiGetDeviceRegistryPropertyW` — ALL hardware
- `DeviceIoControl` — disk serial, BIOS, VM detection
- `GlobalMemoryStatusEx` — RAM fingerprint
- `EnumDisplayDevicesW` + `EnumDisplaySettingsExW` — monitor/GPU info
- `D3D11CreateDevice` — DirectX/GPU detection
- Found in: gc-sdk-clnt.dll, libagora_screen_capture_extension.dll

#### Registry Scanning
- `RegEnumKeyExW` + `RegEnumValueW` — walks entire registry trees
- `RegQueryValueExA/W` — reads specific keys
- `RegCreateKeyExA/W` + `RegSetValueExA/W` — writes markers
- Found in: gc-sdk-clnt.dll, IronANE.dll, expapply.dll

#### Session/User Monitoring
- `WTSEnumerateSessionsW/ExA` — lists ALL Windows sessions
- `WTSQueryUserToken` — gets user tokens per session
- `OpenProcessToken` + `GetTokenInformation` — security context
- `AdjustTokenPrivileges` — requests elevated access
- Found in: gc-sdk-clnt.dll, Loki.dll

#### Service Enumeration
- `EnumServicesStatusExW` — lists ALL Windows services
- Found in: gc-sdk-clnt.dll

#### Anti-Debug
- `IsDebuggerPresent` — in GGnet.exe, IronANE.dll, gc-sdk-clnt.dll, expapply.dll
- `SymGetModuleBase64` (dbghelp) — debug symbol analysis
- `DebugBreak` — debugger trap
- Found in: multiple modules

#### Crypto/Telemetry
- Full CryptAPI suite (Sign, Hash, Encrypt, Decrypt)
- Certificate store access (validates DLL integrity)
- BCryptGenRandom for secure random
- Found in: IronANE.dll, gc-sdk-clnt.dll, Loai.dll

#### Network
- `WSAEnumNetworkEvents` — network event monitoring
- libcurl for HTTPS telemetry
- Found in: gc-sdk-clnt.dll, CppGeoLocs.dll

---

## Countermeasures Matrix

| Detection Vector | Risk Level | Countermeasure |
|---|---|---|
| Process enumeration | HIGH | Run bot on separate machine |
| Window title scanning | HIGH | No bot windows on poker machine |
| Mouse tracking | MEDIUM | Humanized curves + idle movement |
| Hardware fingerprinting | HIGH | Unique VM per instance |
| Registry scanning | MEDIUM | Clean machine, no tools installed |
| Screen capture | HIGH | No bot UI visible, headless only |
| Session enumeration | LOW | Single user session |
| Service enumeration | LOW | No bot services |
| Anti-debug | LOW | Don't attach debuggers during play |
| Keyboard layout | LOW | Match target locale |
| Network monitoring | LOW | Standard connection, no VPN |

## Architecture for Safe Bot Deployment
```
Machine A: Poker Client (clean)
  - Only poker software installed
  - Normal browser, no dev tools
  - Clean registry
  - Real hardware (no VM)

Machine B: Bot Controller (separate)
  - Watches Machine A via VNC/screen capture
  - Runs YOLO detection + strategy
  - Sends mouse/keyboard commands to Machine A
  - OR: uses hardware KVM to simulate input
```
