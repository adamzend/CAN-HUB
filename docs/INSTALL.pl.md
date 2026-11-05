# Instalacja CAN HUB-a

[English](INSTALL.en.md) · [Opis projektu](../README.pl.md)

## Platforma i zawartość

Wersja referencyjna: Raspberry Pi 5, 64-bitowy Raspberry Pi OS/Debian Trixie z firmware Raspberry Pi i NetworkManagerem zarządzającym `eth0`. Nakładka Waveshare 2-CH CAN HAT ma dwa MCP2515 z kwarcami 16 MHz. Moduł MCP2515 przy ESP32 ma kwarc **8 MHz** — tych ustawień nie należy zamieniać.

Repozytorium zawiera WWW, odbiornik CAN, serwer Modbus, symulator, archiwizację FTP/DTL, opcjonalną integrację SATEL, sterownik ILI9341, reset hasła, bibliotekę ESP32, szkice i testy. Nie zawiera kont użytkowników, konfiguracji działającej instalacji, archiwów pomiarów ani prywatnego pełnego projektu HMI. Nowa instalacja zaczyna z pustą listą modułów.

## 1. Instalacja programu

Na Raspberry Pi z działającym Ethernetem i SSH:

```sh
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/adamzend/CAN-HUB.git
cd CAN-HUB
sudo bash scripts/install.sh
```

Instalator używa pakietów Debiana, sprawdza zgodność API, zakłada konto usługi `canhub`, zapisuje kod w `/opt/can-hub`, a dane w `/var/lib/can-hub-web`. Wymagany jest **pymodbus 3.8.6**. Nowe wersje tej biblioteki nie są automatycznie zgodne. Plik `requirements.txt` opisuje zależności dla osobnego środowiska programistycznego; instalator nie używa pip do zmiany systemowego Pythona.

Porty TCP 80 (WWW) i 502 (Modbus) muszą być wolne. Modbus używa Unit ID 1 i FC03. Program jest przeznaczony do zaufanej sieci lokalnej: nie przekierowuj tych portów do Internetu. Hasło WWW nie zabezpiecza odczytów Modbus. Instalator nie zmienia konfiguracji startowej, nie restartuje RPi, nie programuje ESP32/HMI i nie instaluje tymczasowych narzędzi diagnostyki zasilania.

Usługa SATEL zostanie zainstalowana, lecz komunikacja i sterowanie są domyślnie wyłączone. Pobieranie FTP jest domyślnie ręczne, bez hasła; przykładowy adres wymaga zastąpienia adresem własnego urządzenia.

## 2. Pierwsze logowanie

Otwórz `http://<aktualne-IP-RPi>/can`. Konto WWW ma stałą nazwę **omen**, niezależną od konta Linux. Ustanów hasło (minimum 10 znaków), podaj docelowy wolny adres IP, prefiks i opcjonalnie bramę/DNS. Nie kopiuj adresów przykładowych bez dopasowania do sieci.

Zmiana zapisuje profil NetworkManagera. Zaczyna obowiązywać po świadomym restarcie RPi; do tego czasu aktywne połączenie pozostaje na dotychczasowym adresie. Po restarcie zmień też adres urządzenia w HMI. Funkcja konfiguracji IP zakłada interfejs `eth0`; inne nazewnictwo wymaga dostosowania `web/network_settings.py`.

## 3. Magistrala CAN

Przy odłączonym zasilaniu zamontuj HAT i połącz CAN H/L, GND oraz terminację zgodnie ze schematem. Logika nakładki musi być zgodna z RPi, czyli 3,3 V. Zrób kopię `/boot/firmware/config.txt` i dopisz poniższe ustawienia w odpowiedniej sekcji `[all]`, bez dublowania lub konfliktu z istniejącymi nakładkami SPI:

```ini
dtparam=spi=on
dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=23,spimaxfrequency=2000000
dtoverlay=mcp2515-can1,oscillator=16000000,interrupt=25,spimaxfrequency=2000000
```

Uruchom ponownie RPi, potem z katalogu repozytorium wykonaj:

```sh
sudo bash scripts/enable-can.sh
sudo reboot
```

Skrypt sprawdza obecność obu kontrolerów SPI0, ustawia stałe nazwy interfejsów i usługę uruchamiającą CAN z prędkością 125 kbit/s. Po restarcie sprawdź:

```sh
ip -details -statistics link show canhat0
ip -details -statistics link show canhat1
systemctl status can-hub-interfaces can-hub-web
```

Jeżeli nazwy się nie zmieniły, sprawdź `udevadm info -q property /sys/class/net/can0` i dopasuj `Path=` w `/etc/systemd/network/10-canhat*.link` do właściwości `ID_PATH`. Złącza CAN0/CAN1 odpowiadają `canhat0`/`canhat1` oraz kanałom 0/1 konfiguracji.

## 4. Opcjonalny ekran ILI9341

Użyj [schematu połączeń](../assets/rpi5-ili9341-wiring.png). Sprawdź dopuszczalne zasilanie i zworę konkretnej płytki LCD; sygnały logiczne mają 3,3 V. Sterownik używa SPI1: CS GPIO18, SCLK21, MOSI20, MISO19, DC16, RESET26. Do konfiguracji startowej dodaj, a następnie zrestartuj RPi:

```ini
dtoverlay=spi1-1cs,cs0_pin=18
```

```sh
sudo systemctl enable --now can-hub-display.service
```

Program oczekuje `/dev/spidev1.0` i `/dev/gpiochip0`. Zależności gpiod v2, spidev, Pillow i czcionki DejaVu instaluje skrypt. Bez zgodnej telemetrii/`vcgencmd` napięcie może być niedostępne. Bez podłączonego ekranu nie włączaj usługi. Po aktualizacji aplikacji uruchom ponownie usługę LCD, jeśli wcześniej była używana.

## 5. ESP32 i konfiguracja modułu

Instrukcja [firmware i protokołu](FIRMWARE.en.md) opisuje wymagane biblioteki, format ramek i wszystkie bity statusu. W Arduino IDE wybierz **ESP32 Dev Module**; wersja referencyjna core Espressif to **3.3.2**. Skopiuj `firmware/libraries/CanHubProtocol` do `libraries` szkicownika. Do przykładu ThreeValues doinstaluj bibliotekę **autowp-mcp2515**, wybierz nieużywany NODE_ID, np. 4, właściwy port USB, skompiluj i wgraj.

Szkic szklarni znajduje się w `firmware/node3_szklarnia_can`, wraz z wymaganym podkatalogiem `src`. Doinstaluj OneWire, DallasTemperature, Adafruit GFX Library, Adafruit SH110X, Adafruit BusIO i bibliotekę DHT sensor library for ESPx (`DHTesp.h`). Wire/SPI/esp_timer pochodzą z core ESP32. Wpisz adresy ROM swoich DS18B20 zamiast przykładowych tablic `DeviceAddress`, sprawdź GPIO i typ OLED. Kwarc MCP2515: 8 MHz, CAN 125 kbit/s, SPI 100 kHz.

W menu Moduły dodaj ID, kanał i watchdog 5 s. Dodaj pomiary w kolejności zgodnej z firmware. Przykład dla trzech wartości: rejestry danych/statusu/wieku 200/201/202, 203/204/205 i 206/207/208; mnożnik 1. Numery ramek i pozycje są automatyczne. Zapisz, sprawdź heartbeat, zmianę sekwencji i wartości w monitorze. Nie podłączaj dwóch modułów z tym samym ID do tej samej magistrali.

## 6. HMI

Dodaj urządzenie Modbus TCP z adresem RPi, portem 502 i Unit ID 1. Adresy w dokumentacji są zerowe, zgodne z protokołem; dobierz zgodną konwencję sterownika panelu. Najpierw sprawdź pojedynczą znaną wartość.

Dołączone makro 47 odczytuje **100–141**, nie przykładowe 200–208. Wymaga nazw urządzeń `CAN_HUB` i `Weintek`, wolnej pamięci LW/LB i wywoływania co sekundę. Dostosuj mapę do własnego projektu. Pomiar jest signed16, status i wiek unsigned16. Word Lamp otrzymuje numer komunikatu po dekodowaniu maski. Mapa pamięci, pierwszeństwo komunikatów i trzy poziomy kontroli świeżości są w opisie projektu. Pełny prywatny projekt HMI nie jest publikowany; kontrolki należy utworzyć we własnym projekcie.

## 7. Funkcje opcjonalne

Symulator `/sym` korzysta z oddzielnego `vcanhub` i serwera testowego Modbus. Jego testowy format ramek jest niezależny od protokołu produkcyjnego. Próbkowanie `/can/sampling` wymaga wpisania danych własnego serwera FTP HMI; oryginalne pliki są zachowywane, a wykresy powstają tylko z obsługiwanych i poprawnie zweryfikowanych danych DTL. Przywracanie plików na HMI nie jest zaimplementowane.

SATEL konfiguruje się osobno dla ETHM-1/ETHM-1 Plus. Integracja jest w fazie testów, nie obsługuje szyfrowanego wariantu protokołu ani poleceń zapisu Modbus z HMI. Polecenia WWW wymagają włączenia sterowania i autoryzacji. Bieżącą mapę i opis bitów pobierz z interfejsu; nie kopiuj starych map z innej instalacji.

## 8. Kopie, aktualizacja i hasło

Przed zmianami pobierz kopię JSON w Ustawieniach. Import może uzupełniać albo zastępować konfigurację. JSON nie zawiera systemu, firmware ani hasła konta. Osobno wykonuj chronioną kopię `/var/lib/can-hub-web`, która może zawierać hasła usług, konta i archiwa.

Aktualizacja: `git pull`, potem ponowne wykonanie instalatora. Przy istniejącej instalacji usługi aplikacji są zatrzymywane, a źródła i stan kopiowane do `/var/backups/can-hub`. Konfiguracja i obecne hasło pozostają. Aktualizacja powoduje przerwę pracy; nie zmienia projektu HMI ani programu ESP32.

Zapomniane hasło WWW można zresetować lokalnie lub przez SSH z sudo:

```sh
sudo can-hub-reset-password
```

Narzędzie umożliwia wpisanie własnego hasła albo wygenerowanie losowego i wymaga potwierdzenia. Nie zmienia hasła Linux.

Diagnostyka:

```sh
systemctl status can-hub-web can-hub-satel
journalctl -u can-hub-web -n 100 --no-pager
journalctl -u can-hub-display -n 100 --no-pager
```

## 9. Testy

Na Linux, bez sudo: `python3 scripts/run_tests.py`. Runner uruchamia każdy zestaw w osobnym procesie z tymczasową konfiguracją. Testuje protokół, watchdogi, dopasowanie ramek, Modbus na localhost, import kopii, parser DTL, SATEL i reset hasła. Nie nadaje do rzeczywistej magistrali, nie steruje alarmem i nie zmienia działającej konfiguracji. Używaj runnera zamiast uruchamiania przypadkowych skryptów testowych.

Testy programowe nie zastępują kontroli zasilania, terminacji, okablowania, kwarcu i awarii fizycznych czujników. Publikowaną kopię sprawdzono w odizolowanym katalogu na referencyjnym Linuxie. Nie wykonywano destrukcyjnej instalacji nowego systemu ani kolejnego wgrywania firmware.
