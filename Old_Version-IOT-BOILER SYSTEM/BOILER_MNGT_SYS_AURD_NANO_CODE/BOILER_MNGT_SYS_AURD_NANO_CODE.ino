#include <avr/wdt.h>

#define trigPin 9
#define echoPin 10
#define pressurePin A0

#define DEBUG_MODE 0

uint16_t seq = 0;

struct TelemetryPacket
{
  uint16_t sequence;
  uint32_t timestamp;
  float distance;
  float pressure;
  uint16_t crc;
};

TelemetryPacket pkt;

/* CRC16 */

uint16_t crc16(uint8_t *data, uint16_t len)
{
  uint16_t crc = 0xFFFF;

  for(uint16_t i=0;i<len;i++)
  {
    crc ^= data[i];

    for(uint8_t j=0;j<8;j++)
    {
      if(crc & 1)
        crc = (crc >> 1) ^ 0xA001;
      else
        crc >>= 1;
    }
  }

  return crc;
}

/* COBS ENCODE */

int cobsEncode(uint8_t *input, int length, uint8_t *output)
{
  int read_index = 0;
  int write_index = 1;
  int code_index = 0;
  uint8_t code = 1;

  while(read_index < length)
  {
    if(input[read_index] == 0)
    {
      output[code_index] = code;
      code = 1;
      code_index = write_index++;
      read_index++;
    }
    else
    {
      output[write_index++] = input[read_index++];
      code++;

      if(code == 0xFF)
      {
        output[code_index] = code;
        code = 1;
        code_index = write_index++;
      }
    }
  }

  output[code_index] = code;

  return write_index;
}

/* ================= DISTANCE (ROBUST FILTER) ================= */

float getDistance()
{
  const int samples = 5;
  float readings[samples];

  // ---- MULTI SAMPLING ----
  for(int i = 0; i < samples; i++)
  {
    digitalWrite(trigPin, LOW);
    delayMicroseconds(5);

    digitalWrite(trigPin, HIGH);
    delayMicroseconds(10);
    digitalWrite(trigPin, LOW);

    long duration = pulseIn(echoPin, HIGH, 30000);

    if(duration == 0)
      readings[i] = -1;
    else
      readings[i] = duration * 0.0343 / 2;

    delay(10);
  }

  // ---- REMOVE INVALID ----
  float valid[samples];
  int count = 0;

  for(int i = 0; i < samples; i++)
  {
    if(readings[i] > 2 && readings[i] < 400) // ignore too close + noise
      valid[count++] = readings[i];
  }

  // ---- HANDLE NO VALID DATA ----
  static float last_valid = -1;

  if(count == 0)
  {
    return last_valid;  // keep last stable value (important)
  }

  // ---- SORT (MEDIAN) ----
  for(int i = 0; i < count-1; i++)
  {
    for(int j = i+1; j < count; j++)
    {
      if(valid[j] < valid[i])
      {
        float temp = valid[i];
        valid[i] = valid[j];
        valid[j] = temp;
      }
    }
  }

  float median = valid[count/2];

  // ---- FILTERING ----
  static float filtered = 0;
  const float alpha = 0.3;   // smoother for cheap sensor
  const float max_jump = 30; // tighter limit

  if(filtered != 0)
  {
    float diff = median - filtered;

    // 🚨 LIMIT JUMP (NOT REJECT)
    if(diff > max_jump) diff = max_jump;
    if(diff < -max_jump) diff = -max_jump;

    median = filtered + diff;
  }

  // ---- EMA ----
  if(filtered == 0)
    filtered = median;
  else
    filtered = alpha * median + (1 - alpha) * filtered;

  last_valid = filtered;

  return filtered;
}

/* ================= PRESSURE ================= */

float getPressure()
{
  int rawValue = analogRead(pressurePin);

  int raw_min = 100;
  int raw_max = 900;
  float pressure_max = 10.0;

  if(rawValue < raw_min) rawValue = raw_min;
  if(rawValue > raw_max) rawValue = raw_max;

  float pressure =
  (float)(rawValue - raw_min) /
  (raw_max - raw_min) *
  pressure_max;

  return pressure;
}

/* ================= SETUP ================= */

void setup()
{
  Serial.begin(115200);

  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);

  wdt_enable(WDTO_2S);

  delay(2000);
}

/* ================= LOOP ================= */

void loop()
{
  wdt_reset();

  pkt.sequence = seq++;
  pkt.timestamp = millis();
  pkt.distance = getDistance();
  pkt.pressure = getPressure();

  pkt.crc = crc16((uint8_t*)&pkt, sizeof(pkt)-2);

#if DEBUG_MODE

  Serial.print("SEQ:");
  Serial.print(pkt.sequence);
  Serial.print(" DIST:");
  Serial.print(pkt.distance);
  Serial.print(" PRES:");
  Serial.println(pkt.pressure);

#else

  uint8_t encoded[32];

  int len = cobsEncode((uint8_t*)&pkt, sizeof(pkt), encoded);

  Serial.write(encoded, len);
  Serial.write(0x00);

#endif

  delay(300);
}
