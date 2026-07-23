#!/usr/bin/env python3
# Printer Monitor — приём данных LPT через Arduino-мост (Serial)
# Вся синхронизация STROBE/BUSY/ACK — на Arduino, Pi просто читает поток байт

import serial
import serial.tools.list_ports
import tkinter as tk
from tkinter import scrolledtext, messagebox
from datetime import datetime
import subprocess
import threading
import shutil
import queue
import json
import os

# === Настройки ===
SERIAL_BAUD  = 115200
SERIAL_PORT  = None                      # None = автопоиск (Arduino по VID/PID),
                                          # либо укажи вручную "/dev/ttyACM0"
LOG_DIR      = "/home/raspberry/printer_logs"
DRAIN_MS     = 50                        # период выборки очереди, мс
CONFIG_FILE  = os.path.expanduser("~/.printer_monitor.json")

DEFAULT_CONFIG = {
    "share_path": "//192.168.8.10/logs",  # SMB-шара или локальный путь
    "share_user": "",
    "share_pass": "",
    "share_subdir": ""                    # подпапка на шаре, опционально
}


def find_arduino_port():
    """Автопоиск порта Arduino среди подключённых USB-Serial устройств."""
    ports = list(serial.tools.list_ports.comports())
    # Сначала ищем по типичным именам Arduino
    for p in ports:
        desc = (p.description or "").lower()
        if "arduino" in desc or "usb serial" in desc or "ch340" in desc:
            return p.device
    # Иначе берём первый попавшийся ttyACM/ttyUSB
    for p in ports:
        if "ttyACM" in p.device or "ttyUSB" in p.device:
            return p.device
    return None


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return {**DEFAULT_CONFIG, **cfg}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.chmod(CONFIG_FILE, 0o600)  # пароль внутри — только владельцу

# === Файл лога ===
os.makedirs(LOG_DIR, exist_ok=True)
log_filename = os.path.join(
    LOG_DIR, datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".txt"
)


class PrinterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Printer Monitor")
        self.root.configure(bg="#f0f0f0")
        self.is_fullscreen = True
        self.root.attributes("-fullscreen", True)

        self.byte_count = 0
        self.rx_queue = queue.Queue()
        self.config = load_config()
        self.ser = None

        self._build_ui()
        self._setup_serial()

        # периодическая выборка очереди в GUI-потоке
        self.root.after(DRAIN_MS, self._drain_queue)

    # ---------- UI ----------
    def _build_ui(self):
        header = tk.Frame(self.root, bg="#e0e0e0", pady=8)
        header.pack(fill=tk.X)

        tk.Label(header, text="Printer Monitor",
                 font=("Courier", 18, "bold"),
                 bg="#e0e0e0", fg="#006633").pack(side=tk.LEFT, padx=16)

        self.status_label = tk.Label(header, text="Поиск Arduino...",
                                     font=("Courier", 13),
                                     bg="#e0e0e0", fg="#555555")
        self.status_label.pack(side=tk.LEFT, padx=20)

        self.counter_label = tk.Label(header, text="Байт получено: 0",
                                      font=("Courier", 12),
                                      bg="#e0e0e0", fg="#333333")
        self.counter_label.pack(side=tk.RIGHT, padx=16)

        # Основная область — принятый текст (белый фон, чёрный текст)
        self.text_area = scrolledtext.ScrolledText(
            self.root,
            font=("Courier", 14),
            bg="#ffffff", fg="#000000",
            insertbackground="black",
            wrap=tk.WORD,
            state=tk.DISABLED,
            padx=12, pady=8
        )
        self.text_area.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        footer = tk.Frame(self.root, bg="#e0e0e0", pady=10)
        footer.pack(fill=tk.X)

        tk.Label(footer, text=f"Лог: {log_filename}",
                 font=("Courier", 10),
                 bg="#e0e0e0", fg="#777777").pack(side=tk.LEFT, padx=12)

        # Крупные кнопки под тач
        tk.Button(footer, text="✖ Закрыть", font=("Courier", 16, "bold"),
                  bg="#cc4444", fg="#ffffff",
                  activebackground="#aa2222", activeforeground="#ffffff",
                  relief=tk.FLAT, padx=28, pady=16,
                  command=self.quit_app).pack(side=tk.RIGHT, padx=10)

        self.fullscreen_btn = tk.Button(
                  footer, text="🗗 Окно", font=("Courier", 16, "bold"),
                  bg="#4477cc", fg="#ffffff",
                  activebackground="#2255aa", activeforeground="#ffffff",
                  relief=tk.FLAT, padx=28, pady=16,
                  command=self.toggle_fullscreen)
        self.fullscreen_btn.pack(side=tk.RIGHT, padx=10)

        tk.Button(footer, text="Очистить", font=("Courier", 16, "bold"),
                  bg="#d0d0d0", fg="#000000",
                  activebackground="#b0b0b0", activeforeground="#000000",
                  relief=tk.FLAT, padx=28, pady=16,
                  command=self.clear_screen).pack(side=tk.RIGHT, padx=10)

        self.send_btn = tk.Button(
                  footer, text="📤 На шару", font=("Courier", 16, "bold"),
                  bg="#33aa66", fg="#ffffff",
                  activebackground="#228844", activeforeground="#ffffff",
                  relief=tk.FLAT, padx=28, pady=16,
                  command=self.send_to_share)
        self.send_btn.pack(side=tk.RIGHT, padx=10)

        tk.Button(footer, text="⚙ Опции", font=("Courier", 16, "bold"),
                  bg="#888888", fg="#ffffff",
                  activebackground="#666666", activeforeground="#ffffff",
                  relief=tk.FLAT, padx=28, pady=16,
                  command=self.open_options).pack(side=tk.RIGHT, padx=10)

    # ---------- Serial (Arduino-мост) ----------
    def _setup_serial(self):
        self.serial_running = True
        self.poll_thread = threading.Thread(target=self._serial_loop, daemon=True)
        self.poll_thread.start()

    def _open_serial(self):
        port = SERIAL_PORT or find_arduino_port()
        if not port:
            return None
        try:
            return serial.Serial(port, SERIAL_BAUD, timeout=0.2)
        except serial.SerialException:
            return None

    def _serial_loop(self):
        """Открывает порт (с переподключением при обрыве) и читает байты."""
        while self.serial_running:
            if self.ser is None:
                self.ser = self._open_serial()
                if self.ser is None:
                    self.rx_queue.put(("status", "Arduino не найден..."))
                    threading.Event().wait(2)
                    continue
                self.rx_queue.put(("status", f"Подключено: {self.ser.port}"))

            try:
                data = self.ser.read(256)  # блокирует максимум на timeout
                if data:
                    for b in data:
                        self.rx_queue.put(("byte", b))
            except (serial.SerialException, OSError):
                # Порт пропал — Arduino отключили/перезагрузили
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
                self.rx_queue.put(("status", "Соединение потеряно, ищу порт..."))
                threading.Event().wait(1)

    # ---------- Обработка очереди в GUI-потоке ----------
    def _drain_queue(self):
        got_any = False
        last_byte = None
        status_text = None
        chunk = []

        while not self.rx_queue.empty():
            kind, val = self.rx_queue.get()
            if kind == "status":
                status_text = val
                continue
            byte = val
            got_any = True
            last_byte = byte
            self.byte_count += 1
            if 32 <= byte <= 126 or byte in (10, 13):
                chunk.append(chr(byte))

        if chunk:
            text = "".join(chunk)
            self._append_text(text)
            self._write_log(text)

        if got_any:
            self.counter_label.config(
                text=f"Байт получено: {self.byte_count}")
            self.status_label.config(
                text=f"Последний: {last_byte:#04x}", fg="#006633")
        elif status_text:
            self.status_label.config(text=status_text, fg="#cc4444")

        self.root.after(DRAIN_MS, self._drain_queue)

    def _append_text(self, text):
        self.text_area.config(state=tk.NORMAL)
        self.text_area.insert(tk.END, text)
        self.text_area.see(tk.END)
        self.text_area.config(state=tk.DISABLED)

    def _write_log(self, text):
        with open(log_filename, "a", encoding="utf-8") as f:
            f.write(text)

    # ---------- Отправка на шару ----------
    def send_to_share(self):
        """Отправить текущий лог-файл на сетевую шару (в фоне)."""
        if not os.path.exists(log_filename) or os.path.getsize(log_filename) == 0:
            messagebox.showinfo("Отправка", "Лог-файл пока пуст.")
            return

        self.send_btn.config(state=tk.DISABLED, text="⏳ Шлём...")
        threading.Thread(target=self._do_send, daemon=True).start()

    def _do_send(self):
        cfg = self.config
        share = cfg["share_path"].strip()
        subdir = cfg["share_subdir"].strip().strip("/")
        fname = os.path.basename(log_filename)
        remote_name = f"{subdir}/{fname}" if subdir else fname

        try:
            if share.startswith("//") or share.startswith("\\\\"):
                # SMB через smbclient
                share_norm = share.replace("\\", "/")
                auth = f"{cfg['share_user']}%{cfg['share_pass']}" \
                    if cfg["share_user"] else "%"
                cmd = ["smbclient", share_norm, "-U", auth,
                       "-c", f'put "{log_filename}" "{remote_name}"']
                r = subprocess.run(cmd, capture_output=True,
                                   text=True, timeout=30)
                if r.returncode != 0:
                    raise RuntimeError(r.stderr.strip() or r.stdout.strip()
                                       or "smbclient error")
            else:
                # Локальный/примонтированный путь — просто копируем
                dest_dir = os.path.join(share, subdir) if subdir else share
                os.makedirs(dest_dir, exist_ok=True)
                shutil.copy2(log_filename, os.path.join(dest_dir, fname))

            self.root.after(0, self._send_done, True, fname)
        except FileNotFoundError:
            self.root.after(0, self._send_done, False,
                            "smbclient не установлен:\n"
                            "sudo apt install smbclient")
        except Exception as e:
            self.root.after(0, self._send_done, False, str(e))

    def _send_done(self, ok, info):
        self.send_btn.config(state=tk.NORMAL, text="📤 На шару")
        if ok:
            self.status_label.config(text=f"Отправлен: {info}", fg="#006633")
            messagebox.showinfo("Отправка", f"Файл {info} отправлен на шару.")
        else:
            self.status_label.config(text="Ошибка отправки", fg="#cc4444")
            messagebox.showerror("Ошибка отправки", info)

    # ---------- Диалог опций ----------
    def open_options(self):
        win = tk.Toplevel(self.root)
        win.title("Опции — сетевая шара")
        win.configure(bg="#f0f0f0")
        win.transient(self.root)
        win.grab_set()

        fields = [
            ("Путь шары (//host/share или /mnt/...):", "share_path"),
            ("Подпапка (опционально):", "share_subdir"),
            ("Пользователь:", "share_user"),
            ("Пароль:", "share_pass"),
        ]
        entries = {}
        for row, (label, key) in enumerate(fields):
            tk.Label(win, text=label, font=("Courier", 13),
                     bg="#f0f0f0", fg="#000000", anchor="w"
                     ).grid(row=row, column=0, sticky="w",
                            padx=14, pady=8)
            e = tk.Entry(win, font=("Courier", 14), width=30,
                         bg="#ffffff", fg="#000000",
                         show="*" if key == "share_pass" else "")
            e.insert(0, self.config.get(key, ""))
            e.grid(row=row, column=1, padx=14, pady=8, ipady=6)
            entries[key] = e

        def do_save():
            for key, e in entries.items():
                self.config[key] = e.get()
            save_config(self.config)
            win.destroy()
            self.status_label.config(text="Настройки сохранены",
                                     fg="#006633")

        btns = tk.Frame(win, bg="#f0f0f0")
        btns.grid(row=len(fields), column=0, columnspan=2, pady=14)

        tk.Button(btns, text="💾 Сохранить", font=("Courier", 14, "bold"),
                  bg="#33aa66", fg="#ffffff", relief=tk.FLAT,
                  padx=24, pady=12, command=do_save
                  ).pack(side=tk.LEFT, padx=10)
        tk.Button(btns, text="Отмена", font=("Courier", 14, "bold"),
                  bg="#d0d0d0", fg="#000000", relief=tk.FLAT,
                  padx=24, pady=12, command=win.destroy
                  ).pack(side=tk.LEFT, padx=10)

    # ---------- Кнопки ----------
    def toggle_fullscreen(self):
        self.is_fullscreen = not self.is_fullscreen
        self.root.attributes("-fullscreen", self.is_fullscreen)
        if self.is_fullscreen:
            self.fullscreen_btn.config(text="🗗 Окно")
        else:
            self.root.geometry("1024x600")
            self.fullscreen_btn.config(text="🗖 Макс")

    def clear_screen(self):
        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete(1.0, tk.END)
        self.text_area.config(state=tk.DISABLED)

    def quit_app(self):
        self.serial_running = False
        self.poll_thread.join(timeout=1)
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = PrinterApp(root)
    root.mainloop()
