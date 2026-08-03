#!/usr/bin/env python3
# Printer Monitor — LPT data reception via Arduino bridge (Serial)
# All STROBE/BUSY/ACK timing lives on the Arduino; the Pi just reads bytes.

import serial
import serial.tools.list_ports
import tkinter as tk
from tkinter import scrolledtext
from datetime import datetime
import subprocess
import threading
import shutil
import queue
import json
import os

# === Settings ===
SERIAL_BAUD  = 115200
SERIAL_PORT  = None                      # None = auto-detect (by Arduino VID/PID),
                                          # or set manually e.g. "/dev/ttyACM0"
LOG_DIR      = "/home/raspberry/printer_logs"
DRAIN_MS     = 50                        # queue drain period, ms
CONFIG_FILE  = os.path.expanduser("~/.printer_monitor.json")
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
ICON_UPLOAD  = os.path.join(SCRIPT_DIR, "icon_upload.png")
ICON_MINIMIZE = os.path.join(SCRIPT_DIR, "icon_minimize.png")
ICON_POWER   = os.path.join(SCRIPT_DIR, "icon_power.png")

DEFAULT_CONFIG = {
    "share_path": "//192.168.8.10/logs",  # SMB share or local path
    "share_user": "",
    "share_pass": "",
    "share_subdir": "",                   # optional subfolder on the share
    "language": "ru",                     # ru / en / lv
}

# === Translations ===
LANGUAGES = [("ru", "RU"), ("en", "ENG"), ("lv", "LV")]

TR = {
    "ru": {
        "status_searching": "Поиск Arduino...",
        "status_not_found": "Arduino не найден...",
        "status_connected": "Подключено: {port}",
        "status_lost": "Соединение потеряно, ищу порт...",
        "status_last_byte": "Последний: {byte}",
        "status_sent": "Отправлен: {fname}",
        "status_sending": "Отправка на шару...",
        "status_send_error": "Ошибка отправки",
        "status_settings_saved": "Настройки сохранены",
        "msg_shutdown_title": "Выключение",
        "msg_shutdown_confirm": "Выключить Raspberry Pi?",
        "counter_bytes": "Байт получено: {n}",
        "log_label": "Лог: {path}",
        "btn_close": "✖ Закрыть программу",
        "btn_window": "🗗 Окно",
        "btn_maximize": "🗖 Макс",
        "btn_clear": "Очистить",
        "btn_minimize": "Свернуть",
        "btn_send": "На шару",
        "btn_sending": "⏳ Шлём...",
        "msg_send_title": "Отправка",
        "msg_send_empty": "Лог-файл пока пуст.",
        "msg_send_success": "Файл {fname} отправлен на шару.",
        "msg_send_error_title": "Ошибка отправки",
        "msg_smbclient_missing": "smbclient не установлен:\nsudo apt install smbclient",
        "options_title": "Опции — сетевая шара",
        "menu_title": "Меню",
        "field_share_path": "Путь шары (//host/share или /mnt/...):",
        "field_share_subdir": "Подпапка (опционально):",
        "field_share_user": "Пользователь:",
        "field_share_pass": "Пароль:",
        "field_language": "Язык / Language:",
        "btn_save": "💾 Сохранить",
        "btn_cancel": "Отмена",
        "btn_yes": "Да",
        "btn_no": "Нет",
        "btn_ok": "OK",
    },
    "en": {
        "status_searching": "Searching for Arduino...",
        "status_not_found": "Arduino not found...",
        "status_connected": "Connected: {port}",
        "status_lost": "Connection lost, searching...",
        "status_last_byte": "Last: {byte}",
        "status_sent": "Sent: {fname}",
        "status_sending": "Sending to share...",
        "status_send_error": "Send error",
        "status_settings_saved": "Settings saved",
        "msg_shutdown_title": "Shutdown",
        "msg_shutdown_confirm": "Shut down the Raspberry Pi?",
        "counter_bytes": "Bytes received: {n}",
        "log_label": "Log: {path}",
        "btn_close": "✖ Close program",
        "btn_window": "🗗 Window",
        "btn_maximize": "🗖 Maximize",
        "btn_clear": "Clear",
        "btn_minimize": "Minimize",
        "btn_send": "Send to share",
        "btn_sending": "⏳ Sending...",
        "msg_send_title": "Send",
        "msg_send_empty": "Log file is still empty.",
        "msg_send_success": "File {fname} sent to the share.",
        "msg_send_error_title": "Send error",
        "msg_smbclient_missing": "smbclient is not installed:\nsudo apt install smbclient",
        "options_title": "Options — network share",
        "menu_title": "Menu",
        "field_share_path": "Share path (//host/share or /mnt/...):",
        "field_share_subdir": "Subfolder (optional):",
        "field_share_user": "Username:",
        "field_share_pass": "Password:",
        "field_language": "Язык / Language:",
        "btn_save": "💾 Save",
        "btn_cancel": "Cancel",
        "btn_yes": "Yes",
        "btn_no": "No",
        "btn_ok": "OK",
    },
    "lv": {
        "status_searching": "Meklē Arduino...",
        "status_not_found": "Arduino nav atrasts...",
        "status_connected": "Pievienots: {port}",
        "status_lost": "Savienojums zaudēts, meklē portu...",
        "status_last_byte": "Pēdējais: {byte}",
        "status_sent": "Nosūtīts: {fname}",
        "status_sending": "Sūta uz koplietojumu...",
        "status_send_error": "Sūtīšanas kļūda",
        "status_settings_saved": "Iestatījumi saglabāti",
        "msg_shutdown_title": "Izslēgšana",
        "msg_shutdown_confirm": "Izslēgt Raspberry Pi?",
        "counter_bytes": "Saņemti baiti: {n}",
        "log_label": "Žurnāls: {path}",
        "btn_close": "✖ Aizvērt programmu",
        "btn_window": "🗗 Logs",
        "btn_maximize": "🗖 Pilnekrāns",
        "btn_clear": "Notīrīt",
        "btn_minimize": "Minimizēt",
        "btn_send": "Sūtīt uz koplietojumu",
        "btn_sending": "⏳ Sūta...",
        "msg_send_title": "Sūtīšana",
        "msg_send_empty": "Žurnāla fails vēl ir tukšs.",
        "msg_send_success": "Fails {fname} nosūtīts uz koplietojumu.",
        "msg_send_error_title": "Sūtīšanas kļūda",
        "msg_smbclient_missing": "smbclient nav instalēts:\nsudo apt install smbclient",
        "options_title": "Opcijas — tīkla koplietojums",
        "menu_title": "Izvēlne",
        "field_share_path": "Koplietojuma ceļš (//host/share vai /mnt/...):",
        "field_share_subdir": "Apakšmape (nav obligāti):",
        "field_share_user": "Lietotājvārds:",
        "field_share_pass": "Parole:",
        "field_language": "Язык / Language:",
        "btn_save": "💾 Saglabāt",
        "btn_cancel": "Atcelt",
        "btn_yes": "Jā",
        "btn_no": "Nē",
        "btn_ok": "Labi",
    },
}


def find_arduino_port():
    """Auto-detect the Arduino's serial port among connected USB-Serial devices."""
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        desc = (p.description or "").lower()
        if "arduino" in desc or "usb serial" in desc or "ch340" in desc:
            return p.device
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
    os.chmod(CONFIG_FILE, 0o600)  # password inside — owner-only

# === Log file ===
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

        # Запоминаем реальный размер экрана — понадобится при каждом
        # переключении в полноэкранный режим.
        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()

        self.byte_count = 0
        self.rx_queue = queue.Queue()
        self.config = load_config()
        self.ser = None
        self.is_sending = False
        self.connected = False
        self.last_port = None

        # Значки для кнопок — PNG рядом со скриптом.
        # tk.PhotoImage читает PNG нативно (Tcl/Tk 8.6+), сторонние библиотеки не нужны.
        try:
            self.upload_icon = tk.PhotoImage(file=ICON_UPLOAD)
        except (tk.TclError, FileNotFoundError):
            self.upload_icon = None  # файла нет рядом — кнопка останется просто текстом
        try:
            self.minimize_icon = tk.PhotoImage(file=ICON_MINIMIZE)
        except (tk.TclError, FileNotFoundError):
            self.minimize_icon = None
        try:
            self.power_icon = tk.PhotoImage(file=ICON_POWER)
        except (tk.TclError, FileNotFoundError):
            self.power_icon = None

        self._build_ui()

        # Атрибут -fullscreen зависит от поддержки протокола конкретным
        # оконным менеджером (на некоторых сборках Pi OS/labwc не срабатывает
        # при запуске через ярлык рабочего стола — окно остаётся с рамкой и
        # панелью поверх). overrideredirect убирает декорации напрямую,
        # без участия WM, и работает предсказуемо везде. Переключаем режим
        # только после того, как все виджеты уже созданы и упакованы —
        # иначе pack() для дочерних элементов иногда не срабатывает на
        # window, которое уже прошло withdraw()/deiconify() пустым.
        self._apply_window_mode(fullscreen=True)
        self.apply_language()
        self._setup_serial()

        self.restore_win = None

        self.root.after(DRAIN_MS, self._drain_queue)

    # ---------- Translation helper ----------
    def tr(self, key, **kwargs):
        lang = self.config.get("language", "ru")
        table = TR.get(lang, TR["ru"])
        text = table.get(key, TR["ru"].get(key, key))
        return text.format(**kwargs) if kwargs else text

    # ---------- UI ----------
    def _build_ui(self):
        header = tk.Frame(self.root, bg="#e0e0e0", pady=8)
        header.pack(fill=tk.X)

        tk.Label(header, text="Printer Monitor",
                 font=("Courier", 18, "bold"),
                 bg="#e0e0e0", fg="#006633").pack(side=tk.LEFT, padx=16)

        self.status_label = tk.Label(header, text="",
                                     font=("Courier", 13),
                                     bg="#e0e0e0", fg="#555555")
        self.status_label.pack(side=tk.LEFT, padx=20)

        self.counter_label = tk.Label(header, text="",
                                      font=("Courier", 12),
                                      bg="#e0e0e0", fg="#333333")
        self.counter_label.pack(side=tk.RIGHT, padx=16)

        # Footer пакуется ДО text_area и обязательно с side=BOTTOM.
        # Порядок критичен: если сначала запаковать text_area с fill=BOTH
        # expand=True, а footer после (оба default side=TOP), то при нехватке
        # места (суммарная запрошенная высота > высоты экрана) упаковщик
        # сжимает именно последний по порядку виджет — то есть footer с
        # кнопками схлопывается почти до нуля, а не text_area, которая как
        # раз и должна тут ужиматься/скроллиться.
        footer = tk.Frame(self.root, bg="#e0e0e0", pady=10)
        footer.pack(side=tk.BOTTOM, fill=tk.X)

        self.log_label = tk.Label(footer, text="",
                 font=("Courier", 10),
                 bg="#e0e0e0", fg="#777777")
        self.log_label.pack(side=tk.LEFT, padx=12)

        self.menu_btn = tk.Button(footer, text="☰", font=("Courier", 26, "bold"),
                  bg="#888888", fg="#ffffff",
                  activebackground="#666666", activeforeground="#ffffff",
                  relief=tk.FLAT, padx=24, pady=14,
                  command=self.open_menu)
        self.menu_btn.pack(side=tk.RIGHT, padx=10)

        self.send_btn = tk.Button(
                  footer, image=self.upload_icon,
                  bg="#33aa66", fg="#ffffff",
                  activebackground="#228844", activeforeground="#ffffff",
                  relief=tk.FLAT,
                  command=self.send_to_share)
        self.send_btn.pack(side=tk.RIGHT, padx=10, fill=tk.BOTH,
                           expand=True, ipady=20)

        self.power_btn = tk.Button(
                  footer, image=self.power_icon,
                  bg="#996633", fg="#ffffff",
                  activebackground="#774f26", activeforeground="#ffffff",
                  relief=tk.FLAT,
                  command=self.confirm_shutdown)
        self.power_btn.pack(side=tk.RIGHT, padx=10, fill=tk.BOTH,
                            expand=True, ipady=20)

        # text_area пакуется ПОСЛЕДНИМ — забирает всё, что осталось между
        # header (сверху) и footer (снизу, уже зарезервирован выше).
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

    def apply_language(self):
        """Refresh all static UI texts after a language change (or at startup)."""
        self.log_label.config(
            text=self.tr("log_label", path=os.path.basename(log_filename)))
        self.counter_label.config(text=self.tr("counter_bytes", n=self.byte_count))

        if self.connected and self.last_port:
            self.status_label.config(text=self.tr("status_connected", port=self.last_port))
        else:
            self.status_label.config(text=self.tr("status_searching"))

    # ---------- Serial (Arduino bridge) ----------
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
        """Opens the port (reconnecting on drop) and reads bytes."""
        while self.serial_running:
            if self.ser is None:
                self.ser = self._open_serial()
                if self.ser is None:
                    self.rx_queue.put(("status_not_found", None))
                    threading.Event().wait(2)
                    continue
                self.rx_queue.put(("status_connected", self.ser.port))

            try:
                data = self.ser.read(256)
                if data:
                    for b in data:
                        self.rx_queue.put(("byte", b))
            except (serial.SerialException, OSError):
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
                self.rx_queue.put(("status_lost", None))
                threading.Event().wait(1)

    # ---------- Queue handling in the GUI thread ----------
    def _drain_queue(self):
        got_any = False
        last_byte = None
        status_kind = None
        status_arg = None
        chunk = []

        while not self.rx_queue.empty():
            kind, val = self.rx_queue.get()
            if kind == "byte":
                got_any = True
                last_byte = val
                self.byte_count += 1
                if 32 <= val <= 126 or val in (10, 13):
                    chunk.append(chr(val))
            else:
                status_kind = kind
                status_arg = val
                if kind == "status_connected":
                    self.connected = True
                    self.last_port = val
                elif kind == "status_lost" or kind == "status_not_found":
                    self.connected = False

        if chunk:
            text = "".join(chunk)
            self._append_text(text)
            self._write_log(text)

        if got_any:
            self.counter_label.config(text=self.tr("counter_bytes", n=self.byte_count))
            self.status_label.config(
                text=self.tr("status_last_byte", byte=f"{last_byte:#04x}"), fg="#006633")
        elif status_kind == "status_connected":
            self.status_label.config(
                text=self.tr("status_connected", port=status_arg), fg="#006633")
        elif status_kind == "status_lost":
            self.status_label.config(text=self.tr("status_lost"), fg="#cc4444")
        elif status_kind == "status_not_found":
            self.status_label.config(text=self.tr("status_not_found"), fg="#cc4444")

        self.root.after(DRAIN_MS, self._drain_queue)

    def _append_text(self, text):
        self.text_area.config(state=tk.NORMAL)
        self.text_area.insert(tk.END, text)
        self.text_area.see(tk.END)
        self.text_area.config(state=tk.DISABLED)

    def _write_log(self, text):
        with open(log_filename, "a", encoding="utf-8") as f:
            f.write(text)

    # ---------- Собственный модальный диалог (вместо messagebox) ----------
    def _modal_dialog(self, title, message, buttons=None):
        """Показывает модальное окно в том же стиле overrideredirect, что и
        главное окно. Обычный tkinter.messagebox создаёт WM-управляемое
        окно, а наше — overrideredirect: на некоторых оконных менеджерах
        (в т.ч. используемом на Pi OS) окна этих двух разных типов
        конфликтуют по слоям — messagebox мог оказаться СПРЯТАН под
        полноэкранным приложением, но при этом продолжал держать модальный
        захват ввода — внешне это выглядело как полное зависание программы.
        Возвращает нажатую кнопку (строку) или None, если было только одно
        окно с кнопкой OK."""
        if buttons is None:
            buttons = [self.tr("btn_ok")]

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.configure(bg="#f0f0f0")

        w, h = 480, 220
        x = (self.screen_w - w) // 2
        y = (self.screen_h - h) // 2
        win.geometry(f"{w}x{h}+{x}+{y}")

        tk.Label(win, text=title, font=("Courier", 16, "bold"),
                 bg="#f0f0f0", fg="#000000").pack(pady=(24, 10))
        tk.Label(win, text=message, font=("Courier", 13),
                 bg="#f0f0f0", fg="#333333", wraplength=w - 40,
                 justify="center").pack(pady=6, padx=20)

        result = {"value": None}
        btn_frame = tk.Frame(win, bg="#f0f0f0")
        btn_frame.pack(pady=20)

        def make_cb(val):
            def cb():
                result["value"] = val
                win.grab_release()
                win.destroy()
            return cb

        for label in buttons:
            tk.Button(btn_frame, text=label, font=("Courier", 14, "bold"),
                      bg="#d0d0d0", fg="#000000", relief=tk.FLAT,
                      padx=22, pady=12,
                      command=make_cb(label)).pack(side=tk.LEFT, padx=10)

        win.lift()
        win.grab_set()
        win.focus_force()
        self.root.wait_window(win)
        return result["value"]

    # ---------- Send to share ----------
    def send_to_share(self):
        if not os.path.exists(log_filename) or os.path.getsize(log_filename) == 0:
            self._modal_dialog(self.tr("msg_send_title"), self.tr("msg_send_empty"))
            return

        self.is_sending = True
        self.status_label.config(text=self.tr("status_sending"), fg="#cc8800")
        self.send_btn.config(state=tk.DISABLED)
        self._pulse_send_btn()
        threading.Thread(target=self._do_send, daemon=True).start()

    def _pulse_send_btn(self):
        """Мигание кнопки между двумя оттенками зелёного, пока идёт отправка —
        явный визуальный признак того, что процесс не завис, а работает."""
        if not self.is_sending:
            self.send_btn.config(bg="#33aa66")
            return
        current = self.send_btn.cget("bg")
        next_color = "#77bb99" if current == "#33aa66" else "#33aa66"
        self.send_btn.config(bg=next_color)
        self.root.after(400, self._pulse_send_btn)

    def _do_send(self):
        cfg = self.config
        share = cfg["share_path"].strip()
        subdir = cfg["share_subdir"].strip().strip("/")
        fname = os.path.basename(log_filename)
        remote_name = f"{subdir}/{fname}" if subdir else fname

        try:
            if share.startswith("//") or share.startswith("\\\\"):
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
                dest_dir = os.path.join(share, subdir) if subdir else share
                os.makedirs(dest_dir, exist_ok=True)
                shutil.copy2(log_filename, os.path.join(dest_dir, fname))

            self.root.after(0, self._send_done, True, fname)
        except FileNotFoundError:
            self.root.after(0, self._send_done, False, self.tr("msg_smbclient_missing"))
        except Exception as e:
            self.root.after(0, self._send_done, False, str(e))

    def _send_done(self, ok, info):
        self.is_sending = False
        self.send_btn.config(state=tk.NORMAL, bg="#33aa66")
        if ok:
            self.status_label.config(text=self.tr("status_sent", fname=info), fg="#006633")
            self._modal_dialog(self.tr("msg_send_title"),
                               self.tr("msg_send_success", fname=info))
        else:
            self.status_label.config(text=self.tr("status_send_error"), fg="#cc4444")
            self._modal_dialog(self.tr("msg_send_error_title"), info)

    # ---------- Options dialog ----------
    # ---------- Меню (все действия кроме "Отправить") ----------
    def open_menu(self):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.configure(bg="#f0f0f0")

        w, h = 420, 420
        x = (self.screen_w - w) // 2
        y = (self.screen_h - h) // 2
        win.geometry(f"{w}x{h}+{x}+{y}")

        tk.Label(win, text=self.tr("menu_title"), font=("Courier", 18, "bold"),
                 bg="#f0f0f0", fg="#000000").pack(pady=(20, 14))

        body = tk.Frame(win, bg="#f0f0f0")
        body.pack(fill=tk.BOTH, expand=True, padx=24)

        def close_menu():
            win.grab_release()
            win.destroy()

        def act(fn):
            def cb():
                close_menu()
                fn()
            return cb

        items = [
            (self.tr("btn_clear"), None, "#d0d0d0", "#000000", self.clear_screen),
            (self.tr("btn_minimize"), self.minimize_icon, "#4477cc", "#ffffff", self.minimize_app),
            (self.tr("options_title"), None, "#888888", "#ffffff", self.open_options),
            (self.tr("btn_close"), None, "#cc4444", "#ffffff", self.quit_app),
        ]
        for label, icon, bg, fg, fn in items:
            b = tk.Button(body, text=label or "", image=icon, compound=tk.LEFT,
                          font=("Courier", 15, "bold"),
                          bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
                          relief=tk.FLAT, anchor="w", padx=18, pady=14,
                          command=act(fn))
            b.pack(fill=tk.X, pady=6)

        tk.Button(win, text=self.tr("btn_cancel"), font=("Courier", 15, "bold"),
                  bg="#e0e0e0", fg="#000000",
                  activebackground="#c8c8c8", activeforeground="#000000",
                  relief=tk.FLAT, pady=14,
                  command=close_menu).pack(fill=tk.X, padx=24, pady=(6, 18))

        win.lift()
        win.grab_set()
        win.focus_force()

    def open_options(self):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.configure(bg="#f0f0f0")

        w = min(560, self.screen_w - 40)
        h = min(520, self.screen_h - 40)
        x = (self.screen_w - w) // 2
        y = (self.screen_h - h) // 2
        win.geometry(f"{w}x{h}+{x}+{y}")

        # overrideredirect убирает системную рамку вместе с заголовком —
        # рисуем свой, иначе окно выглядит "безголовым"
        tk.Label(win, text=self.tr("options_title"), font=("Courier", 16, "bold"),
                 bg="#f0f0f0", fg="#000000").pack(pady=(16, 10))

        body = tk.Frame(win, bg="#f0f0f0")
        body.pack(fill=tk.BOTH, expand=True, padx=20)

        fields = [
            (self.tr("field_share_path"), "share_path"),
            (self.tr("field_share_subdir"), "share_subdir"),
            (self.tr("field_share_user"), "share_user"),
            (self.tr("field_share_pass"), "share_pass"),
        ]
        entries = {}
        for label, key in fields:
            tk.Label(body, text=label, font=("Courier", 12),
                     bg="#f0f0f0", fg="#000000", anchor="w"
                     ).pack(fill=tk.X, pady=(6, 2))
            e = tk.Entry(body, font=("Courier", 14),
                         bg="#ffffff", fg="#000000",
                         show="*" if key == "share_pass" else "")
            e.insert(0, self.config.get(key, ""))
            e.pack(fill=tk.X, ipady=6)
            entries[key] = e

        tk.Label(body, text=self.tr("field_language"), font=("Courier", 12),
                 bg="#f0f0f0", fg="#000000", anchor="w"
                 ).pack(fill=tk.X, pady=(12, 4))

        lang_var = tk.StringVar(value=self.config.get("language", "ru"))
        lang_frame = tk.Frame(body, bg="#f0f0f0")
        lang_frame.pack(fill=tk.X)
        for code, label in LANGUAGES:
            tk.Radiobutton(lang_frame, text=label, variable=lang_var, value=code,
                            font=("Courier", 13, "bold"),
                            bg="#f0f0f0", fg="#000000",
                            selectcolor="#ffffff",
                            activebackground="#f0f0f0"
                            ).pack(side=tk.LEFT, padx=8)

        def do_save():
            for key, e in entries.items():
                self.config[key] = e.get()
            self.config["language"] = lang_var.get()
            save_config(self.config)
            win.grab_release()
            win.destroy()
            self.apply_language()
            self.status_label.config(text=self.tr("status_settings_saved"), fg="#006633")

        def do_cancel():
            win.grab_release()
            win.destroy()

        btns = tk.Frame(win, bg="#f0f0f0")
        btns.pack(pady=16)

        tk.Button(btns, text=self.tr("btn_save"), font=("Courier", 14, "bold"),
                  bg="#33aa66", fg="#ffffff", relief=tk.FLAT,
                  padx=24, pady=12, command=do_save
                  ).pack(side=tk.LEFT, padx=10)
        tk.Button(btns, text=self.tr("btn_cancel"), font=("Courier", 14, "bold"),
                  bg="#d0d0d0", fg="#000000", relief=tk.FLAT,
                  padx=24, pady=12, command=do_cancel
                  ).pack(side=tk.LEFT, padx=10)

        win.lift()
        win.grab_set()
        win.focus_force()

    # ---------- Buttons ----------
    def _apply_window_mode(self, fullscreen):
        """Переключает окно между полноэкранным (без декораций, во весь
        экран) и оконным режимом. overrideredirect не зависит от того,
        поддерживает ли оконный менеджер атрибут -fullscreen — работает
        одинаково предсказуемо и через ярлык рабочего стола, и из терминала."""
        self.root.withdraw()  # прячем на время смены декораций — без мигания
        self.root.overrideredirect(fullscreen)
        if fullscreen:
            self.root.geometry(f"{self.screen_w}x{self.screen_h}+0+0")
        else:
            self.root.geometry("1024x600+40+40")
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def minimize_app(self):
        """Полностью прячет главное окно (withdraw — чистая операция Tk,
        не требует поддержки со стороны оконного менеджера, в отличие от
        связки overrideredirect+iconify, которая не срабатывала на
        используемом WM). Поверх показываем маленькую плавающую кнопку —
        дочерние Toplevel-окна остаются видимыми независимо от того,
        спрятан ли их родитель root."""
        self.root.withdraw()
        self._show_restore_button()

    def _show_restore_button(self):
        size, margin = 90, 20
        x = self.screen_w - size - margin
        y = self.screen_h - size - margin

        self.restore_win = tk.Toplevel(self.root)
        self.restore_win.overrideredirect(True)
        self.restore_win.geometry(f"{size}x{size}+{x}+{y}")

        btn = tk.Button(self.restore_win, text="▲", font=("Courier", 30, "bold"),
                        bg="#4477cc", fg="#ffffff",
                        activebackground="#2255aa", activeforeground="#ffffff",
                        relief=tk.FLAT, command=self.restore_app)
        btn.pack(fill=tk.BOTH, expand=True)

        self.restore_win.lift()
        self.restore_win.attributes("-topmost", True)

    def restore_app(self):
        if self.restore_win is not None:
            try:
                self.restore_win.destroy()
            except tk.TclError:
                pass
            self.restore_win = None
        self.root.deiconify()
        self._apply_window_mode(True)

    def confirm_shutdown(self):
        answer = self._modal_dialog(
            self.tr("msg_shutdown_title"),
            self.tr("msg_shutdown_confirm"),
            buttons=[self.tr("btn_yes"), self.tr("btn_no")])
        if answer == self.tr("btn_yes"):
            try:
                subprocess.Popen(["sudo", "shutdown", "-h", "now"])
            except Exception as e:
                self._modal_dialog(self.tr("msg_shutdown_title"), str(e))

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
