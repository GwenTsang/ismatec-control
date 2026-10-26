// Incremental, supervised pump tests. Relay contacts verified active-low.
// Power the pump OFF before upload/reset. No motion or contact feedback exists.
#include <Arduino.h>
#include <avr/wdt.h>
#include <string.h>

// Disable a watchdog surviving reset before C runtime initialization.
void disableWatchdogEarly(void) __attribute__((naked, section(".init3"), used));
void disableWatchdogEarly(void) {
  MCUSR = 0;
  wdt_disable();
}

const uint8_t K1_PIN = 2, K2_PIN = 3;
const unsigned long STOP_GUARD_MS = 5000UL;
const unsigned long ARM_WINDOW_MS = 10000UL;
const unsigned long MAX_PULSE_MS = 10000UL;
bool directionB = false, running = false, armed = false;
unsigned long stoppedAt = 0, armedAt = 0, startedAt = 0, pulseMs = 0;
char command[32];
uint8_t length = 0;
bool overflow = false;

void stopPump() {
  // Preserve direction: STOP/timeout must not reverse a coasting rotor.
  digitalWrite(K2_PIN, HIGH);
  if (running) stoppedAt = millis();
  running = false;
  armed = false;
}

void checkDeadlines() {
  unsigned long now = millis();
  if (running && now - startedAt >= pulseMs) {
    stopPump();
    Serial.println(F("AUTO_STOP pulse complete; direction preserved"));
  }
  if (armed && now - armedAt >= ARM_WINDOW_MS) {
    armed = false;
    Serial.println(F("DISARMED arm window expired"));
  }
}

void status() {
  unsigned long elapsed = millis() - stoppedAt;
  Serial.print(F("STATE commanded_run="));
  Serial.print(running ? F("ON") : F("OFF"));
  Serial.print(F(" direction="));
  Serial.print(directionB ? F("B") : F("A"));
  Serial.print(F(" armed="));
  Serial.print(armed ? 1 : 0);
  Serial.print(F(" guard_remaining_ms="));
  Serial.print(elapsed < STOP_GUARD_MS ? STOP_GUARD_MS - elapsed : 0);
  Serial.print(F(" pulse_remaining_ms="));
  Serial.println(running ? pulseMs - (millis() - startedAt) : 0);
}

void reject(const __FlashStringHelper *reason) {
  stopPump();
  Serial.print(F("ERR stopped/disarmed: "));
  Serial.println(reason);
}

void execute() {
  command[length] = '\0';
  checkDeadlines();
  if (!strcmp(command, "STOP") || !strcmp(command, "OFF")) {
    stopPump();
    Serial.println(F("OK STOP; direction preserved"));
  } else if (!strcmp(command, "STATUS")) {
    status();
  } else if (!strcmp(command, "ID")) {
    Serial.println(F("ID ISMATEC_PUMP_TEST_v2 max_pulse_ms=10000 stop_guard_ms=5000 watchdog_ms=1000"));
  } else if (!strcmp(command, "ARM")) {
    if (running || millis() - stoppedAt < STOP_GUARD_MS)
      reject(F("wait stopped at least 5s; operator must confirm no motion"));
    else {
      armed = true;
      armedAt = millis();
      Serial.println(F("OK ARMED one pulse within 10s"));
    }
  } else if (!strcmp(command, "DIR A") || !strcmp(command, "DIR B")) {
    if (running || millis() - stoppedAt < STOP_GUARD_MS) {
      reject(F("direction requires stopped at least 5s AND observed rotor stopped"));
    } else {
      armed = false;
      directionB = command[4] == 'B';
      digitalWrite(K1_PIN, directionB ? LOW : HIGH);
      stoppedAt = millis(); // also enforce 5s observation after changing direction
      Serial.println(F("OK direction set; stopped/disarmed; observe pump before rearming"));
    }
  } else if (!strncmp(command, "PULSE ", 6)) {
    unsigned long duration = 0;
    if (!command[6]) { reject(F("pulse requires 250..10000 ms")); return; }
    for (uint8_t i = 6; command[i]; ++i) {
      if (command[i] < '0' || command[i] > '9' || i >= 11) {
        reject(F("invalid duration")); return;
      }
      duration = duration * 10 + command[i] - '0';
    }
    if (duration < 250 || duration > MAX_PULSE_MS) {
      reject(F("pulse requires 250..10000 ms")); return;
    }
    if (running || !armed || millis() - armedAt >= ARM_WINDOW_MS ||
        millis() - stoppedAt < STOP_GUARD_MS) {
      reject(F("pulse requires fresh ARM after observed stop")); return;
    }
    armed = false; // single-use authorization; no command can extend this pulse
    pulseMs = duration;
    startedAt = millis();
    running = true;
    digitalWrite(K2_PIN, LOW);
    Serial.print(F("OK PULSE ms="));
    Serial.println(pulseMs);
  } else {
    reject(F("unknown command"));
  }
}

void setup() {
  digitalWrite(K2_PIN, HIGH);
  digitalWrite(K1_PIN, HIGH);
  pinMode(K2_PIN, OUTPUT);
  pinMode(K1_PIN, OUTPUT);
  stoppedAt = millis();
  Serial.begin(115200);
  wdt_enable(WDTO_1S);
  Serial.println(F("READY ISMATEC_PUMP_TEST_v2 STOP direction=A DISARMED"));
  status();
}

void loop() {
  wdt_reset();
  checkDeadlines();
  // Bound input work so continuous traffic cannot postpone the stop check.
  for (uint8_t budget = 0; budget < 32 && Serial.available(); ++budget) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (!overflow && length) execute();
      length = 0;
      overflow = false;
    } else if (!overflow) {
      if (length < sizeof(command) - 1) command[length++] = c;
      else {
        overflow = true;
        reject(F("command too long"));
      }
    }
    checkDeadlines();
  }
}
