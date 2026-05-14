#include <avr/wdt.h>
#include <NewPing.h>

#define trigPin 9
#define echoPin 10
#define pressurePin A0

#define MAX_DISTANCE 150

NewPing sonar(trigPin, echoPin, MAX_DISTANCE);

#define DEBUG_MODE 0 // keep zero both before deployement
#define USE_MEDIAN_FILTER 0

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

/* ================= DISTANCE ================= */

float getDistance()
{
  static float lastValid = 0;

  unsigned int uS = sonar.ping();

  // ---- TIMEOUT ----
  if(uS == 0)
  {
    return lastValid;
  }

  float distance = uS * 0.0343 / 2.0;

  // ---- REJECT GARBAGE SPIKES ----
  if(distance < 2 || distance > 200)
  {
    distance = lastValid;
  }

  // ---- SIMPLE SMOOTHING ----
  distance = 0.7 * lastValid + 0.3 * distance;

  lastValid = distance;

  return distance;
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