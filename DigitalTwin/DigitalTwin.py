import json
import logging
import os
import sys
import sqlite3
import time
import threading
import schedule

import paho.mqtt.client as mqtt

from datetime import datetime
from logging.handlers import RotatingFileHandler


# ============================================================
# CONFIGURAZIONE LOG
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def setup_logging():
    """Configura log su file, errori e terminale."""

    formatter = logging.Formatter(
        fmt=(
            "%(asctime)s.%(msecs)03d | "
            "%(levelname)-8s | "
            "%(name)-14s | "
            "%(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Evita di aggiungere più volte gli handler.
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()

    # File generale: INFO e livelli superiori.
    general_handler = RotatingFileHandler(
        os.path.join(BASE_DIR, "twin.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    general_handler.setLevel(logging.INFO)
    general_handler.setFormatter(formatter)

    # File errori: ERROR e CRITICAL.
    error_handler = RotatingFileHandler(
        os.path.join(BASE_DIR, "twin.err"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    # Output sul terminale.
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    root.addHandler(general_handler)
    root.addHandler(error_handler)
    root.addHandler(console_handler)


setup_logging()

log = logging.getLogger("DIGITAL_TWIN")
db_log = logging.getLogger("DATABASE")
mqtt_log = logging.getLogger("MQTT")
sched_log = logging.getLogger("SCHEDULER")


# ============================================================
# DATABASE
# ============================================================

class DatabaseManager:

    DAYS = (
        "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday"
    )

    def __init__(self, db_name="Digital_twin_db.db"):
        self.db_name = db_name
        self.db_path = os.path.join(BASE_DIR, db_name)
        self.static_id_patient = 1

        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self):
        """Crea le tabelle se non esistono."""

        try:
            with self.get_connection() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS patients (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        lat FLOAT,
                        lng FLOAT,
                        default_intake_time TEXT DEFAULT '10:00'
                    );

                    CREATE TABLE IF NOT EXISTS devices (
                        device_name TEXT PRIMARY KEY,
                        patient_id INTEGER,
                        lat FLOAT,
                        lng FLOAT,
                        status TEXT DEFAULT 'disconnected',
                        last_seen TEXT,
                        FOREIGN KEY (patient_id) REFERENCES patients(id)
                    );

                    CREATE TABLE IF NOT EXISTS week_status (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        device_name TEXT,
                        patient_id INTEGER,
                        timestamp TEXT,
                        monday BOOLEAN,
                        tuesday BOOLEAN,
                        wednesday BOOLEAN,
                        thursday BOOLEAN,
                        friday BOOLEAN,
                        saturday BOOLEAN,
                        sunday BOOLEAN,
                        FOREIGN KEY (device_name)
                            REFERENCES devices(device_name),
                        FOREIGN KEY (patient_id)
                            REFERENCES patients(id),
                        CHECK (
                            (device_name IS NOT NULL AND patient_id IS NULL)
                            OR
                            (device_name IS NULL AND patient_id IS NOT NULL)
                        )
                    );

                    CREATE TABLE IF NOT EXISTS schedules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        patient_id INTEGER,
                        enabled BOOLEAN,
                        intake_time TEXT DEFAULT '10:00',
                        FOREIGN KEY (patient_id) REFERENCES patients(id)
                    );
                """)

                conn.executescript('''
                INSERT OR IGNORE INTO patients (id, name, lat, lng, default_intake_time)
                VALUES (?, ?, NULL, NULL, NULL)
            ''', (self.static_id_patient, "default_patient"))

            db_log.info("Database inizializzato: %s", self.db_path)

        except sqlite3.Error:
            db_log.exception("Errore durante l'inizializzazione del database")
            raise

    def register_or_update_device(
        self, name=None, lat=None, lng=None,
        status="connected", last_seen=None
    ):
        """Registra o aggiorna un dispositivo."""

        with self.get_connection() as conn:
            conn.cursor.execute('''
                UPDATE devices
                SET status = ?,
                    last_seen = ?, lat = ?, lng = ?
                WHERE device_name = ?
            ''', (
                status, last_seen, lat, lng, name
                ))

            if conn.cursor.rowcount == 0:  # caso in cui la riga non esiste
                conn.cursor.execute('''
                    INSERT INTO devices (device_name, patient_id, status, last_seen, lat, lng)
                    VALUES (?, NULL, ?, ?, ?, ?)
                ''', (name, self.static_id_patient, status, last_seen, lat, lng))

        db_log.info(
            "Dispositivo aggiornato: %s, status=%s",
            name, status
        )

    def insert_or_update_status_device(self, device_name, timestamp, week):
        """Salva lo stato settimanale di un dispositivo."""

        values = tuple(int(bool(week.get(day, False))) for day in self.DAYS)

        with self.get_connection() as conn:
            cursor = conn.execute("""
                UPDATE week_status
                SET timestamp = ?,
                    monday = ?, tuesday = ?, wednesday = ?,
                    thursday = ?, friday = ?, saturday = ?, sunday = ?
                WHERE device_name = ? AND patient_id IS NULL
            """, (timestamp, *values, device_name))

            if cursor.rowcount == 0:
                conn.execute("""
                    INSERT INTO week_status (
                        device_name, patient_id, timestamp,
                        monday, tuesday, wednesday, thursday,
                        friday, saturday, sunday
                    )
                    VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (device_name, timestamp, *values))

        db_log.info("Stato settimanale salvato per device %s", device_name)

    def insert_or_update_status_person(self, timestamp, week):
        """Salva lo stato settimanale della persona."""

        values = tuple(int(bool(week.get(day, False))) for day in self.DAYS)

        with self.get_connection() as conn:
            cursor = conn.execute("""
                UPDATE week_status
                SET timestamp = ?,
                    monday = ?, tuesday = ?, wednesday = ?,
                    thursday = ?, friday = ?, saturday = ?, sunday = ?
                WHERE patient_id = ? AND device_name IS NULL
            """, (timestamp, *values, self.static_id_patient))

            if cursor.rowcount == 0:
                conn.execute("""
                    INSERT INTO week_status (
                        device_name, patient_id, timestamp,
                        monday, tuesday, wednesday, thursday,
                        friday, saturday, sunday
                    )
                    VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (self.static_id_patient, timestamp, *values))

        db_log.info("Stato settimanale della persona salvato")

    def load_devices_state(self):
        """Carica dispositivi e ultimo stato settimanale disponibile."""

        devices = {}

        with self.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM devices").fetchall()

            for row in rows:
                week_row = conn.execute("""
                    SELECT * FROM week_status
                    WHERE device_name = ? AND patient_id IS NULL
                    ORDER BY timestamp DESC, id DESC
                    LIMIT 1
                """, (row["device_name"],)).fetchone()

                week = {
                    day: bool(week_row[day]) if week_row else False
                    for day in self.DAYS
                }

                devices[row["device_name"]] = {
                    "name": row["device_name"],
                    "status": row["status"],
                    "last_seen": row["last_seen"],
                    "lat": row["lat"],
                    "lng": row["lng"],
                    "week": week
                }

        db_log.info("Caricati %d dispositivi", len(devices))
        return devices

    def load_global_state(self):
        """Carica lo stato settimanale della persona."""

        with self.get_connection() as conn:
            conn.row_factory = sqlite3.Row

            row = conn.execute("""
                SELECT * FROM week_status
                WHERE patient_id IS NOT NULL AND device_name IS NULL
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
            """).fetchone()

        if not row:
            return {day: False for day in self.DAYS}

        return {day: bool(row[day]) for day in self.DAYS}

    def reset_week_status_person(self):
        """Azzera lo stato settimanale della persona."""

        with self.get_connection() as conn:
            conn.execute("""
                UPDATE week_status
                SET timestamp = ?,
                    monday = 0, tuesday = 0, wednesday = 0,
                    thursday = 0, friday = 0, saturday = 0, sunday = 0
                WHERE patient_id IS NOT NULL AND device_name IS NULL
            """, (datetime.now().astimezone().isoformat(),))

        db_log.info("Stato settimanale della persona azzerato")

    def reset_week_status_devices(self):
        """Azzera gli stati settimanali dei dispositivi."""

        with self.get_connection() as conn:
            conn.execute("""
                UPDATE week_status
                SET timestamp = ?,
                    monday = 0, tuesday = 0, wednesday = 0,
                    thursday = 0, friday = 0, saturday = 0, sunday = 0
                WHERE device_name IS NOT NULL AND patient_id IS NULL
            """, (datetime.now().astimezone().isoformat(),))

        db_log.info("Stati settimanali dei dispositivi azzerati")

    def update_scheduler_config(self, enabled, new_time):
        """Salva la configurazione dello scheduler."""

        with self.get_connection() as conn:
            cursor = conn.execute("""
                UPDATE schedules
                SET enabled = ?, intake_time = ?
                WHERE patient_id = ?
            """, (int(bool(enabled)), new_time, self.static_id_patient))

            if cursor.rowcount == 0:
                conn.execute("""
                    INSERT INTO schedules (patient_id, enabled, intake_time)
                    VALUES (?, ?, ?)
                """, (self.static_id_patient, int(bool(enabled)), new_time))

        db_log.info(
            "Configurazione scheduler salvata: enabled=%s, time=%s",
            enabled, new_time
        )

    def update_user_location(self, lat, lng):
        """Aggiorna la posizione del paziente."""

        with self.get_connection() as conn:
            cursor = conn.execute("""
                UPDATE patients
                SET lat = ?, lng = ?
                WHERE id = ?
            """, (lat, lng, self.static_id_patient))

        if cursor.rowcount == 0:
            db_log.warning(
                "Posizione non salvata: paziente %s non presente",
                self.static_id_patient
            )
        else:
            db_log.info("Posizione del paziente aggiornata")

    def delete_device(self, device_name):
        """Elimina lo stato e il dispositivo."""

        with self.get_connection() as conn:
            conn.execute(
                "DELETE FROM week_status WHERE device_name = ?",
                (device_name,)
            )
            conn.execute(
                "DELETE FROM devices WHERE device_name = ?",
                (device_name,)
            )

        db_log.info("Dispositivo eliminato: %s", device_name)


# ============================================================
# COMANDI MQTT
# ============================================================

class PillCommands:

    DAILY_CHECK = "REQ"
    REQ_STATUS = "REQ_STATUS"
    REFILL = "REF"
    ALARM_ON = "ALM_ON"
    ALARM_OFF = "ALM_OFF"
    SYNC = "SYNC_REQ"

    DAYS = (
        "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday"
    )

    class FEEDS:
        COMMANDS = "pill-commands"
        BUZZER = "buzzer-commands"
        STATUS = "digital-twin-status"
        MESSAGES = "digital-twin-core"

    @staticmethod
    def get_day_code(day):
        try:
            return PillCommands.DAYS.index(day.lower())
        except (ValueError, AttributeError):
            return -1

    @staticmethod
    def get_day(code):
        if 0 <= code < 7:
            return PillCommands.DAYS[code]
        return "monday"


# ============================================================
# DIGITAL TWIN
# ============================================================

class PillBoxDigitalTwin:

    DAYS = PillCommands.DAYS

    def __init__(
        self, username, key,
        broker="io.adafruit.com", port=1883
    ):
        if not username or not key:
            raise ValueError(
                "Impostare ADA_USERNAME e ADA_KEY nelle variabili d'ambiente"
            )

        self.username = username
        self.key = key
        self.broker = broker
        self.port = port

        self.devices = {}
        self.week = {day: False for day in self.DAYS}

        self.scheduler_enabled = True
        self.hour_check = "11:10"
        self.user_lat = None
        self.user_lng = None

        self.db = DatabaseManager()
        self.week = self.db.load_global_state()
        self.devices = self.db.load_devices_state()

        self.client = mqtt.Client()
        self.client.username_pw_set(username, key)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

        self.check_disconnection()

        log.info(
            "Digital Twin inizializzato: broker=%s, port=%s",
            self.broker, self.port
        )

    # --------------------------------------------------------
    # MQTT
    # --------------------------------------------------------

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            mqtt_log.info("Connesso al broker %s", self.broker)

            client.subscribe(
                f"{self.username}/feeds/{PillCommands.FEEDS.MESSAGES}"
            )
            client.subscribe(
                f"{self.username}/feeds/{PillCommands.FEEDS.COMMANDS}"
            )

            self.publish_state()
        else:
            mqtt_log.error("Connessione MQTT fallita: rc=%s", rc)

    def on_disconnect(self, client, userdata, rc):
        if rc == 0:
            mqtt_log.info("Disconnessione MQTT richiesta")
        else:
            mqtt_log.warning(
                "Connessione MQTT interrotta: rc=%s", rc
            )

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            action = payload.get("action")

            mqtt_log.info(
                "Messaggio ricevuto: topic=%s action=%s",
                msg.topic, action
            )
            mqtt_log.debug("Payload: %s", payload)

            if action == PillCommands.DAILY_CHECK:
                self.handle_daily_check(payload)

            elif action == PillCommands.REQ_STATUS:
                self.handle_device_status(payload)

            elif action == "connection":
                self.handle_connection(payload)

            elif action == "scheduler_config":
                self.handle_scheduler_config(payload)

            elif action == "location_update":
                self.handle_location_update(payload)

            elif action == "print_db_debug":
                log.info("Stato globale DB: %s", self.db.load_global_state())
                log.info(
                    "Stato dispositivi DB: %s",
                    self.db.load_devices_state()
                )

            else:
                mqtt_log.debug("Azione non gestita: %s", action)

        except (ValueError, TypeError, KeyError, IndexError):
            mqtt_log.exception(
                "Messaggio MQTT non valido (topic=%s)", msg.topic
            )
        except Exception:
            mqtt_log.exception(
                "Errore durante la gestione del messaggio (topic=%s)",
                msg.topic
            )

    def handle_daily_check(self, payload):
        device = payload.get("device")
        timestamp = payload.get("timestamp")
        value = payload.get("val")

        if not timestamp:
            mqtt_log.warning("Timestamp mancante nella risposta DAILY_CHECK")
            return

        try:
            dt = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            )
        except ValueError:
            mqtt_log.warning("Timestamp non valido: %s", timestamp)
            return

        day = self.DAYS[dt.weekday()]

        if value != "taken":
            mqtt_log.info(
                "Pillola non confermata: device=%s giorno=%s",
                device, day
            )
            return

        self.week[day] = True

        if device in self.devices:
            self.devices[device]["week"][day] = True
            self.devices[device]["last_seen"] = timestamp

            self.db.insert_or_update_status_device(
                device, timestamp, self.devices[device]["week"]
            )

        self.db.insert_or_update_status_person(timestamp, self.week)

        mqtt_log.info(
            "Pillola confermata: device=%s giorno=%s",
            device, day
        )

        self.send_command(
            "all", PillCommands.ALARM_OFF, "OFF",
            PillCommands.FEEDS.BUZZER
        )

        self.publish_state()

    def handle_device_status(self, payload):
        device = payload.get("device")
        status = payload.get("status")
        timestamp = payload.get("timestamp")

        if device not in self.devices:
            mqtt_log.warning(
                "Stato ricevuto da dispositivo sconosciuto: %s", device
            )
            return

        if not isinstance(status, str) or len(status) < 7:
            mqtt_log.warning("Stato settimanale non valido: %s", status)
            return

        if not timestamp:
            timestamp = datetime.now().astimezone().isoformat()

        for i, day in enumerate(self.DAYS):
            self.devices[device]["week"][day] = status[i] == "0"

        self.devices[device]["last_seen"] = timestamp

        self.db.insert_or_update_status_device(
            device, timestamp, self.devices[device]["week"]
        )
        self.db.insert_or_update_status_person(timestamp, self.week)

        mqtt_log.info("Stato aggiornato per dispositivo %s", device)
        self.publish_state()

    def handle_connection(self, payload):
        device = payload.get("device")
        timestamp = payload.get("timestamp")
        lat = payload.get("lat")
        lng = payload.get("lng")

        if not device:
            mqtt_log.warning("Messaggio connection senza device")
            return

        if device not in self.devices:
            self.devices[device] = {
                "name": payload.get("name", device),
                "status": "connected",
                "last_seen": timestamp,
                "lat": lat,
                "lng": lng,
                "week": {day: False for day in self.DAYS}
            }
            mqtt_log.info("Nuovo dispositivo rilevato: %s", device)
        else:
            current = self.devices[device]
            current["status"] = "connected"
            current["last_seen"] = timestamp

            if lat is not None:
                current["lat"] = lat
            if lng is not None:
                current["lng"] = lng

        # Si usa l'identificativo del dispositivo come chiave DB.
        self.db.register_or_update_device(
            name=device,
            lat=lat,
            lng=lng,
            status="connected",
            last_seen=timestamp
        )

        self.publish_state()

    def handle_scheduler_config(self, payload):
        enabled = payload.get("enabled", True)
        new_time = payload.get("time", self.hour_check)

        if not isinstance(enabled, bool):
            mqtt_log.warning("Valore enabled non valido: %r", enabled)
            return

        try:
            time.strptime(new_time, "%H:%M")
        except (ValueError, TypeError):
            mqtt_log.warning("Orario scheduler non valido: %r", new_time)
            return

        self.update_scheduler(enabled, new_time)
        self.db.update_scheduler_config(enabled, new_time)

        sched_log.info(
            "Configurazione ricevuta: enabled=%s time=%s sender=%s",
            enabled, new_time, payload.get("sender", "unknown")
        )

    def handle_location_update(self, payload):
        try:
            lat = float(payload["lat"])
            lng = float(payload["lng"])

            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                raise ValueError("Coordinate fuori intervallo")

        except (KeyError, TypeError, ValueError):
            log.warning("Coordinate utente mancanti o non valide")
            return

        self.user_lat = lat
        self.user_lng = lng

        self.db.update_user_location(lat, lng)
        log.info("Posizione utente aggiornata")

    # --------------------------------------------------------
    # STATO E COMANDI
    # --------------------------------------------------------

    def publish_state(self):
        topic = (
            f"{self.username}/feeds/{PillCommands.FEEDS.STATUS}"
        )

        devices_payload = {
            name: {
                "name": device.get("name", name),
                "status": device.get("status", "unknown"),
                "last_seen": device.get("last_seen"),
                "lat": device.get("lat"),
                "lng": device.get("lng"),
                "week": device.get("week", {})
            }
            for name, device in self.devices.items()
        }

        payload = {
            **self.week,
            "_devices": devices_payload
        }

        try:
            result = self.client.publish(
                topic, json.dumps(payload), retain=True
            )

            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                mqtt_log.error(
                    "Pubblicazione stato fallita: rc=%s", result.rc
                )
            else:
                mqtt_log.info("Stato pubblicato su %s", topic)

        except Exception:
            mqtt_log.exception("Errore durante la pubblicazione dello stato")

    def send_command(self, target_device, command_type, value, feed):
        payload = {
            "target": target_device,
            "command": command_type,
            "val": value,
            "sender": "digital_twin_engine",
            "timestamp": datetime.now().astimezone().isoformat()
        }

        topic = f"{self.username}/feeds/{feed}"

        try:
            result = self.client.publish(topic, json.dumps(payload))

            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                mqtt_log.error(
                    "Invio comando fallito: target=%s command=%s rc=%s",
                    target_device, command_type, result.rc
                )
            else:
                mqtt_log.info(
                    "Comando inviato: target=%s command=%s value=%s",
                    target_device, command_type, value
                )

        except Exception:
            mqtt_log.exception(
                "Errore nell'invio del comando %s", command_type
            )

    def update_twin_state(self, status, device):
        if device not in self.devices:
            mqtt_log.warning("Dispositivo sconosciuto: %s", device)
            return

        if not isinstance(status, str) or len(status) < 7:
            mqtt_log.warning("Stato non valido per %s", device)
            return

        for i, day in enumerate(self.DAYS):
            self.devices[device]["week"][day] = status[i] == "0"

        self.publish_state()

    def find_nearest_device(self):
        if self.user_lat is None or self.user_lng is None:
            log.warning("Posizione utente sconosciuta: broadcast allarme")
            return "all"

        best_device = None
        best_distance = float("inf")

        for device_id, device in self.devices.items():
            lat = device.get("lat")
            lng = device.get("lng")

            if lat is None or lng is None:
                continue

            try:
                lat = float(lat)
                lng = float(lng)
            except (TypeError, ValueError):
                continue

            distance = (
                (lat - self.user_lat) ** 2 +
                (lng - self.user_lng) ** 2
            ) ** 0.5

            if distance < best_distance:
                best_distance = distance
                best_device = device_id

        if best_device is None:
            log.warning("Nessun dispositivo con coordinate disponibili")
            return "all"

        log.info(
            "Dispositivo più vicino: %s (distanza approssimata %.5f°)",
            best_device, best_distance
        )
        return best_device

    # --------------------------------------------------------
    # SCHEDULER
    # --------------------------------------------------------

    def update_scheduler(self, enabled, new_time):
        self.scheduler_enabled = enabled

        schedule.clear("daily_check")

        if enabled:
            self.hour_check = new_time
            schedule.every().day.at(
                self.hour_check
            ).do(self.trigger_global_check).tag("daily_check")

            sched_log.info("Check giornaliero pianificato alle %s", new_time)
        else:
            sched_log.info("Scheduler giornaliero disattivato")

    def run_scheduler_loop(self):
        schedule.every().day.at(
            self.hour_check
        ).do(self.trigger_global_check).tag("daily_check")

        schedule.every().sunday.at("21:00").do(
            self.trigger_global_refill
        ).tag("refill")

        schedule.every().sunday.at("00:00").do(
            self.trigger_reset
        ).tag("weekly_reset")

        schedule.every().day.at("11:21").do(
            self.trigger_global_refill
        ).tag("daily_refill")

        schedule.every().day.at("00:00").do(
            self.check_disconnection
        ).tag("disconnection")

        sched_log.info("Scheduler avviato")

        while True:
            try:
                schedule.run_pending()
            except Exception:
                sched_log.exception("Errore durante l'esecuzione scheduler")

            time.sleep(1)

    def trigger_global_check(self):
        if not self.scheduler_enabled:
            sched_log.info("Check saltato: scheduler disabilitato")
            return

        day = self.DAYS[datetime.now().weekday()]

        if self.week[day]:
            sched_log.info("Pillola già presa per %s", day)
            return

        sched_log.info("Avvio controllo giornaliero per %s", day)

        self.send_command(
            "all", PillCommands.DAILY_CHECK,
            PillCommands.get_day_code(day),
            PillCommands.FEEDS.COMMANDS
        )

        threading.Timer(600, self.last_chance_check).start()

    def last_chance_check(self):
        day = self.DAYS[datetime.now().weekday()]

        if self.week[day]:
            sched_log.info("Pillola confermata: secondo check annullato")
            return

        sched_log.warning("Pillola non confermata: secondo controllo")

        self.send_command(
            "all", PillCommands.DAILY_CHECK,
            PillCommands.get_day_code(day),
            PillCommands.FEEDS.COMMANDS
        )

        threading.Timer(100, self.final_alarm_verification).start()

    def final_alarm_verification(self):
        day = self.DAYS[datetime.now().weekday()]

        if not self.week[day]:
            sched_log.error("Pillola non confermata: attivazione allarme")

            target = self.find_nearest_device()

            self.send_command(
                target, PillCommands.ALARM_ON, "ON",
                PillCommands.FEEDS.BUZZER
            )

            threading.Timer(
                10,
                self.send_command,
                args=(
                    "all", PillCommands.ALARM_OFF, "OFF",
                    PillCommands.FEEDS.BUZZER
                )
            ).start()
        else:
            sched_log.info("Allarme annullato: pillola confermata")

    def trigger_global_refill(self):
        sched_log.info("Avvio controllo refill")

        day = self.DAYS[datetime.now().weekday()]

        self.send_command(
            "all", PillCommands.REQ_STATUS,
            PillCommands.get_day_code(day),
            PillCommands.FEEDS.COMMANDS
        )

        threading.Timer(100, self.refill_verification).start()

    def refill_verification(self):
        for device_id, device in self.devices.items():
            week = device.get("week", {})
            full = all(status is False for status in week.values())

            if not full:
                empty_days = [
                    day for day, status in week.items()
                    if status is True
                ]

                sched_log.error(
                    "Refill incompleto: device=%s giorni=%s",
                    device_id, empty_days
                )

                self.send_command(
                    device_id, PillCommands.ALARM_ON, "ON",
                    PillCommands.FEEDS.BUZZER
                )

                threading.Timer(
                    10,
                    self.send_command,
                    args=(
                        "all", PillCommands.ALARM_OFF, "OFF",
                        PillCommands.FEEDS.BUZZER
                    )
                ).start()
            else:
                sched_log.info("Refill verificato: device=%s", device_id)

    def trigger_reset(self):
        log.info("Avvio reset settimanale")

        self.week = {day: False for day in self.DAYS}

        for device in self.devices.values():
            device["week"] = {day: False for day in self.DAYS}

        timestamp = datetime.now().astimezone().isoformat()

        self.db.reset_week_status_person()
        self.db.reset_week_status_devices()

        # Salva lo stato azzerato anche per i dispositivi in memoria.
        for device_id, device in self.devices.items():
            self.db.insert_or_update_status_device(
                device_id, timestamp, device["week"]
            )

        self.db.insert_or_update_status_person(timestamp, self.week)
        self.publish_state()

        log.info("Reset settimanale completato")

    # --------------------------------------------------------
    # CONTROLLO CONNESSIONI
    # --------------------------------------------------------

    def check_disconnection(self):
        cutoff = time.time() - (48 * 60 * 60)

        for device_id, device in list(self.devices.items()):
            last_seen = device.get("last_seen")

            if not last_seen:
                continue

            try:
                if isinstance(last_seen, (int, float)):
                    last_seen_timestamp = float(last_seen)
                else:
                    last_seen_timestamp = datetime.fromisoformat(
                        last_seen.replace("Z", "+00:00")
                    ).timestamp()

                if last_seen_timestamp < cutoff:
                    device["status"] = "disconnected"

                    self.db.register_or_update_device(
                        name=device_id,
                        status="disconnected"
                    )

                    log.warning(
                        "Dispositivo disconnesso da oltre 48 ore: %s",
                        device_id
                    )

            except (TypeError, ValueError, OSError):
                log.warning(
                    "Timestamp last_seen non valido per %s: %r",
                    device_id, last_seen
                )

        self.publish_state()

    # --------------------------------------------------------
    # AVVIO
    # --------------------------------------------------------

    def start(self):
        scheduler_thread = threading.Thread(
            target=self.run_scheduler_loop,
            name="SchedulerThread",
            daemon=True
        )
        scheduler_thread.start()

        log.info(
            "Avvio Digital Twin: ora=%s giorno=%s",
            datetime.now().strftime("%H:%M:%S"),
            self.DAYS[datetime.now().weekday()]
        )

        try:
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_forever()
        except Exception:
            mqtt_log.exception("Errore fatale nel loop MQTT")
            raise
        finally:
            self.client.disconnect()
            log.info("Digital Twin arrestato")


# ============================================================
# MAIN
# ============================================================

def main():
    try:
        username = os.environ.get("ADA_USERNAME")
        key = os.environ.get("ADA_KEY")

        twin = PillBoxDigitalTwin(username, key)
        twin.start()

    except KeyboardInterrupt:
        log.info("Arresto richiesto dall'utente")

    except Exception:
        log.exception("Errore fatale durante l'avvio del Digital Twin")
        sys.exit(1)


if __name__ == "__main__":
    main()
