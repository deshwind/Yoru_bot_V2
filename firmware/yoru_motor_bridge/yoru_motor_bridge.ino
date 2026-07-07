/* Yoru V2 motor bridge — Arduino Nano Every (ATmega4809).
 *
 * Port of ROSArduinoBridge (Goebel/Nugen, BSD) for the Nano Every: the
 * original counts encoders with ATmega328 PCINT register ISRs, which do
 * not exist on the megaAVR core, so this port uses attachInterrupt()
 * (supported on every digital pin of the Nano Every) with the same
 * 4x quadrature decode table. Protocol and pin map are unchanged, so it
 * stays compatible with diffdrive_arduino-style hosts.
 *
 * Wiring (identical to the standard ROSArduinoBridge L298N map):
 *   D10 -> L298N IN1  left  forward  (PWM)
 *   D6  -> L298N IN2  left  backward (PWM)
 *   D9  -> L298N IN3  right forward  (PWM)
 *   D5  -> L298N IN4  right backward (PWM)
 *   D13 -> ENA (left enable, held HIGH)   — or keep the jumper cap on
 *   D12 -> ENB (right enable, held HIGH)  — or keep the jumper cap on
 *   D2  <- left  encoder A     D3  <- left  encoder B
 *   A4  <- right encoder A     A5  <- right encoder B
 *   GND shared: Nano, L298N, encoder grounds.
 *
 * Serial protocol, 57600 baud, commands end with CR (LF also accepted):
 *   b            -> report baud rate
 *   e            -> "left right" encoder counts
 *   r            -> reset encoder counts
 *   m <l> <r>    -> closed-loop speed, encoder counts per PID frame (30 Hz)
 *   o <l> <r>    -> raw PWM -255..255 (disables PID)
 *   u kp:kd:ki:ko -> update PID gains
 * Motors auto-stop if no m/o command arrives for 2 s.
 */

#define BAUDRATE 57600
#define MAX_PWM 255
#define PID_RATE 30  // Hz
#define AUTO_STOP_INTERVAL 2000  // ms

// L298N pins (PWM on the IN pins; ENA/ENB just held HIGH)
#define LEFT_MOTOR_FORWARD 10
#define LEFT_MOTOR_BACKWARD 6
#define RIGHT_MOTOR_FORWARD 9
#define RIGHT_MOTOR_BACKWARD 5
#define LEFT_MOTOR_ENABLE 13
#define RIGHT_MOTOR_ENABLE 12

#define LEFT_ENC_PIN_A 2
#define LEFT_ENC_PIN_B 3
#define RIGHT_ENC_PIN_A A4
#define RIGHT_ENC_PIN_B A5

#define LEFT 0
#define RIGHT 1

/* ---------------- Encoders (4x quadrature via attachInterrupt) ------- */

volatile long left_enc_pos = 0L;
volatile long right_enc_pos = 0L;
// Index: (prev_state << 2) | state, each state = A<<1 | B
static const int8_t ENC_STATES[] = {0, 1, -1, 0, -1, 0, 0, 1,
                                    1, 0, 0, -1, 0, -1, 1, 0};

// Bench-verified 2026-07-07: positive PWM rolls both wheels forward but
// counted negative (A/B phase order on this chassis), so both ISRs
// subtract to make forward = positive.
void leftEncoderISR() {
  static uint8_t enc_last = 0;
  uint8_t state = (digitalRead(LEFT_ENC_PIN_A) << 1) |
                  digitalRead(LEFT_ENC_PIN_B);
  enc_last = ((enc_last << 2) | state) & 0x0f;
  left_enc_pos -= ENC_STATES[enc_last];
}

void rightEncoderISR() {
  static uint8_t enc_last = 0;
  uint8_t state = (digitalRead(RIGHT_ENC_PIN_A) << 1) |
                  digitalRead(RIGHT_ENC_PIN_B);
  enc_last = ((enc_last << 2) | state) & 0x0f;
  right_enc_pos -= ENC_STATES[enc_last];
}

long readEncoder(int i) {
  long v;
  noInterrupts();
  v = (i == LEFT) ? left_enc_pos : right_enc_pos;
  interrupts();
  return v;
}

void resetEncoders() {
  noInterrupts();
  left_enc_pos = 0L;
  right_enc_pos = 0L;
  interrupts();
}

/* ---------------- L298N motor driver --------------------------------- */

void initMotorController() {
  pinMode(LEFT_MOTOR_FORWARD, OUTPUT);
  pinMode(LEFT_MOTOR_BACKWARD, OUTPUT);
  pinMode(RIGHT_MOTOR_FORWARD, OUTPUT);
  pinMode(RIGHT_MOTOR_BACKWARD, OUTPUT);
  pinMode(LEFT_MOTOR_ENABLE, OUTPUT);
  pinMode(RIGHT_MOTOR_ENABLE, OUTPUT);
  digitalWrite(LEFT_MOTOR_ENABLE, HIGH);
  digitalWrite(RIGHT_MOTOR_ENABLE, HIGH);
}

void setMotorSpeed(int i, int spd) {
  bool reverse = spd < 0;
  if (reverse) spd = -spd;
  if (spd > MAX_PWM) spd = MAX_PWM;
  int fwd = (i == LEFT) ? LEFT_MOTOR_FORWARD : RIGHT_MOTOR_FORWARD;
  int back = (i == LEFT) ? LEFT_MOTOR_BACKWARD : RIGHT_MOTOR_BACKWARD;
  analogWrite(reverse ? back : fwd, spd);
  analogWrite(reverse ? fwd : back, 0);
}

void setMotorSpeeds(int leftSpeed, int rightSpeed) {
  setMotorSpeed(LEFT, leftSpeed);
  setMotorSpeed(RIGHT, rightSpeed);
}

/* ---------------- PID (ticks-per-frame, from ArbotiX) ----------------- */

// Named struct + elaborated-type signature below keep the Arduino
// preprocessor's hoisted prototype for doPID() compilable.
struct SetPointInfo {
  double TargetTicksPerFrame;
  long Encoder;
  long PrevEnc;
  int PrevInput;  // previous input, avoids derivative kick
  int ITerm;
  long output;
};

SetPointInfo leftPID, rightPID;

int Kp = 20;
int Kd = 12;
int Ki = 0;
int Ko = 50;

unsigned char moving = 0;

void resetPID() {
  leftPID.TargetTicksPerFrame = 0.0;
  leftPID.Encoder = readEncoder(LEFT);
  leftPID.PrevEnc = leftPID.Encoder;
  leftPID.output = 0;
  leftPID.PrevInput = 0;
  leftPID.ITerm = 0;

  rightPID.TargetTicksPerFrame = 0.0;
  rightPID.Encoder = readEncoder(RIGHT);
  rightPID.PrevEnc = rightPID.Encoder;
  rightPID.output = 0;
  rightPID.PrevInput = 0;
  rightPID.ITerm = 0;
}

void doPID(struct SetPointInfo *p) {
  long Perror;
  long output;
  int input;

  input = p->Encoder - p->PrevEnc;
  Perror = p->TargetTicksPerFrame - input;

  output = (Kp * Perror - Kd * (input - p->PrevInput) + p->ITerm) / Ko;
  p->PrevEnc = p->Encoder;

  output += p->output;
  if (output >= MAX_PWM)
    output = MAX_PWM;
  else if (output <= -MAX_PWM)
    output = -MAX_PWM;
  else
    p->ITerm += Ki * Perror;  // anti-windup: freeze when saturated

  p->output = output;
  p->PrevInput = input;
}

void updatePID() {
  leftPID.Encoder = readEncoder(LEFT);
  rightPID.Encoder = readEncoder(RIGHT);

  if (!moving) {
    if (leftPID.PrevInput != 0 || rightPID.PrevInput != 0) resetPID();
    return;
  }

  doPID(&rightPID);
  doPID(&leftPID);
  setMotorSpeeds(leftPID.output, rightPID.output);
}

/* ---------------- Serial command handling ----------------------------- */

const int PID_INTERVAL = 1000 / PID_RATE;
unsigned long nextPID = PID_INTERVAL;
long lastMotorCommand = AUTO_STOP_INTERVAL;

int arg = 0;
int idx = 0;
char cmd;
char argv1[16];
char argv2[16];
long arg1;
long arg2;

void resetCommand() {
  cmd = 0;
  memset(argv1, 0, sizeof(argv1));
  memset(argv2, 0, sizeof(argv2));
  arg1 = 0;
  arg2 = 0;
  arg = 0;
  idx = 0;
}

void runCommand() {
  int i = 0;
  char *p = argv1;
  char *str;
  int pid_args[4];
  arg1 = atol(argv1);
  arg2 = atol(argv2);

  switch (cmd) {
    case 'b':
      Serial.println(BAUDRATE);
      break;
    case 'e':
      Serial.print(readEncoder(LEFT));
      Serial.print(" ");
      Serial.println(readEncoder(RIGHT));
      break;
    case 'r':
      resetEncoders();
      resetPID();
      Serial.println("OK");
      break;
    case 'm':
      lastMotorCommand = millis();
      if (arg1 == 0 && arg2 == 0) {
        setMotorSpeeds(0, 0);
        resetPID();
        moving = 0;
      } else {
        moving = 1;
      }
      leftPID.TargetTicksPerFrame = arg1;
      rightPID.TargetTicksPerFrame = arg2;
      Serial.println("OK");
      break;
    case 'o':
      lastMotorCommand = millis();
      resetPID();
      moving = 0;  // raw PWM: PID off
      setMotorSpeeds(arg1, arg2);
      Serial.println("OK");
      break;
    case 'u':
      while ((str = strtok_r(p, ":", &p)) != NULL && i < 4) {
        pid_args[i] = atoi(str);
        i++;
      }
      if (i == 4) {
        Kp = pid_args[0];
        Kd = pid_args[1];
        Ki = pid_args[2];
        Ko = pid_args[3];
      }
      Serial.println("OK");
      break;
    default:
      Serial.println("Invalid Command");
      break;
  }
}

void setup() {
  Serial.begin(BAUDRATE);

  pinMode(LEFT_ENC_PIN_A, INPUT_PULLUP);
  pinMode(LEFT_ENC_PIN_B, INPUT_PULLUP);
  pinMode(RIGHT_ENC_PIN_A, INPUT_PULLUP);
  pinMode(RIGHT_ENC_PIN_B, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(LEFT_ENC_PIN_A), leftEncoderISR, CHANGE);
  attachInterrupt(digitalPinToInterrupt(LEFT_ENC_PIN_B), leftEncoderISR, CHANGE);
  attachInterrupt(digitalPinToInterrupt(RIGHT_ENC_PIN_A), rightEncoderISR, CHANGE);
  attachInterrupt(digitalPinToInterrupt(RIGHT_ENC_PIN_B), rightEncoderISR, CHANGE);

  initMotorController();
  resetPID();
}

void loop() {
  while (Serial.available() > 0) {
    char chr = Serial.read();

    if (chr == '\r' || chr == '\n') {
      if (arg == 1)
        argv1[idx] = 0;
      else if (arg == 2)
        argv2[idx] = 0;
      if (cmd != 0) runCommand();
      resetCommand();
    } else if (chr == ' ') {
      if (arg == 0) {
        arg = 1;
      } else if (arg == 1) {
        argv1[idx] = 0;
        arg = 2;
        idx = 0;
      }
    } else {
      if (arg == 0) {
        cmd = chr;
      } else if (arg == 1 && idx < (int)sizeof(argv1) - 1) {
        argv1[idx++] = chr;
      } else if (arg == 2 && idx < (int)sizeof(argv2) - 1) {
        argv2[idx++] = chr;
      }
    }
  }

  unsigned long now = millis();
  if (now > nextPID) {
    updatePID();
    nextPID += PID_INTERVAL;
  }
  if ((now - lastMotorCommand) > AUTO_STOP_INTERVAL) {
    setMotorSpeeds(0, 0);
    moving = 0;
  }
}
