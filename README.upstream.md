# 🌌 TimeGuardian — Ultra‑Precise Windows Time Sync Tray App

<p align="center">
  <img alt="TimeGuardian Logo" src="logo.png" width="96" height="96">
</p>

<p align="center">
  <b>Keep your Windows clock laser‑accurate with an ultra‑polished, low‑overhead tray app.</b><br>
  Intelligent NTP selection, elegant UI, optional IP‑based timezone and region tuning — made for perfectionists and professionals.
</p>

<p align="center">
  <a href="https://github.com/mixtrus/TimeGuardian/stargazers"><img src="https://img.shields.io/github/stars/mixtrus/TimeGuardian?style=social" alt="GitHub stars"></a>
  <a href="https://github.com/mixtrus/TimeGuardian/network/members"><img src="https://img.shields.io/github/forks/mixtrus/TimeGuardian?style=social" alt="GitHub forks"></a>
  <a href="https://github.com/mixtrus/TimeGuardian/issues"><img src="https://img.shields.io/github/issues/mixtrus/TimeGuardian?color=%23f59e0b" alt="Issues"></a>
  <a href="https://github.com/mixtrus/TimeGuardian/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-10b981" alt="License"></a>
  <img src="https://img.shields.io/badge/Windows-7%20%2B-2563eb" alt="Windows 7+">
  <img src="https://img.shields.io/badge/Python-3.9–3.13-22c55e" alt="Python">
  <img src="https://img.shields.io/badge/GUI-PySide6-8b5cf6" alt="PySide6">
</p>

---

## ✨ Why TimeGuardian?

- 🧭 Precision obsessed: Measures clock drift and only updates when needed — avoids jitter and saves resources.
- ⚡ Smart by design: Probes a diverse pool of NTP servers, selects the lowest‑latency winner, and adapts live.
- 🌐 World‑ready: Full Windows timezone support with a beautiful “(UTC ±HH:MM) City/Area” picker.
- 🛰️ IP‑aware (optional): Can derive timezone from your public IP using multiple modern providers — resilient to ISP blocks.
- 🧩 Region tuning (optional): Attempts to align Windows region/culture with your IP (user‑consent and elevation).
- 🧿 Aesthetic tray: Minimal, elegant, color‑coded icons — Idle (blue), Connected (green), Disconnected (red).
- 🧪 Accurate ping lab: Multi‑sample, high‑resolution RTT measurement with NTP‑first + ICMP fallback — sortable results.
- 🔕 Respectful: One switch to silence all notifications.
- 🚀 Startup‑friendly: Cleanly registers to run on login (no admin required).
- 🔒 Admin‑light: Only elevates for operations that truly require it (time/region updates).

> Give it a ⭐ if you appreciate meticulous engineering, ultra‑aesthetic UX, and tools that just work.

---

## 🧭 Feature Highlights

- Background tray app with ultra‑clean design
- Auto or Preferred NTP server modes
- “Ping servers” with live progress bar, median‑of‑N samples, and method labeling (NTP / ICMP / fail)
- Startup Idle icon, dynamic Connected/Disconnected state
- Welcome dialog on manual launch (disable any time); never shown on startup‑launch
- IP timezone via multi‑provider parallel fetch (ipapi, ip-api, ipwho, worldtimeapi, ifconfig, ip.sb)
- Uses logo.png automatically for app identity and notifications (with AppUserModelID branding)
- Elegant Settings with drift threshold, frequency, notifications, startup, and more

---

## 📦 Installation

Prereqs:
- Windows 7 or newer
- Python 3.9–3.13 recommended

Install dependencies:
```bash
python -m pip install -U pip setuptools wheel
python -m pip install -r requirements.txt
```

Run (developer mode):
```bash
python -m app.main
```

> Tip: Place a logo.png in the root folder to brand the app and notifications.

---

## 🏗️ Build a Windows EXE (PyInstaller)

One‑folder build (recommended):
```powershell
# From project root
pyinstaller --noconfirm --clean --windowed --name TimeGuardian `
  --paths . `
  --collect-all PySide6 `
  --add-data "elevated_time_setter.py;." `
  --add-data "logo.png;." `
  app/main.py
```

Embed your EXE icon (convert your PNG to a real multi‑size ICO first):
```powershell
# Clean and rebuild with an ICO
Remove-Item -Recurse -Force .\build, .\dist -ErrorAction SilentlyContinue
pyinstaller --noconfirm --clean --windowed --name TimeGuardian `
  --icon "$PWD\logo.ico" `
  --paths . `
  --collect-all PySide6 `
  --add-data "elevated_time_setter.py;." `
  --add-data "logo.png;." `
  app/main.py
```

Run your app:
```
dist/TimeGuardian/TimeGuardian.exe
```

If Explorer still shows the old icon, it’s likely the icon cache — rename the EXE (e.g., TimeGuardian2.exe) or clear the cache.

---

## 🖥️ Usage

- Launch the EXE; a tray icon appears near the clock.
- Right‑click the tray icon for:
  - Sync now
  - Enable automatic sync
  - Start at Windows login
  - Set timezone (UTC offset with city/region)
  - Set timezone from IP (multi‑source)
  - Set Windows region from IP (optional)
  - Settings
  - Open GitHub (mixtrus)
  - “Please star, support, and report bugs on GitHub ♥” (disabled, eye‑catching reminder)
  - Exit

Icon states:
- 🔵 Idle (blue): starting / waiting
- 🟢 Connected (green): recently synced
- 🔴 Disconnected (red): not recently synced

---

## ⚙️ Settings You’ll Love

- Automatic sync toggle
- Check interval (seconds)
- Drift threshold (ms) — only corrects when meaningful
- Servers list with “Ping servers”:
  - Live ultra‑aesthetic progress bar
  - Multi‑sample NTP RTT (median) with ICMP fallback
  - Sorted ascending by latency
  - Quickly set Preferred server or let Auto pick
- Startup on login (no admin)
- Timezone from IP (optional)
- Region from IP (optional, elevated)
- Notifications (enable/disable)
- Welcome dialog on manual launch (enable/disable)

---

## 🔐 Security & Privacy

- Elevation only when setting system time or region.
- IP lookups use no‑auth, well‑known providers over HTTPS where possible.
- No telemetry. No tracking. No ads.

---

## 🧪 Troubleshooting

- “No tray icon”: Ensure you’re in a desktop user session (not a service) and the system tray is enabled.
- “Could not load Qt platform plugin”: Rebuild with `--collect-all PySide6` as shown above.
- NTP blocked by network: The app falls back to ICMP for measurables; time sync requires NTP reachability.
- Time not updating: Accept the UAC prompt. Some corporate policies forbid setting system time.

---

## 🧭 Roadmap

- Optional parallel NTP probing for even faster selection
- Export/import configuration profiles
- Advanced telemetry dashboard (local only) for latency history

> Have ideas? Open an issue — your suggestions shape the future.

---

## 💚 Support the Project

- ⭐ Star this repo — it really helps with visibility!
- 🐛 Report issues and propose features
- 🔁 Share TimeGuardian with friends and colleagues
- 🤝 Contribute PRs if you’re into Python/PySide6

<p align="center">
  <a href="https://github.com/mixtrus/TimeGuardian/stargazers"><b>Give a Star — Make my day ✨</b></a>
</p>

---

## 📥 Install (Source)

```bash
python -m pip install -U pip setuptools wheel
python -m pip install -r requirements.txt
python -m app.main
```

---

## 🧾 License

MIT — see [LICENSE](./LICENSE). Use freely, build boldly, credit kindly.

---

## 🙌 Author

Created with care by [mixtrus](https://github.com/mixtrus).
If TimeGuardian helped you, please star the repo — your support fuels future polish and features.

---
