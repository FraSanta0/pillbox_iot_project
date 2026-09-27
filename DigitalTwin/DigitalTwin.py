import paho.mqtt.client as mqtt
import json
import time
import threading
import schedule
from datetime import datetime
import sys
import os

class PillCommands:
    # Comandi verso le schede
    DAILY_CHECK = "REQ"
    REQ_STATUS = "REQ_STATUS"
    REFILL = "REF"
    ALARM_ON = "ALM_ON"
    ALARM_OFF = "ALM_OFF"
    SYNC = "SYNC_REQ"

    class FEEDS :
        COMMANDS = "pill-commands"
        BUZZER = "buzzer-commands"
        STATUS = "digital-twin-status"
        MESSAGES = "digital-twin-core"
    
    # Metodo per ottenere la stringa formattata
    @staticmethod
    def get(command_name):
        return getattr(PillCommands, command_name.upper(), "UNKNOWN")
    
    @staticmethod
    def get_command_by_day(day):
        day = day.lower()
        if day == "monday":
            return PillCommands.MONDAY_CHECK
        elif day == "tuesday":
            return PillCommands.TUESDAY_CHECK
        elif day == "wednesday":
            return PillCommands.WEDNESDAY_CHECK
        elif day == "thursday":
            return PillCommands.THURSDAY_CHECK
        elif day == "friday":
            return PillCommands.FRIDAY_CHECK
        elif day == "saturday":
            return PillCommands.SATURDAY_CHECK
        elif day == "sunday":
            return PillCommands.SUNDAY_CHECK
        else:
            return "UNKNOWN"
        
    @staticmethod
    def get_day_code(day):
        day = day.lower()
        if day == "monday":
            return 0
        elif day == "tuesday":
            return 1
        elif day == "wednesday":
            return 2
        elif day == "thursday":
            return 3
        elif day == "friday":
            return 4
        elif day == "saturday":
            return 5
        elif day == "sunday":
            return 6
        else:
            return -1
        
    @staticmethod
    def get_day(day):
        if day == 0:
            return "monday"
        elif day == 1:
            return "tuesday"
        elif day == 2:
            return "wednesday"
        elif day == 3:
            return "thursday"
        elif day == 4:
            return "friday"
        elif day == 5:
            return "saturday"
        elif day == 6:
            return "sunday"
        else:
            return "monday"


class PillBoxDigitalTwin:
    def __init__(self, username, key, broker="io.adafruit.com", port=1883):
        self.username = username
        self.key = key
        self.broker = broker
        self.port = port

        self.devices = {}
        self.scheduler_enabled = True  # Controllabile da app
        self.user_lat = None  # Posizione utente (aggiornata dall'app)
        self.user_lng = None
        
        self.week = {
            "monday" : False,
            "tuesday" : False,
            "wednesday" : False,
            "thursday" : False,
            "friday" : False,
            "saturday" : False,
            "sunday" : False,

        }

        # Stato interno del Twin
        self.state = {
            "last_update": None,
        }

        self.client = mqtt.Client()
        self.client.username_pw_set(self.username, self.key)
        
        # Assegnazione dei metodi di callback
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc):
        """Gestisce l'apertura della connessione e si iscrive ai feeds"""
        if rc == 0:
            print(f"[*] Connesso al Broker Adafruit come: {self.username}")
            # Iscrizione ai feed (Topic)
            client.subscribe(f"{self.username}/feeds/digital-twin-core")
            client.subscribe(f"{self.username}/feeds/pill-commands")
        else:
            print(f"[!] Errore di connessione, codice: {rc}")

    def on_message(self, client, userdata, msg):
        """Gestisce i messaggi in entrata"""
        try:
            payload = json.loads(msg.payload.decode())
            topic = msg.topic
            print(f"[<-] Ricevuto su {topic}: {payload}")

            if "action" in payload and payload["action"] == PillCommands.DAILY_CHECK:
                device = payload.get("device")
                timestamp = payload.get("timestamp")
                dt_obj = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                day = dt_obj.strftime("%A").lower()
                if payload.get("val") == "taken" and self.week[day] != True:
                    self.week[day] = True
                    if device in self.devices:
                        self.devices[device]["week"][day] = True
                        self.devices[device]["last_seen"] = timestamp
                    print(f"[#] Pillola del {day} PRESA da {device}. Stato settimana: {self.week}")
                    self.send_command(target_device="all", command_type=PillCommands.ALARM_OFF, value="OFF", feed=PillCommands.FEEDS.BUZZER)
                    self.publish_state()

            if "action" in payload and payload["action"] == PillCommands.REQ_STATUS:
                status = payload.get("status")
                device = payload.get("device")
                time = payload.get("timestamp")
                self.devices[device]["last seen"] = time
                self.update_twin_state(status=status, device=device)

            if "action" in payload and payload["action"] == "connection":
                payload = json.loads(msg.payload.decode())
                device_name = payload.get("device")
                time = payload.get("timestamp")
                lat = None
                lng = None
                if lat in payload:
                    lat = payload.get("lat")
                    lng = payload.get("lng")

                if device_name not in self.devices:
                    self.devices[device_name] = {
                        "name": payload.get("name", f"{device_name}"),
                        "status": "connected",
                        "last_seen": time,
                        "lat": lat,
                        "lng":lng,
                        "week": {day: False for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]}
                    }
                    print(f"[*] Registrato nuovo dispositivo: {device_name}")
                else:
                    self.devices[device_name]["status"] = "connected"
                    self.devices[device_name]["last_seen"] = time
                self.publish_state()

            if "action" in payload and payload["action"] == "scheduler_config":
                enabled = payload.get("enabled", True)
                new_time = payload.get("time", self.hour_check)
                sender = payload.get("sender", "unknown")
                print(f"[*] Ricevuta configurazione scheduler da {sender}: enabled={enabled}, time={new_time}")
                self.update_scheduler(enabled=enabled, new_time=new_time)

            if "action" in payload and payload["action"] == "location_update":
                self.user_lat = payload.get("lat")
                self.user_lng = payload.get("lng")
                print(f"[*] Posizione utente aggiornata: lat={self.user_lat}, lng={self.user_lng}")
                
        except Exception as e:
            print(f"[!] Errore nel parsing del messaggio: {e}")

    def update_twin_state(self, status, device):
        """Metodo per cambiare lo stato del singolo device"""
        
        for i in range(7):
            if(status[i] == "0"):
                self.devices[device]["week"][PillCommands.get_day(i)] = True
            else: 
                self.devices[device]["week"][PillCommands.get_day(i)] = False
        
        #print(f"[#] Stato Twin aggiornato: {self.week}")
        
        # Notifica il nuovo stato a tutti i dispositivi (Broadcast)
        self.publish_state()

    def find_nearest_device(self):
        """
        Restituisce il "name" del portapillole più vicino alla posizione dell'utente.
        Se la posizione dell'utente o dei dispositivi non è disponibile, restituisce "all"(broadcast).
        """
        if self.user_lat is None or self.user_lng is None:
            print("[~] Posizione utente non disponibile: allarme a tutti i dispositivi.")
            return "all"

        best_device = None
        best_dist = float("inf")

        for device_id, d in self.devices.items():
            lat = d.get("lat")
            lng = d.get("lng")
            if lat is None or lng is None:
                continue
            dist = ((lat - self.user_lat) ** 2 + (lng - self.user_lng) ** 2) ** 0.5 #distanza euclidea
            if dist < best_dist:
                best_dist = dist
                best_device = device_id

        if best_device is None:
            print("[~] Nessun dispositivo con posizione nota: allarme a tutti.")
            return "all"

        print(f"[~] Dispositivo più vicino all'utente: {best_device} (distanza ~{best_dist:.5f}°)")
        return best_device

    def publish_state(self):
        """Invia lo stato attuale sul feed (payload completo con week + devices)"""
        topic = f"{self.username}/feeds/"+PillCommands.FEEDS.STATUS

        devices_payload = {
            name: {
                "name": d.get("name", name),
                "status": d.get("status", "unknown"),
                "last_seen": d.get("last_seen"),
                "lat":d.get("lat"),
                "lng":d.get("lng"),
                "week": d.get("week", {})
            }
            for name, d in self.devices.items()
        }

        payload = {
            **self.week,                    # monday, tuesday, ... (stato globale utente)
            "_devices": devices_payload     # stato per-dispositivo
        }

        self.client.publish(topic, json.dumps(payload), retain=True)
        print(f"[->] Stato pubblicato su {topic}")

    def send_command(self, target_device, command_type, value, feed):
        """Metodo per inviare messaggi ai vari dispositivi"""    
        payload = {
            "target": target_device,
            "command": command_type,
            "val": value,
            "sender": "digital_twin_engine",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }

        topic = f"{self.username}/feeds/"+feed

        self.client.publish(topic, json.dumps(payload))
        print(f"[->] Comando inviato a {target_device}: {command_type} -> {value}")

    def update_scheduler(self, enabled, new_time):
        """Aggiorna orario e stato dello scheduler ricevuto dall'app mobile"""
        self.scheduler_enabled = enabled
        if enabled:
            # Cancella il vecchio scheduler e ripianifica con il nuovo orario
            schedule.clear('daily_check')
            self.hour_check = new_time
            schedule.every().day.at(self.hour_check).do(self.trigger_global_check).tag('daily_check')
            print(f"[*] Scheduler aggiornato: attivo alle {self.hour_check}")
        else:
            schedule.clear('daily_check')
            print(f"[*] Scheduler disattivato dall'app mobile")

    def run_scheduler_loop(self): 
        """Il loop dello scheduler eseguito nel thread"""
        # Cambia l'orario qui per i tuoi test
        self.hour_check = "11:10:00"
        schedule.every().day.at(self.hour_check).do(self.trigger_global_check).tag('daily_check')
        # Scheduler del refill
        schedule.every().sunday.at("21:00").do(self.trigger_global_refill)
        # Scheduler del reset dello status del DigitalTwin
        schedule.every().sunday.at("00:00").do(self.trigger_reset)
        schedule.every().day.at("11:21").do(self.trigger_global_refill)


        while True:
            schedule.run_pending()  #controllo i vari schedule
            time.sleep(1)           #faccio controllare ogni schedule ogni secondo

    def trigger_global_check(self):
        """Metodo per cìfar partire il controllo della pillola giornaliera
           su tutte le schede"""
        if not self.scheduler_enabled:
            print("[*] Scheduler disabilitato, check saltato.")
            return
        print("[!] Avvio primo controllo globale...")
        self.day_of_week = time.strftime("%A").lower()
        day_code = PillCommands.get_day_code(self.day_of_week)

        if self.week[self.day_of_week]:
            print(f"[OK] Pillola di {self.day_of_week} già risultante come PRESA. Salto il check.")
            return

        print(f"[!] Avvio controllo per: {self.day_of_week}")
        day_code = PillCommands.get_day_code(self.day_of_week)
        self.send_command(target_device="all", command_type=PillCommands.DAILY_CHECK, value=day_code, feed=PillCommands.FEEDS.COMMANDS)

        # Dopo 600 secondi (10 min), esegui il secondo controllo (Last Chance)
        threading.Timer(10, self.last_chance_check).start()

    def last_chance_check(self):
        """Secondo tentativo prima dell'allarme"""
        day_code = PillCommands.get_day_code(self.day_of_week)

        # Se nel frattempo la pillola è stata presa (tramite risposte precedenti), fermati
        if self.week[self.day_of_week]:
            print("[OK] Pillola rilevata prima del secondo controllo. Nessuna azione necessaria.")
            return
        print("[?] Pillola non ancora presa. Invio secondo controllo di verifica...")
        
        # Interroga di nuovo le schede
        self.send_command(target_device="all", command_type=PillCommands.DAILY_CHECK, value=day_code, feed=PillCommands.FEEDS.COMMANDS)

        # Aspetta altri 100 secondi, poi verifica definitivamente per l'allarme
        threading.Timer(10, self.final_alarm_verification).start()

    def final_alarm_verification(self):
        """Verifica finale: se ancora False, l'allarme suona davvero"""
        if not self.week[self.day_of_week]:
            print("[!!!] ATTENZIONE: Controllo fallito anche al secondo tentativo. Invio allarmi.")
            target = self.find_nearest_device()
            self.send_command(target_device=target, command_type=PillCommands.ALARM_ON, value="ON", feed=PillCommands.FEEDS.BUZZER)
            #interrompo gli allarmi dopo 10 secondi per semplicità
            threading.Timer(10,self.send_command,args=(d["name"], PillCommands.ALARM_OFF, "OFF", PillCommands.FEEDS.BUZZER)).start()
        else:
            print("[OK] Pillola rilevata al secondo controllo. Allarme annullato.")

    def trigger_global_refill(self):
        print("[!] Invio comando di refill globale...")
        self.day_of_week = time.strftime("%A").lower()
        day_code = PillCommands.get_day_code(self.day_of_week)
        

        # Invia il primo comando REF
        self.send_command(target_device="all", command_type=PillCommands.REQ_STATUS, value=day_code, feed=PillCommands.FEEDS.COMMANDS)

        # Dopo 600 secondi (10 min), esegui il secondo controllo (Last Chance)
        threading.Timer(100, self.refill_verification).start()

    def refill_verification(self):
        for d in self.devices.values():
            full = all(status == False for status in d["week"].values())

            if not full:
                giorni_vuoti = [day for day, s in d["week"].items() if s == True]
                print(f"[!!!] Errore Refill: i vani di {giorni_vuoti} risultano ancora vuoti nella scheda {d["name"]}!")
                self.send_command(target_device=d["name"], command_type=PillCommands.ALARM_ON, value="ON", feed=PillCommands.FEEDS.BUZZER)
                #interrompo gli allarmi dopo 10 secondi per semplicità
                threading.Timer(10,self.send_command,args=(d["name"], PillCommands.ALARM_OFF, "OFF", PillCommands.FEEDS.BUZZER)).start()
            else:
                print(f"[OK] Refill avvenuto con successo per la scheda {d["name"]}.")

    def trigger_reset(self):
        """Resetto lo stato del digitalTwin"""
        self.week = {day: False for day in self.week}
        for d in self.devices:
            d["week"] = {day: False for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]}
        self.publish_state()

    def start(self):
        daemon_sched = threading.Thread(target=self.run_scheduler_loop, daemon=True)# Avvio lo scheduler in un thread dedicato
        daemon_sched.start()
        self.day_of_week = time.strftime("%A").lower()
        print(f"[*] Thread Scheduler avviato. Ora sistema: {time.strftime('%H:%M:%S')}, Giorno: {self.day_of_week}")

        """Avvia il loop di comunicazione"""
        self.client.connect(self.broker, self.port)
        self.client.loop_forever()                  #gestisce automaticamente la connessione in caso di cadute, è bloccante fino alla disconessione


        

# def main():
#     try:
#         ADAFRUIT_USER = os.environ.get("ADA_USERNAME")
#         ADAFRUIT_KEY = os.environ.get("ADA_KEY")
#         sys.stdout = open("../twin.log", "a", buffering=1)  # line-buffered
#         sys.stderr = open("../twin.err", "a", buffering=1)
#         twin = PillBoxDigitalTwin(ADAFRUIT_USER, ADAFRUIT_KEY)
#         twin.start()
#     except KeyboardInterrupt:
#         print("Arresto del digital twin...")

# if __name__ == "__main__":
#     main()


#--- MAIN VECCHIO ---
if __name__ == "__main__":
    # Inserisci qui le tue credenziali Adafruit
    ADAFRUIT_USER = "321758"
    ADAFRUIT_KEY = "aio_FPwq82dt3DfLKbJ9Ipohk6RxoKfF"
    twin = PillBoxDigitalTwin(ADAFRUIT_USER, ADAFRUIT_KEY)
    twin.start()


    #t3DfLKbJ9Ipohk6RxoKfF
    #questa non è la chiave
    #aio_FPwq82d
    