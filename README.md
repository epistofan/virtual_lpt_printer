# LPT Alarm Printer Replacement

A hardware/software bridge that replaces an aging **OKI Microline 280 (D22300B)**
parallel-port alarm printer on an industrial machine (G.S Coating System) with
a **Raspberry Pi 4** touchscreen kiosk, using an **Arduino Nano Every** as a
real-time Centronics/LPT interface bridge.

The machine keeps printing exactly as before — it has no idea its "printer"
is now a microcontroller. All alarm/event logs are displayed live on a
touchscreen, saved to disk, and can be pushed to a Windows network share.

---

## Why this exists

The original printer (9-pin dot matrix, Centronics/LPT interface) was end of
life. Rather than replace the machine's controller (expensive, risky,
unsupported), this project **emulates the printer** at the electrical
protocol level and captures everything it would have printed.

## Architecture

```
Machine (CN36 Centronics, 5V)
        │
        ▼
Arduino Nano Every  ── hardware interrupt on /STROBE, drives BUSY/ACK
        │                (all timing-critical work happens here)
        │  USB (CDC serial, appears as /dev/ttyACM0)
        ▼
Raspberry Pi 4 ── Python/Tkinter app
        │
        ├── Live view on 10" HDMI touchscreen
        ├── Append-only log file per session
        └── "Send to share" button → SMB (Windows) or local/mounted path
```

### Why an Arduino in the middle?

An early version read the parallel port directly on the Pi's GPIO
(via a TXS0108E level shifter + resistor divider for /STROBE). It worked
perfectly against a slow test rig, but against the real machine the
data came out corrupted — long runs of the same character, garbled
bytes, dropped characters.

Root cause: Python on Linux (even in a tight polling loop) cannot
service a GPIO edge with guaranteed microsecond latency — every
read goes through a syscall (lgpio), and the GIL/OS scheduler adds
unpredictable jitter. The machine's printer buffer lets it burst
data far faster than its ~300 char/s average print speed suggests.

An AVR's `attachInterrupt` is a genuine hardware interrupt — a fixed,
tiny number of CPU cycles between the electrical edge and the
handler running. That's the only way to reliably keep up with real
Centronics timing. So the Arduino now owns 100% of the LPT protocol
(STROBE/BUSY/ACK, status lines), buffers bytes, and streams them to
the Pi over serial at a leisurely pace the Pi can always keep up
with.

Bonus: since both the machine and the Arduino run at 5V, **no level
shifter is needed anymore** — everything before the USB cable is a
direct 5V-to-5V connection.

---

## Hardware

### Connector

The machine side uses a **CN36 Centronics** female connector (not DB25).
See pinout below.

### Bill of materials

| Part | Notes |
|---|---|
| Arduino Nano Every | 5V logic — matches Centronics natively |
| Raspberry Pi 4 | Runs the display/logging app |
| 10.1" HDMI touchscreen (12V) | Model D90101-1HLC1EUIH-F; needs its own 12V PSU, **not** powered from Pi USB |
| CN36 female Centronics connector | Panel-mount or salvaged from a donor printer |
| 2× resistors 4.7kΩ | Pull-ups for SELECT / /ERROR status lines |
| USB cable (Arduino ↔ Pi) | Standard A–B or A–micro/USB-C depending on Nano Every revision |

> An earlier revision used a TXS0108E level shifter and a resistor divider
> for /STROBE, needed when the Pi read GPIO directly. **Not required** in
> the current Arduino-bridge architecture — kept here for reference only.

### CN36 → Arduino Nano Every wiring (direct, 5V–5V)

| CN36 pin | Signal | Arduino pin | Direction |
|---|---|---|---|
| 1 | /STROBE | D2 | machine → Arduino |
| 2–9 | D0–D7 | D5–D12 | machine → Arduino |
| 10 | /ACK | D4 | Arduino → machine |
| 11 | BUSY | D3 | Arduino → machine |
| 12 | PE (Paper End) | tied to GND | static — "paper present" |
| 13 | SELECT | 5V via 4.7kΩ | static — "printer online" |
| 32 | /ERROR | 5V via 4.7kΩ | static — "no error" |
| 16, 19–30, 33 | GND | GND | common ground |

### Optional: mains power-loss detection (GPIO26)

A relay contact wired between Pi `GPIO26` and `GND` triggers a clean
shutdown via the built-in overlay — no code needed:

```ini
# /boot/firmware/config.txt
dtoverlay=gpio-shutdown,gpio_pin=26,active_low=1,gpio_pull=up
```

Use a normally-open (NO) relay contact so the pin reads HIGH while
mains power is present and pulls LOW (triggering shutdown) when it's
cut. Feed the relay coil from the machine's mains/control circuit;
the Pi side of the contact only ever sees a dry 3.3V signal, so this
stays galvanically isolated from the machine's supply.

---

## Firmware — `lpt_serial_bridge.ino`

Flash to the Arduino Nano Every via Arduino IDE
(`Tools → Board → Arduino megaAVR Boards → Arduino Nano Every`).

What it does:
- `attachInterrupt` on /STROBE (hardware interrupt, microsecond latency)
- Reads the 8 data lines, pulses BUSY then /ACK per Centronics handshake
- Pushes each byte into a 512-byte ring buffer
- `loop()` drains the ring buffer to `Serial.write()` at USB-CDC speed

No configuration needed beyond the pin numbers already wired above.

---

## Software — `printer_monitor.py` (Raspberry Pi)

### Requirements

```bash
sudo apt install python3-tk python3-serial smbclient
```

(`smbclient` only needed if you use the SMB "send to share" feature.)

### Running

```bash
python3 printer_monitor.py
```

The app:
- Auto-detects the Arduino's serial port (by USB description, falling
  back to the first `/dev/ttyACM*` or `/dev/ttyUSB*`); auto-reconnects
  if the Arduino is unplugged/replugged
- Displays incoming text live, full-screen, touch-friendly, no window
  decorations (borderless kiosk via `overrideredirect`, not the
  `-fullscreen` attribute — more reliable across window managers,
  see "UI implementation notes" below)
- Writes an append-only log file per session to `~/printer_logs/`
- **Three languages** — Russian / English / Latvian, switchable live
  from the Options dialog, no restart needed
- **Send to share** and **Power off** — two large buttons split evenly
  across the footer. Send pushes the current log to an SMB share
  (Windows) or a local/mounted path, configurable via the **Options**
  dialog reachable from the menu (share path, subfolder, username,
  password — stored in `~/.printer_monitor.json`, file permissions
  `600`). Power off shows a Yes/No confirmation, then runs
  `sudo shutdown -h now`
- **☰ Menu button** (footer, right) opens a popup with: Clear screen,
  Minimize, Options, Close program, Cancel
- **Minimize** hides the main window (`withdraw()`) and shows a small
  floating "▲" button in the bottom-right corner to bring it back —
  deliberately not implemented via `iconify()`/taskbar (see notes below)

### UI implementation notes (for future maintainers)

A few non-obvious fixes are baked into the current version — worth
knowing before "simplifying" them back:

- **Footer must be packed with `side=tk.BOTTOM` before the text area
  is packed.** If the scrollable text area is packed first with
  `expand=True` and the footer afterwards, Tkinter's packer will
  collapse the footer to ~1px the moment total requested height
  exceeds the screen — not the text area, which is what should
  shrink. Pack footer first, `side=BOTTOM`, text area last.
- **Custom dialogs, not `tkinter.messagebox`.** The main window is
  `overrideredirect(True)` (no decorations). On this project's window
  manager, a normal WM-managed dialog (which `messagebox` creates)
  could end up stacked *behind* the borderless main window while
  still holding an input grab — the whole app looked frozen with no
  visible dialog. All confirmations/alerts now use a hand-rolled
  `_modal_dialog()` Toplevel that is *also* `overrideredirect(True)`,
  keeping it in the same stacking class as the main window.
- **Minimize doesn't use `iconify()`.** The obvious approach
  (`overrideredirect(False)` → `iconify()`, catching restore via
  `<Map>`/`<Unmap>`) turned out to depend on WM cooperation that
  wasn't reliable here — toggling `overrideredirect` on a live window
  didn't consistently hand it back to the WM. Instead, minimize just
  `withdraw()`s the root (pure Tk, no WM involved) and spawns an
  independent floating Toplevel button to bring it back — Toplevels
  stay visible even while their parent is withdrawn.

### Desktop launcher

`printer-monitor.desktop` + `printer-monitor.png` — copy both to
`~/Desktop/` (and optionally `~/.local/share/applications/`) for a
tap-to-launch icon.

### Autostart on boot

`printer-monitor-autostart.desktop` — copy to
`~/.config/autostart/printer-monitor.desktop`. Includes an 8-second
delay before launch to let the desktop session settle. Requires
desktop autologin to be enabled (`sudo raspi-config` → System Options
→ Boot → Desktop Autologin) — otherwise there's no session to
autostart into.

### Icons

Small PNGs loaded via `tk.PhotoImage` (no Pillow needed at runtime —
Tk 8.6+ reads PNG natively). Must sit next to `printer_monitor.py`:
`icon_upload.png` (send to share), `icon_power.png` (shutdown),
`icon_minimize.png` (minimize). If a file is missing, the affected
button silently falls back to a blank image rather than crashing.

### End-user documentation

Operator-facing manuals (not this technical README) exist in Russian
and Latvian: `Руководство_пользователя_RU.md` /
`Lietotaja_rokasgramata_LV.md`. They cover the UI from a "what do I
tap and what does this message mean" angle — hand these to whoever
runs the machine day to day, not this file.

### On-screen keyboard (if needed for config screens)

Raspberry Pi OS (Trixie) ships **Squeekboard**:

```
Preferences → Raspberry Pi Configuration → Display → On-screen Keyboard
```

---

## Known limitations / open items

- **Reverse-engineering ESC/OKI control codes**: the app currently
  strips anything outside printable ASCII + CR/LF. If the machine's
  ESC-sequence parameters happen to land in the printable range,
  occasional stray characters can appear. Not yet an issue in
  practice, but a proper ESC/P parser would eliminate the last
  theoretical edge case. OKI's Hex Dump Mode is a good way to capture
  raw bytes for this if it ever becomes necessary.
- **Time sync on an isolated network**: the machine's network blocks
  outbound NTP (UDP 123) entirely — confirmed via a local NTP server
  that itself failed root-distance checks, then via `chrony`/
  `timesyncd` both timing out on every public pool. HTTPS (443) is
  open, so time sync now runs via `htpdate` against a `cron` job
  (`@reboot`, retried every 5s for up to 30 attempts to ride out slow
  network bring-up, plus hourly). `systemd-timesyncd`/`chrony` are
  disabled to avoid them fighting over the clock.
- **USB enumeration instability**: seen intermittently when
  reconnecting the Arduino (`device descriptor read/64, error -110`,
  `unable to enumerate USB device` in `dmesg`) — same symptom appeared
  across different physical ports, with a USB touchscreen and a data
  drive also sharing the Pi's internal hub. Not yet root-caused;
  candidates are the USB cable, the connector on the Nano Every, or
  hub contention with the other devices. Worth re-testing with a
  known-good short data cable and the Arduino on the Pi 4's other USB
  controller group (ports are split into two internal hub pairs).
- **Long-run stability**: works correctly against real machine data
  (verified with full formatted multi-column reports); longer-term
  soak testing still recommended before treating this as fully
  production-hardened.

### Resolved since first written

- ~~Autostart on boot: not yet configured~~ — done, see
  `printer-monitor-autostart.desktop` above.
- ~~Window doesn't reliably go fullscreen / dialogs freeze the app~~
  — both were the same root cause (WM interaction with
  `overrideredirect`); see "UI implementation notes" above.

---

## Related idea: mains power-loss → Windows batch trigger

A colleague is using the same Arduino + relay pattern for an
unrelated purpose: detecting mains power loss and firing a `.bat`
script on a Windows PC over a USB-serial connection. Not part of this
repo, but documented here since it reuses the exact same
relay-to-GPIO detection technique described above.
