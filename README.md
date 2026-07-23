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
- Displays incoming text live, full-screen, touch-friendly
- Writes an append-only log file per session to `~/printer_logs/`
- **Send to share** — pushes the current log to an SMB share (Windows)
  or a local/mounted path, configurable via the **Options** dialog
  (share path, subfolder, username, password — stored in
  `~/.printer_monitor.json`, file permissions `600`)
- **Window / Fullscreen** toggle and **Clear** button, sized for touch

### Desktop launcher

`printer-monitor.desktop` + `printer-monitor.png` — copy both to
`~/Desktop/` (and optionally `~/.local/share/applications/`) for a
tap-to-launch icon.

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
- **Autostart on boot**: not yet configured — currently launched
  manually or via the desktop icon.
- **Long-run stability**: works correctly against real machine data
  (verified with full formatted multi-column reports); longer-term
  soak testing still recommended before treating this as fully
  production-hardened.

---

## Related idea: mains power-loss → Windows batch trigger

A colleague is using the same Arduino + relay pattern for an
unrelated purpose: detecting mains power loss and firing a `.bat`
script on a Windows PC over a USB-serial connection. Not part of this
repo, but documented here since it reuses the exact same
relay-to-GPIO detection technique described above.
