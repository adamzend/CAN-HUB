// SZKLARNIA / NODE 3 — ESP32-WROOM-32 + MCP2515 8 MHz + TXS0108E
// Rzeczywiste pomiary DS18B20 i AM2302. Bez Wi-Fi i bez lokalnego Modbus TCP.
// Oryginał: node3_szklarnia_aktualny.ino (ESP8266), autor użytkownik.
// Opis połączeń i protokołu: ../szklarnia_CAN_instrukcja.txt

#include <Arduino.h>
#include "src/CanHubProtocol/CanHubProtocol.h"
#include <esp_timer.h>
#include <SPI.h>
#include <Wire.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SH110X.h>
#include <DHTesp.h>
#include <math.h>
#include "src/mcp2515/mcp2515.h"

#if !defined(CONFIG_IDF_TARGET_ESP32)
#error Ten szkic jest przeznaczony dla klasycznego ESP32-WROOM-32.
#endif

// NUMERY GPIO. Nie są to oznaczenia D0/D1... z ESP8266.
const uint8_t PIN_DS18B20 = 14;
const uint8_t PIN_DHT22 = 13;
const uint8_t PIN_OLED_SDA = 21;
const uint8_t PIN_OLED_SCL = 22;
const uint8_t PIN_CAN_SCK = 18;
const uint8_t PIN_CAN_MOSI = 23;
const uint8_t PIN_CAN_MISO = 19;
const uint8_t PIN_CAN_CS = 27;
const uint8_t PIN_CAN_INT = 26;
const uint8_t PIN_TXS_OE = 25;
const uint8_t PIN_LED_ZYCIA = 32;  // Opcjonalna zewnętrzna LED przez rezystor.
const uint32_t CZAS_DIAGNOSTYKI_MS = 30000;
uint32_t startEkranu = 0;
bool ekranPomiarow = false;
bool ekranAnalogowy = false;
uint32_t zmianaStronyOLED = 0;
const uint32_t OKRES_STRONY_OLED_MS = 60000;
bool pomiarGotowy = false;

const uint8_t NODE_ID = 3;
static_assert(CanHub::validNode(NODE_ID), "NODE_ID must be 1..95");
const uint8_t WERSJA_PROTOKOLU = CanHub::VERSION;
// Znacznik tylko wewnętrzny. W CAN wysyłamy 0 + osobną flagę błędu.
const int16_t BRAK_POMIARU = INT16_MIN;
const uint32_t OKRES_POMIAROW_MS = 2500;
const uint32_t OKRES_HEARTBEAT_MS = 1000;
const uint32_t TIMEOUT_HUB_MS = 5000;

OneWire oneWire(PIN_DS18B20);
DallasTemperature sensors(&oneWire);
DHTesp dht;
Adafruit_SH1106G display(128, 64, &Wire, -1);
MCP2515 mcp2515(PIN_CAN_CS, 100000, &SPI); // Sprawdzone SPI 100 kHz dla TXS0108E.

// Zachowane adresy DS18B20 z programu użytkownika.
DeviceAddress sensor1 = {0x28,0x88,0x4D,0x61,0xB1,0x22,0x08,0xCA};
DeviceAddress sensor2 = {0x28,0x2A,0x22,0x62,0xB1,0x22,0x08,0x0E};
DeviceAddress sensor3 = {0x28,0x8E,0x06,0xE7,0xB1,0x22,0x08,0x31};
DeviceAddress sensor4 = {0x28,0xEB,0xCB,0x56,0xB1,0x22,0x07,0x88};

int16_t temp1 = BRAK_POMIARU;
int16_t temp2 = BRAK_POMIARU;
int16_t temp3 = BRAK_POMIARU;
int16_t temp4 = BRAK_POMIARU;
int16_t dhtTemp = BRAK_POMIARU;
int16_t dhtHum = BRAK_POMIARU;
int16_t heart = 0; // Zachowany licznik pomiarów 0..999, dawniej rejestr 1012.
int16_t deviceCount = 0; // Liczba DS18B20 wykrytych podczas startu.
bool oledGotowy = false;
bool canGotowy = false;
bool konwersjaTrwa = false;
bool ledStan = false;
uint8_t numerProbki = 0;
uint8_t heartbeatSequence = 0;
uint8_t hubSequence = 0;
bool hubWidziany = false;
uint32_t ostatniHub = 0;
uint32_t startKonwersji = 0;
uint32_t ostatniPomiar = 0;
uint32_t ostatniHeartbeat = 0;
uint32_t ostatniOLED = 0;
uint32_t ostatniaKontrolaCAN = 0;
uint32_t ostatniaProbaCAN = 0;
uint32_t bledyKolejkiCAN = 0;
uint8_t canEflg = 0;
uint8_t canTEC = 0;
uint8_t canREC = 0;
bool wysylkaTrwa = false;
uint8_t czescDoWyslania = 0;
uint32_t ostatniaCzesc = 0;
uint16_t bledyProbki = 0;

// ADS1115: 0x48 A0..A3 i 0x49 A0..A3. Wyniki w mV, bez kalibracji gleby.
// Sterownik Wire ma limit 30 ms; konwersje sa asynchroniczne.
int16_t gleba1 = BRAK_POMIARU, gleba2 = BRAK_POMIARU, gleba3 = BRAK_POMIARU;
int16_t gleba4 = BRAK_POMIARU, gleba5 = BRAK_POMIARU, gleba6 = BRAK_POMIARU;
int16_t gleba7 = BRAK_POMIARU, gleba8 = BRAK_POMIARU;
// Statusy v2: bit 0 = niepoprawny pomiar; bity 1..4 pozostaja dla HUB-a.
// 5 inicjalizacja, 6 brak odpowiedzi, 7 timeout, 8 CRC, 9 komunikacja,
// 10 format, 11 zakres, 12 przepelnienie, 13 konfiguracja, 14 nasycenie, 15 inne.
uint16_t statusS1=33, statusS2=33, statusS3=33, statusS4=33;
uint16_t statusDhtTemp=33, statusDhtHum=33;
uint16_t statusA1=33, statusA2=33, statusA3=33, statusA4=33;
uint16_t statusA5=33, statusA6=33, statusA7=33, statusA8=33;
uint16_t statusADS=0;
uint8_t adsKrok = 8;
bool adsCzeka = false;
uint32_t adsStart = 0, adsPoll = 0;
uint8_t adsAdres() { return adsKrok < 4 ? 0x48 : 0x49; }
uint8_t adsKanal() { return adsKrok < 4 ? adsKrok : adsKrok - 4; }
bool adsCzytaj(uint8_t reg, uint16_t &value) {
  Wire.beginTransmission(adsAdres()); Wire.write(reg);
  if (Wire.endTransmission(false) != 0) { statusADS=1|(1U<<9); return false; }
  if (Wire.requestFrom(adsAdres(), (uint8_t)2) != 2) { statusADS=1|(1U<<9); return false; }
  value = ((uint16_t)Wire.read() << 8); value |= Wire.read();
  return true;
}
void adsZapisz(int16_t value) {
  if (adsKrok == 0) gleba1 = value;
  if (adsKrok == 1) gleba2 = value;
  if (adsKrok == 2) gleba3 = value;
  if (adsKrok == 3) gleba4 = value;
  if (adsKrok == 4) gleba5 = value;
  if (adsKrok == 5) gleba6 = value;
  if (adsKrok == 6) gleba7 = value;
  if (adsKrok == 7) gleba8 = value;
  if (adsKrok == 0) statusA1 = value == BRAK_POMIARU ? (statusADS ? statusADS : 32769) : 0;
  if (adsKrok == 1) statusA2 = value == BRAK_POMIARU ? (statusADS ? statusADS : 32769) : 0;
  if (adsKrok == 2) statusA3 = value == BRAK_POMIARU ? (statusADS ? statusADS : 32769) : 0;
  if (adsKrok == 3) statusA4 = value == BRAK_POMIARU ? (statusADS ? statusADS : 32769) : 0;
  if (adsKrok == 4) statusA5 = value == BRAK_POMIARU ? (statusADS ? statusADS : 32769) : 0;
  if (adsKrok == 5) statusA6 = value == BRAK_POMIARU ? (statusADS ? statusADS : 32769) : 0;
  if (adsKrok == 6) statusA7 = value == BRAK_POMIARU ? (statusADS ? statusADS : 32769) : 0;
  if (adsKrok == 7) statusA8 = value == BRAK_POMIARU ? (statusADS ? statusADS : 32769) : 0;
  adsKrok++; adsCzeka = false;
}
void obsluzADS(uint32_t teraz) {
  if (adsKrok >= 8) return;
  if (!adsCzeka) {
    statusADS=0;
    // OS=start, single-ended MUX, PGA +/-4.096V (0.125mV/bit),
    // single-shot, 128 SPS, comparator disabled (TI ADS1115 config register).
    uint16_t cfg = 0x8383 | ((uint16_t)(4 + adsKanal()) << 12);
    Wire.beginTransmission(adsAdres()); Wire.write(1);
    Wire.write((uint8_t)(cfg >> 8)); Wire.write((uint8_t)cfg);
    if (Wire.endTransmission() != 0) { statusADS=1|(1U<<9); adsZapisz(BRAK_POMIARU); return; }
    adsStart = teraz; adsPoll = teraz; adsCzeka = true;
    return;
  }
  if (teraz - adsStart > 60) { statusADS=1|(1U<<7); adsZapisz(BRAK_POMIARU); return; }
  if (teraz - adsPoll < 10) return;
  adsPoll = teraz;
  uint16_t cfg = 0, raw = 0;
  if (!adsCzytaj(1, cfg)) { adsZapisz(BRAK_POMIARU); return; }
  uint16_t expected = 0x0383 | ((uint16_t)(4 + adsKanal()) << 12);
  if ((cfg & 0x7FFF) != expected) { statusADS=1|(1U<<13); adsZapisz(BRAK_POMIARU); return; }
  if (!(cfg & 0x8000)) return;
  if (!adsCzytaj(0, raw)) { adsZapisz(BRAK_POMIARU); return; }
  int32_t counts = (int16_t)raw;
  // Drobny ujemny offset przy 0V jest dopuszczalny. Nie wykrywa odlaczonej sondy!
  if (counts == 32767 || counts == -32768) { statusADS=1|(1U<<14); adsZapisz(BRAK_POMIARU); return; }
  if (counts < -80 || counts > 27200) { statusADS=1|(1U<<11); adsZapisz(BRAK_POMIARU); return; }
  adsZapisz(counts <= 0 ? 0 : (counts + 4) / 8);
}

int16_t temperaturaNaInt(float value) {
  if (!isfinite(value) || value == DEVICE_DISCONNECTED_C || value < -55 || value > 125) return BRAK_POMIARU;
  return (int16_t)lroundf(value * 10.0f);
}

uint16_t flagiBledow() {
  uint16_t flags = 0;
  if (temp1 == BRAK_POMIARU) flags |= (1U << 0);
  if (temp2 == BRAK_POMIARU) flags |= (1U << 1);
  if (temp3 == BRAK_POMIARU) flags |= (1U << 2);
  if (temp4 == BRAK_POMIARU) flags |= (1U << 3);
  if (dhtTemp == BRAK_POMIARU) flags |= (1U << 4);
  if (dhtHum == BRAK_POMIARU) flags |= (1U << 5);
  // Bit 6 po usunietym oswietleniu: ADS nr 2 A3 (Analog 8).
  if (gleba8 == BRAK_POMIARU) flags |= (1U << 6);
  if (!oledGotowy) flags |= (1U << 7);
  if (!canGotowy || canEflg != 0 || canTEC != 0 || canREC != 0) flags |= (1U << 8);
  if (gleba1 == BRAK_POMIARU) flags |= (1U << 9);
  if (gleba2 == BRAK_POMIARU) flags |= (1U << 10);
  if (gleba3 == BRAK_POMIARU) flags |= (1U << 11);
  if (gleba4 == BRAK_POMIARU) flags |= (1U << 12);
  if (gleba5 == BRAK_POMIARU) flags |= (1U << 13);
  if (gleba6 == BRAK_POMIARU) flags |= (1U << 14);
  if (gleba7 == BRAK_POMIARU) flags |= (1U << 15);
  return flags;
}

void zapiszInt16(uint8_t *data, uint8_t offset, int16_t value) {
  uint16_t bits = (uint16_t)value;
  data[offset] = bits & 0xFF;
  data[offset + 1] = bits >> 8;
}

bool nadaj(const can_frame &frame) {
  if (!canGotowy) return false;
  MCP2515::ERROR result = mcp2515.sendMessage(&frame);
  if (result != MCP2515::ERROR_OK) {
    bledyKolejkiCAN++;
    return false;
  }
  // To potwierdza przyjęcie do bufora MCP, a NIE odbiór przez HUB.
  return true;
}

bool uruchomCAN() {
  if (mcp2515.reset() != MCP2515::ERROR_OK) return false;
  if (mcp2515.setBitrate(CAN_125KBPS, MCP_8MHZ) != MCP2515::ERROR_OK) return false;
  // Odbieramy tylko heartbeat HUB-a 0x700 i polecenie diagnostyczne 0x603.
  if (mcp2515.setFilterMask(MCP2515::MASK0, false, 0x7FF) != MCP2515::ERROR_OK) return false;
  if (mcp2515.setFilterMask(MCP2515::MASK1, false, 0x7FF) != MCP2515::ERROR_OK) return false;
  mcp2515.setFilter(MCP2515::RXF0, false, 0x700);
  mcp2515.setFilter(MCP2515::RXF1, false, 0x600 + NODE_ID);
  mcp2515.setFilter(MCP2515::RXF2, false, 0x700);
  mcp2515.setFilter(MCP2515::RXF3, false, 0x600 + NODE_ID);
  mcp2515.setFilter(MCP2515::RXF4, false, 0x700);
  mcp2515.setFilter(MCP2515::RXF5, false, 0x600 + NODE_ID);
  // One-shot: brak odbiornika nie powoduje ciągłego ponawiania starej ramki.
  // Nowe pomiary i heartbeat są nadawane cyklicznie przez nasz program.
  if (mcp2515.setNormalOneShotMode() != MCP2515::ERROR_OK) return false;
  canEflg = 0;
  canTEC = 0;
  canREC = 0;
  return true;
}

// Data and status are built by the same library used by new nodes.
void nadajTrzyWartosci(uint16_t id, int16_t a, int16_t b, int16_t c) {
  int16_t values[3]={a==BRAK_POMIARU?int16_t(0):a,b==BRAK_POMIARU?int16_t(0):b,c==BRAK_POMIARU?int16_t(0):c};
  uint16_t statuses[3]={0,0,0}; CanHub::Frame data,status;
  CanHub::pair(NODE_ID,(id-NODE_ID*16)/2,numerProbki,values,statuses,3,data,status);
  can_frame frame={};frame.can_id=data.id;frame.can_dlc=8;memcpy(frame.data,data.data,8);nadaj(frame);
}
void nadajStatusy(uint16_t id, uint16_t a, uint16_t b, uint16_t c) {
  int16_t values[3]={0,0,0};uint16_t statuses[3]={a,b,c};CanHub::Frame data,status;
  CanHub::pair(NODE_ID,(id-NODE_ID*16)/2,numerProbki,values,statuses,3,data,status);
  can_frame frame={};frame.can_id=status.id;frame.can_dlc=8;memcpy(frame.data,status.data,8);nadaj(frame);
}

void obsluzWysylkePomiarow(uint32_t teraz) {
  if (!wysylkaTrwa || teraz - ostatniaCzesc < 20) return;
  ostatniaCzesc = teraz;
  // Pozycje 1..14: grupy 0..4. Metadane techniczne: grupa 7 (pozycje 22,23).
  if (czescDoWyslania == 0) nadajTrzyWartosci(CanHub::dataId(NODE_ID,0),temp1,temp2,temp3);
  if (czescDoWyslania == 1) nadajStatusy(CanHub::dataId(NODE_ID,0),statusS1,statusS2,statusS3);
  if (czescDoWyslania == 2) nadajTrzyWartosci(CanHub::dataId(NODE_ID,1),temp4,dhtTemp,dhtHum);
  if (czescDoWyslania == 3) nadajStatusy(CanHub::dataId(NODE_ID,1),statusS4,statusDhtTemp,statusDhtHum);
  if (czescDoWyslania == 4) nadajTrzyWartosci(CanHub::dataId(NODE_ID,7),heart,deviceCount,0);
  if (czescDoWyslania == 5) nadajStatusy(CanHub::dataId(NODE_ID,7),0,0,0);
  if (czescDoWyslania == 6) nadajTrzyWartosci(CanHub::dataId(NODE_ID,2),gleba1,gleba2,gleba3);
  if (czescDoWyslania == 7) nadajStatusy(CanHub::dataId(NODE_ID,2),statusA1,statusA2,statusA3);
  if (czescDoWyslania == 8) nadajTrzyWartosci(CanHub::dataId(NODE_ID,3),gleba4,gleba5,gleba6);
  if (czescDoWyslania == 9) nadajStatusy(CanHub::dataId(NODE_ID,3),statusA4,statusA5,statusA6);
  if (czescDoWyslania == 10) nadajTrzyWartosci(CanHub::dataId(NODE_ID,4),gleba7,gleba8,0);
  if (czescDoWyslania == 11) nadajStatusy(CanHub::dataId(NODE_ID,4),statusA7,statusA8,0);
  czescDoWyslania++;
  if (czescDoWyslania >= 12) wysylkaTrwa = false;
}

void nadajHeartbeat() {
  CanHub::Frame hb=CanHub::heartbeat(NODE_ID,++heartbeatSequence,millis()/1000,flagiBledow());
  can_frame frame={};frame.can_id=hb.id;frame.can_dlc=8;memcpy(frame.data,hb.data,8);nadaj(frame);
}

void odbierzJednaRamke() {
  if (!canGotowy) return;
  can_frame frame = {};
  if (mcp2515.readMessage(&frame) != MCP2515::ERROR_OK) return;
  if ((frame.can_id & (CAN_EFF_FLAG | CAN_RTR_FLAG | CAN_ERR_FLAG)) != 0) return;
  if (frame.can_dlc != 8 || (frame.data[0] != WERSJA_PROTOKOLU && !(frame.can_id==0x700 && frame.data[0]==1))) return;
  if (frame.can_id == 0x700) {
    if (!hubWidziany || frame.data[1] != hubSequence) {
      hubSequence = frame.data[1];
      ostatniHub = millis();
      hubWidziany = true;
    }
  }
  // Jedyne polecenie tej wersji: diagnostyczne PING (kod 1).
  // Nie sterujemy żadnym przekaźnikiem ani procesem.
  if (frame.can_id == 0x600 + NODE_ID && frame.data[2] == 1) {
    frame.can_id = 0x580 + NODE_ID;
    frame.data[3] = 0; // status odpowiedzi: OK
    nadaj(frame);
  }
}

int16_t odczytajDS(const uint8_t *adres, uint16_t &status) {
  uint8_t dane[9] = {};
  status = 0;
  if (!sensors.readScratchPad(adres,dane)) { status=1|(1U<<6); return BRAK_POMIARU; }
  bool sameZera = !(dane[0]|dane[1]|dane[2]|dane[3]|dane[4]|dane[5]|dane[6]|dane[7]|dane[8]);
  bool sameFF = (dane[0]&dane[1]&dane[2]&dane[3]&dane[4]&dane[5]&dane[6]&dane[7]&dane[8])==255;
  if (sameZera || sameFF) { status=1|(1U<<6); return BRAK_POMIARU; }
  if (OneWire::crc8(dane,8)!=dane[8]) { status=1|(1U<<8); return BRAK_POMIARU; }
  int16_t raw=(int16_t)(((uint16_t)dane[1]<<8)|dane[0]);
  uint8_t rozdzielczosc=dane[4]&0x60;
  if (rozdzielczosc==0) raw &= ~7;
  if (rozdzielczosc==0x20) raw &= ~3;
  if (rozdzielczosc==0x40) raw &= ~1;
  float temp=raw/16.0f;
  if (temp < -55 || temp > 125) { status=1|(1U<<11); return BRAK_POMIARU; }
  return (int16_t)lroundf(temp*10.0f);
}

void zakonczPomiar() {
  temp1 = odczytajDS(sensor1,statusS1);
  temp2 = odczytajDS(sensor2,statusS2);
  temp3 = odczytajDS(sensor3,statusS3);
  temp4 = odczytajDS(sensor4,statusS4);
  // AM2302 w obudowie: rzeczywisty odczyt temperatury i wilgotnosci.
  TempAndHumidity dhtPomiar = dht.getTempAndHumidity();
  dhtTemp = BRAK_POMIARU;
  dhtHum = BRAK_POMIARU;
  statusDhtTemp=0; statusDhtHum=0;
  if (dht.getStatus() != DHTesp::ERROR_NONE) {
    uint16_t powod=1|(1U<<9);
    if (dht.getStatus()==DHTesp::ERROR_TIMEOUT) powod=1|(1U<<7);
    if (dht.getStatus()==DHTesp::ERROR_CHECKSUM) powod=1|(1U<<8);
    statusDhtTemp=powod; statusDhtHum=powod;
  } else {
    if (!isfinite(dhtPomiar.temperature)) statusDhtTemp=1|(1U<<10);
    else if (dhtPomiar.temperature < -40 || dhtPomiar.temperature > 80) statusDhtTemp=1|(1U<<11);
    else dhtTemp=(int16_t)lroundf(dhtPomiar.temperature*10.0f);
    if (!isfinite(dhtPomiar.humidity)) statusDhtHum=1|(1U<<10);
    else if (dhtPomiar.humidity < 0 || dhtPomiar.humidity > 100) statusDhtHum=1|(1U<<11);
    else dhtHum=(int16_t)lroundf(dhtPomiar.humidity*10.0f);
  }
  // Cykl ADS musi byc zakonczony przed zamrozeniem maski i wysylka probki.
  if (adsKrok < 8) return;
  pomiarGotowy = true;
  heart++;
  if (heart >= 1000) heart = 0;
  numerProbki++;
  bledyProbki = flagiBledow();
  wysylkaTrwa = true;
  czescDoWyslania = 0;
  konwersjaTrwa = false;
  Serial.printf("Probka=%u T1=%d T2=%d T3=%d T4=%d DHT=%d HUM=%d ERR=0x%04X\n",
                numerProbki,temp1,temp2,temp3,temp4,dhtTemp,dhtHum,bledyProbki);
  Serial.printf("ADS mV: %d %d %d %d %d %d %d %d\n", gleba1,gleba2,gleba3,gleba4,gleba5,gleba6,gleba7,gleba8);
}

void pokazPomiar(int16_t value) {
  if (value == BRAK_POMIARU) display.print("ERR");
  else display.print(value / 10.0f, 1);
}

void pokazStanCzujnika(const char *nazwa, int16_t value) {
  display.print(nazwa);
  if (!pomiarGotowy) display.print(" WAIT");
  else display.print(value == BRAK_POMIARU ? " ERR" : " OK");
}

void pokazAnalog(const char *nazwa, int16_t value) {
  display.print(nazwa);
  if (!pomiarGotowy) display.println(" WAIT");
  else if (value == BRAK_POMIARU) display.println(" ERR");
  else { display.print(value); display.println(" mV"); }
}

void pokazAnalogKolumna(uint8_t x, uint8_t y, const char *nazwa, int16_t value) {
  display.setCursor(x, y); display.print(nazwa);
  if (!pomiarGotowy) display.print("WAIT");
  else if (value == BRAK_POMIARU) display.print("ERR");
  else display.print(value);
}

void odswiezOLED(uint32_t teraz) {
  if (!oledGotowy) return;
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextWrap(false);
  display.setTextColor(SH110X_WHITE);
  display.setCursor(0, 0);
  if (!ekranPomiarow) {
    display.print("DIAGNOSTYKA ");
    display.print((CZAS_DIAGNOSTYKI_MS - (teraz - startEkranu) + 999) / 1000);
    display.println("s");
    pokazStanCzujnika("S1", temp1); display.print("  "); pokazStanCzujnika("S2", temp2); display.println();
    pokazStanCzujnika("S3", temp3); display.print("  "); pokazStanCzujnika("S4", temp4); display.println();
    pokazStanCzujnika("DHT T", dhtTemp); display.println();
    pokazStanCzujnika("DHT H", dhtHum); display.println();
    display.print("DS wykryte: "); display.println(deviceCount);
    display.print("MCP: "); display.println(canGotowy ? "OK" : "ERR");
    display.print("ADS:");
    display.print(gleba1 != BRAK_POMIARU && gleba2 != BRAK_POMIARU && gleba3 != BRAK_POMIARU && gleba4 != BRAK_POMIARU ? "48 OK " : "48 ERR ");
    display.print(gleba5 != BRAK_POMIARU && gleba6 != BRAK_POMIARU && gleba7 != BRAK_POMIARU && gleba8 != BRAK_POMIARU ? "49 OK" : "49 ERR");
  } else if (ekranAnalogowy) {
    display.println("GLEBA / ADS1115   2/2");
    // Dwie kolumny, osiem wejsc. Jednostka mV we wspolnym naglowku.
    display.println("       mV");
    pokazAnalogKolumna(0, 16, "A1:", gleba1); pokazAnalogKolumna(64, 16, "A5:", gleba5);
    pokazAnalogKolumna(0, 24, "A2:", gleba2); pokazAnalogKolumna(64, 24, "A6:", gleba6);
    pokazAnalogKolumna(0, 32, "A3:", gleba3); pokazAnalogKolumna(64, 32, "A7:", gleba7);
    pokazAnalogKolumna(0, 40, "A4:", gleba4); pokazAnalogKolumna(64, 40, "A8:", gleba8);
    display.setCursor(0, 56);
    display.print("MCP:"); display.print(canGotowy ? "OK" : "ERR");
    display.print(" Zmiana:"); display.print((OKRES_STRONY_OLED_MS - (teraz-zmianaStronyOLED)+999)/1000);
    display.print("s");
  } else {
    display.println("SZKLARNIA ID:3   1/2");
    display.print("MCP:"); display.print(canGotowy ? "OK" : "ERR");
    display.print(" DS:"); display.print(deviceCount); display.println("/4");
    // Kolejnosc taka jak w ESP8266: T1=S2, T2=S4, T3=S1, T4=S3.
    display.print("T1:"); pokazPomiar(temp2); display.print(" T2:"); pokazPomiar(temp4); display.println();
    display.print("T3:"); pokazPomiar(temp1); display.print(" T4:"); pokazPomiar(temp3); display.println();
    display.print("Dht:"); pokazPomiar(dhtTemp); display.print(" H:"); pokazPomiar(dhtHum); display.println();
    // Czas pracy jest niezalezny od liczby pomiarow i aktualizuje sie co sekunde.
    uint64_t sekundyPracy = esp_timer_get_time() / 1000000ULL;
    char czasPracy[22];
    snprintf(czasPracy, sizeof(czasPracy), "Praca %lud %02u:%02u:%02u",
             (unsigned long)(sekundyPracy / 86400ULL),
             (unsigned)((sekundyPracy / 3600ULL) % 24),
             (unsigned)((sekundyPracy / 60ULL) % 60),
             (unsigned)(sekundyPracy % 60));
    display.println(czasPracy);
    display.print("Flagi:0x"); display.println(flagiBledow(), HEX);
    display.print("TEC:"); display.print(canTEC); display.print(" REC:"); display.print(canREC);
  }
  display.display();
}

void setup() {
  Serial.begin(115200);
  // OE wyłączone zanim zaczniemy konfigurować SPI.
  digitalWrite(PIN_TXS_OE, LOW);
  pinMode(PIN_TXS_OE, OUTPUT);
  digitalWrite(PIN_CAN_CS, HIGH);
  pinMode(PIN_CAN_CS, OUTPUT);
  digitalWrite(PIN_CAN_SCK, LOW);
  pinMode(PIN_CAN_SCK, OUTPUT);
  digitalWrite(PIN_CAN_MOSI, LOW);
  pinMode(PIN_CAN_MOSI, OUTPUT);
  pinMode(PIN_CAN_MISO, INPUT);
  pinMode(PIN_CAN_INT, INPUT);
  pinMode(PIN_LED_ZYCIA, OUTPUT);
  digitalWrite(PIN_LED_ZYCIA, LOW);
  SPI.begin(PIN_CAN_SCK, PIN_CAN_MISO, PIN_CAN_MOSI, PIN_CAN_CS);

  Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL);
  Wire.setClock(100000);
  Wire.setTimeOut(30); // Awaria I2C nie może bez końca zatrzymać całego programu.
  Wire.beginTransmission(0x3C);
  if (Wire.endTransmission() == 0) oledGotowy = display.begin(0x3C, true);
  if (oledGotowy) display.setContrast(2);

  sensors.begin();
  deviceCount = sensors.getDS18Count();
  sensors.setResolution(12);
  sensors.setWaitForConversion(false); // Nie zatrzymujemy CAN na czas konwersji temperatur.
  dht.setup(PIN_DHT22, DHTesp::DHT22);


  delay(100); // Oba napięcia zasilające TXS muszą być już stabilne.
  digitalWrite(PIN_TXS_OE, HIGH);
  delay(1);
  canGotowy = uruchomCAN();
  ostatniaProbaCAN = millis();
  Serial.println("Szklarnia ESP32/CAN. Oswietlenie usuniete.");
  Serial.println("DS18B20 GPIO14 + AM2302 GPIO13: pomiary rzeczywiste co 2.5 s.");
  Serial.printf("OLED=%s DS wykryte=%d\n", oledGotowy ? "OK" : "BRAK", deviceCount);
  startEkranu = millis();
  Serial.println("OLED: DIAGNOSTYKA 30 sekund");
  odswiezOLED(startEkranu);
  Serial.println(canGotowy ? "MCP skonfigurowany; odbior HUB potwierdza dopiero jego heartbeat." : "Blad inicjalizacji MCP — ponawianie co 5 s.");
  // Pierwsza konwersja po 2,5 s; odczyt DS i DHT po kolejnych 800 ms.
  ostatniPomiar = millis();
}

void loop() {
  uint32_t teraz = millis();
  if (!ekranPomiarow && teraz - startEkranu >= CZAS_DIAGNOSTYKI_MS) {
    ekranPomiarow = true;
    zmianaStronyOLED = teraz;
    Serial.println("OLED: POMIARY po 30 sekundach");
    odswiezOLED(teraz);
  }
  if (ekranPomiarow && teraz-zmianaStronyOLED >= OKRES_STRONY_OLED_MS) {
    ekranAnalogowy = !ekranAnalogowy;
    zmianaStronyOLED = teraz;
    Serial.println(ekranAnalogowy ? "OLED: ADS 2/2" : "OLED: TEMPERATURY 1/2");
    odswiezOLED(teraz);
  }
  odbierzJednaRamke();
  odbierzJednaRamke(); // MCP2515 ma dwa bufory odbiorcze.

  if (!konwersjaTrwa && teraz-ostatniPomiar >= OKRES_POMIAROW_MS) {
    ostatniPomiar = teraz;
    adsKrok = 0; adsCzeka = false;
    sensors.requestTemperatures();
    startKonwersji = millis();
    konwersjaTrwa = true;
  }
  if (konwersjaTrwa && (uint32_t)(millis()-startKonwersji) >= 800) zakonczPomiar();
  obsluzADS(millis());
  obsluzWysylkePomiarow(teraz);

  if (teraz-ostatniHeartbeat >= OKRES_HEARTBEAT_MS) {
    ostatniHeartbeat = teraz;
    nadajHeartbeat();
    ledStan = !ledStan;
    digitalWrite(PIN_LED_ZYCIA, ledStan);
  }
  if (canGotowy && teraz-ostatniaKontrolaCAN >= 1000) {
    ostatniaKontrolaCAN = teraz;
    canEflg = mcp2515.getErrorFlags();
    canTEC = mcp2515.errorCountTX();
    canREC = mcp2515.errorCountRX();
    if (canEflg & (MCP2515::EFLG_RX0OVR | MCP2515::EFLG_RX1OVR)) mcp2515.clearRXnOVRFlags();
    if (canEflg & MCP2515::EFLG_TXBO) canGotowy = false;
  }
  if (!canGotowy && teraz-ostatniaProbaCAN >= 5000) {
    ostatniaProbaCAN = teraz;
    canGotowy = uruchomCAN();
  }
  if (teraz-ostatniOLED >= 1000) {
    ostatniOLED = teraz;
    odswiezOLED(teraz);
  }
  delay(1);
}
