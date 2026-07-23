// LPT → Serial мост для Arduino Nano Every.
// Полностью заменяет принтер OKI ML280 на аппаратном уровне.
// Вся синхронизация (STROBE/BUSY/ACK) — через настоящее аппаратное
// прерывание процессора, без участия Raspberry Pi.
//
// Raspberry Pi просто читает готовый поток байт по Serial (USB).

#define STROBE_PIN  2     // вход  — от машины
#define BUSY_PIN    3     // выход — к машине
#define ACK_PIN     4     // выход — к машине /ACK
const uint8_t DATA_PINS[8] = {5, 6, 7, 8, 9, 10, 11, 12};

#define ACK_PULSE_US  5

// Кольцевой буфер приёма — на случай, если Pi/USB на миг подвиснет,
// а машина продолжает слать данные
#define RING_SIZE 512
volatile uint8_t ring[RING_SIZE];
volatile uint16_t ringHead = 0;   // пишет ISR
volatile uint16_t ringTail = 0;   // читает loop()

void setup() {
  Serial.begin(115200);

  pinMode(STROBE_PIN, INPUT_PULLUP);
  pinMode(BUSY_PIN, OUTPUT);
  pinMode(ACK_PIN, OUTPUT);
  for (uint8_t i = 0; i < 8; i++) pinMode(DATA_PINS[i], INPUT);

  digitalWrite(BUSY_PIN, LOW);
  digitalWrite(ACK_PIN, HIGH);   // /ACK неактивен (активный LOW)

  attachInterrupt(digitalPinToInterrupt(STROBE_PIN), onStrobe, FALLING);
}

void onStrobe() {
  digitalWrite(BUSY_PIN, HIGH);

  uint8_t b = 0;
  for (uint8_t i = 0; i < 8; i++)
    b |= (digitalRead(DATA_PINS[i]) << i);

  digitalWrite(BUSY_PIN, LOW);

  // Подтверждение приёма для машины
  digitalWrite(ACK_PIN, LOW);
  delayMicroseconds(ACK_PULSE_US);
  digitalWrite(ACK_PIN, HIGH);

  // В кольцевой буфер (быстро, без Serial внутри прерывания)
  uint16_t next = (ringHead + 1) % RING_SIZE;
  if (next != ringTail) {           // есть место — пишем
    ring[ringHead] = b;
    ringHead = next;
  }
  // если буфер полон — байт теряется здесь, но это уже совсем
  // экстремальный случай, при 512 байтах запаса маловероятный
}

void loop() {
  // Переливаем из кольцевого буфера в Serial спокойно, без спешки
  while (ringTail != ringHead) {
    uint8_t b = ring[ringTail];
    ringTail = (ringTail + 1) % RING_SIZE;
    Serial.write(b);
  }
}
