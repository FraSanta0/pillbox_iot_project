#include <ArduinoJson.h>
#include "stepper_motor.h"
#define MIN_LED_ON_TIME 2000
#define DAYS 7
#define MAGNETIC_SENSOR 4
#define RED 5
#define GREEN 6
#define BLUE 9
#define BUZZER 3

StepperMotor carousel_motor(7, 12, 8, 13);

struct JSONMessage {
    String target;
    String command;
    int val = -1;
    String sender;
    String timestamp;
};

enum State { WAITING_MESSAGE, PROCESS_MESSAGE, PILL_CHECK, STATUS_CHECK, REQ_RESPONSE, REQ_STATUS_RESPONSE };
enum CommandType { REQ, REQ_STATUS, ALM_ON, ALM_OFF, UNKNOWN };

char carouselState[DAYS + 1] = {'0', '0', '0', '0', '0', '0', '0', '\n'};
State current_state;
const int led = 2;
bool isTaken = false;
StaticJsonDocument<256> doc;
unsigned long ledTimer = 0;
bool ledForcedOn = false;

void setMotor(StepperMotor *st);
void sendError(int code, const char *msg);
bool getMessage(JSONMessage *fields);
void checkPill(const int lightSource, bool *isTaken);
void setMotor(StepperMotor *st, struct JSONMessage *fields);
void resetMotor(StepperMotor *st, struct JSONMessage *fields);
CommandType parseCommand(const String &command);
void getSystemState(char *vector, size_t size);
void sendREQ(bool isTaken);
void sendREQ_STATE(char *vector, size_t size);
void activateAlarm(int val);
void turnOnLed();
void turnOffLed();

void setup() {
  Serial.begin(9600);
  carousel_motor.setSpeed(10);
  pinMode(led, OUTPUT);
  digitalWrite(led, LOW);
  current_state = WAITING_MESSAGE;
  setMotor(&carousel_motor);
  Serial.println("connected");
}

void loop() {
  switch (current_state) {

    case WAITING_MESSAGE: {
      struct JSONMessage fields;
      if (getMessage(&fields)) {
        if ((fields.target == "all" || fields.target == "home") && fields.command.length() > 0) {
          switch (parseCommand(fields.command)) {
            case REQ:
              setMotor(&carousel_motor, &fields);
              checkPill(led, &isTaken);
              turnOffLed();
              resetMotor(&carousel_motor, &fields);
              current_state = REQ_RESPONSE;
              break;
            case REQ_STATUS:
              getSystemState(carouselState, DAYS);
              current_state = REQ_STATUS_RESPONSE;
              break;
            case ALM_ON:
              activateAlarm(1);
              current_state = WAITING_MESSAGE;
              break;
            case ALM_OFF:
              activateAlarm(0);
              current_state = WAITING_MESSAGE;
              break;
            default:
              current_state = WAITING_MESSAGE;
              break;
          }
        }
      }
      turnOffLed();
      break;
    }

    case REQ_RESPONSE:
      sendREQ(isTaken);
      current_state = WAITING_MESSAGE;
      turnOffLed();
      break;

    case REQ_STATUS_RESPONSE:
      sendREQ_STATE(carouselState, DAYS + 1);
      current_state = WAITING_MESSAGE;
      turnOffLed();
      break;

    default:
      current_state = WAITING_MESSAGE;
      break;
  }
}

bool getMessage(JSONMessage *fields) {
  if (!Serial.available()) return false;

  doc.clear();
  DeserializationError error = deserializeJson(doc, Serial);

  if (error) {
    //sendError(101, "parsing JSON failed");
    return false;
  }

  fields->target    = doc["target"]    | "";
  fields->command   = doc["command"]   | "";
  fields->val       = doc["val"]       | -1;
  fields->sender    = doc["sender"]    | "";
  fields->timestamp = doc["timestamp"] | "";

  return true;
}

void setMotor(StepperMotor *st) {
  while (digitalRead(MAGNETIC_SENSOR) == HIGH) {
    st->stepForward(1);
  }
  while (digitalRead(MAGNETIC_SENSOR) == LOW) {
    st->stepForward(1);
  }
}

void sendError(int code, const char *message) {
  char error[256];
  sprintf(error, "%d %s", code, message);
  Serial.println(error);
}

void checkPill(const int lightSource, bool *isTaken) {
  long sum = 0;
  size_t count = 0;
  turnOnLed();
  unsigned long startMillis = millis();
  while (millis() - startMillis < MIN_LED_ON_TIME) {
    sum += analogRead(A0);
    count++;
    delay(20);
  }
  *isTaken = (sum / count) > 512;
}

void turnOnLed() {
  if (!ledForcedOn) {
    digitalWrite(led, HIGH);
    ledTimer = millis();
    ledForcedOn = true;
  }
}

void turnOffLed() {
  if (ledForcedOn && millis() - ledTimer >= MIN_LED_ON_TIME) {
    digitalWrite(led, LOW);
    ledForcedOn = false;
  }
}

void setMotor(StepperMotor *st, struct JSONMessage *fields) {
  st->stepForward(st->getStep(fields->val));
}

void resetMotor(StepperMotor *st, struct JSONMessage *fields) {
  st->stepBackward(st->getStep(fields->val));
}

CommandType parseCommand(const String &command) {
  if (command == "REQ")        return REQ;
  if (command == "REQ_STATUS") return REQ_STATUS;
  if (command == "ALM_ON")      return ALM_ON;
  if (command == "ALM_OFF")      return ALM_OFF;
  return UNKNOWN;
}

void getSystemState(char *vector, size_t size) {
  for (size_t i = 0; i < size; i++) {
    carousel_motor.stepForward(carousel_motor.getStep(i));
    checkPill(led, &isTaken);
    turnOffLed();
    vector[i] = isTaken ? '0' : '1';
    carousel_motor.stepBackward(carousel_motor.getStep(i));
  }
}

void sendREQ_STATE(char *vector, size_t size) {
  Serial.write(vector, size);
}

void sendREQ(bool isTaken) {
  Serial.println(isTaken ? "taken" : "not taken");
}

void activateAlarm(int start) {
  if (start) {
    tone(BUZZER, 100);
    analogWrite(RED, 147);
    analogWrite(GREEN, 0);
    analogWrite(BLUE, 211);
  } else {
    noTone(BUZZER);
    analogWrite(RED, 0);
    analogWrite(GREEN, 0);
    analogWrite(BLUE, 0);
  }
}