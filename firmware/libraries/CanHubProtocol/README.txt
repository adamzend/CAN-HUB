CAN HUB — dodawanie własnych modułów, protokół CAN v3
2026-10-19

1. Co trzeba zmienić w nowym module
W przykładzie ThreeValues zmień NODE_ID na wolną liczbę 1–95.
Nie zmieniaj ID ramek, slotów, kolejności bajtów ani typu liczb.
Biblioteka wylicza te parametry. ID musi być unikalne na danej magistrali.
Nie podłączaj jednocześnie dwóch ESP32 z takim samym NODE_ID.
HUB nie potrafi odróżnić dwóch fizycznych nadajników o identycznych ID ramek.

2. Biblioteka i przykład
Biblioteka: firmware/libraries/CanHubProtocol (wersja 3.0.0).
W Arduino IDE: Szkic -> Dołącz bibliotekę -> Dodaj bibliotekę ZIP;
wybierz CanHubProtocol-3.0.0.zip z folderu roboczego.
Przykład: Plik -> Przykłady -> CanHubProtocol -> ThreeValues.
Wymaga biblioteki autowp-mcp2515 (MCP2515 autorstwa autowp).
Płytka: ESP32 Dev Module; przykład i szklarnia sprawdzone z core ESP32 3.3.2.
MCP2515: kwarc 8 MHz, CAN 125 kbit/s, SPI 100 kHz.
GPIO: SCK18, MISO19, MOSI23, CS27, INT26, TXS OE25.
Nie zmieniono fizycznych połączeń działającej szklarni.

3. Stała numeracja pomiarów
publish(0,...) wysyła pozycje 1,2,3. publish(1,...) pozycje 4,5,6 itd.
Grupa: 0–7. Pozycja: 1–24. Kolejność w tablicy values jest stała.
Nie przesuwaj kolejnych czujników po odłączeniu lub awarii jednego z nich.
W miejsce uszkodzonego pomiaru podaj 0 i status ERROR | szczegół błędu.
Do pojedynczego pomiaru w nowej grupie użyj count=1; pozostałe pola będą 0.
Zakres wartości: signed int16, -32768..32767 (nie uint16).
Ustal jednostkę i skalę w instrukcji swojego modułu, np. -55 = -5,5°C.
CAN i HUB nie przesyłają automatycznie nazw, jednostek ani liczby miejsc po przecinku.

4. Główna pętla
Wywołuj node.tick(millis(),sendFrame) w każdej szybkiej iteracji loop().
Heartbeat wysyłany jest co 1 s, niezależnie od poprawności czujników.
Odczyt czujników musi mieć timeout i nie może blokować loop na wiele sekund.
publish(group,values,status,count,sendFrame) wysyła dane i pasujący status.
Zwraca false, jeśli choć jedna transmisja się nie powiodła. Nie udaje sukcesu.
Na kolejnym cyklu ponów cały aktualny zestaw. Biblioteka nie obsługuje sprzętowej
inicjalizacji ani restartu kontrolera CAN — odpowiada za to program transportu.
Zwiększanie licznika jest automatyczne, także przy niezmiennych wartościach.

5. Konfiguracja WWW
02 Moduły -> Dodaj moduł. Wpisz nazwę, NODE_ID, kanał fizyczny i watchdog.
Domyślnie watchdog 5 s: heartbeat 1 s, pomiary przykładu/szklarni 2,5 s.
Dodaj pomiary w kolejności zgodnej z firmware. Pierwszy ma pozycję #1,
drugi #2 itd. Nazwa jest dowolna i nie decyduje o przypisaniu danych.
Podaj osobno adres Modbus danych, statusu i wieku; puste pole nie udostępnia
danego rejestru. Każdy adres musi być unikalny w całej konfiguracji.
Mnożnik pozostaw 1, jeśli nie potrzebujesz dodatkowego mnożenia w HUB-ie.
Zapisz konfigurację. Dodanie modułu nie wymaga zmiany kodu HUB-a.
Usunięcie pozycji NIE przesuwa innych i NIE przywraca jej numeru do puli.
Preferuj wyłączenie suwakiem, jeśli czujnik ma wrócić. Przy całkowitym
przebudowaniu modułu utwórz kopię konfiguracji i odtwórz moduł świadomie.

6. Rejestry HMI
Protokół Modbus nie zmienił się: TCP 502, Unit ID 1, FC03, adresy od zera.
Pomiar: signed int16. Status: uint16 (pole bitowe). Wiek: uint16 sekund.
Migracja szklarni zachowuje wszystkie adresy 100–141, suwaki i mnożniki.
Istniejące makro HMI oraz Word Lamp nie wymagają zmian.
Dodawanie zupełnie nowego czujnika wymaga oczywiście nowego odczytu/rejestru
w HMI; biblioteka nie tworzy automatycznie kontrolek na ekranie HMI.

7. Ramki (standardowe CAN 11-bit, DLC zawsze 8, bez RTR)
Dane = NODE_ID*16 + grupa*2. Status = dane+1.
Heartbeat = 0x700+NODE_ID. Blok 16 ID zarezerwowany dla każdego modułu.
Bajt 0: 3 (wersja). Bajt 1: sekwencja 0..255, po 255 wraca 0.
Dane: bajty 2–3 pomiar A, 4–5 B, 6–7 C; int16 little-endian.
Status: te same pary bajtów, uint16 little-endian dla A/B/C.
Heartbeat: bajt 1 niezależny licznik; 2–3 flagi modułu; 4–7 uptime sekund.
Nie wysyłaj jednej starej ramki z tym samym licznikiem jako nowego pomiaru.
HUB łączy wyłącznie dane i diagnostykę z pasującą wersją i sekwencją.

8. Znaczenie bitów statusu — identyczne jak dotąd
0  błąd pomiaru/źródła (zbiorczy); ustaw przy każdym błędzie unieważniającym pomiar
1  brak heartbeat — wyłącznie HUB
2  wyłączony suwakiem — wyłącznie HUB
3  brak aktualnego pomiaru — wyłącznie HUB
4  brak aktualnej diagnostyki pasującej do pomiaru — wyłącznie HUB
5  inicjalizacja / wynik niegotowy
6  brak odpowiedzi czujnika
7  timeout odczytu
8  błąd CRC / sumy kontrolnej
9  błąd komunikacji z czujnikiem
10 nieprawidłowy format odpowiedzi
11 poza zakresem
12 przepełnienie (HUB także ustawia po przepełnieniu mnożnika)
13 błąd konfiguracji
14 nasycenie wejścia
15 inny rozpoznany błąd
Biblioteka usuwa bity 1–4 z statusu ESP32. Przy bicie 0 wysyła wartość 0.
Przykład CRC: ERROR | CRC = 257. Inny pomiar w tej samej ramce nadal działa.
HUB zeruje pomiar dla bitów 0,1,3,4. Wyłączenie ma pierwszeństwo: wartość0,
wiek0 i wyłącznie status4; nie powoduje alarmu pojedynczego czujnika.
Brak samego HUB-a rozpoznaje HMI lokalnie, tak jak przed migracją.

9. Szklarnia NODE_ID=3
Pozycje 1–3: DS18B20 S1–S3; ID 48/49 (0x30/31).
4–6: DS18B20 S4, AM2302 temperatura, AM2302 wilgotność; 50/51 (0x32/33).
7–9: Analog1–3 mV; 52/53 (0x34/35).
10–12: Analog4–6 mV; 54/55 (0x36/37).
13–14: Analog7–8 mV; 56/57 (0x38/39), pozycja15 rezerwowa/zerowa.
22–23: zachowane metadane techniczne: licznik cykli i liczba DS przy starcie;
62/63 (0x3E/3F), bez mapowania do HMI. Heartbeat 1795 (0x703).
Pomiar/status: 12 ramek na cykl 2,5s, odstępy20ms. Heartbeat niezależny1s.
Analogowe 7–14 w konfiguracji użytkownika pozostają wyłączone.

10. Backup i zgodność
Nowy eksport JSON: format can-hub-config, version3. Zawiera protocol,
position, next_position, wyliczone CAN ID/slot, enabled i wszystkie adresy.
Uzupełnianie rozpoznaje moduły po kanale+ID, pomiary v3 po stałej position.
Nie zmienia istniejących adresów/suwaków. Zachowuje najwyższy next_position.
Nadpisanie zastępuje całą konfigurację. Import aktywuje się bez restartu.
Backupy1/2 są obsługiwane bez automatycznego przestawiania starych firmware.
Nie wolno scalać dwóch wersji protokołu w jednym module.
HUB odrzuca kolizje ramek również pomiędzy v3 i starszymi modułami.
Backup JSON nie zawiera programu ESP32, systemu RPi ani haseł.

11. Monitor ramek
04 Monitor ramek CAN: wybór modułu oraz parametru lub wszystkich parametrów.
Pokazuje tylko to, co odebrano, i niczego nie nadaje. Rozróżnia wiek ostatniej
ramki od wieku ostatniej zmiany sekwencji. Zachowuje nieaktualne ramki z oznaczeniem.
Status0 zielony,1 czerwony; dane jasnoniebieskie; inne bajty ciemnożółte.
Zarezerwowane bity1–4 surowego statusu są szare. Status efektywny Modbus
jest pokazany oddzielnie. Nie pomyl błędu dodanego przez HUB z błędem ESP32.
Surowa ramka nie deklaruje liczby używanych pozycji. Zerowe nieskonfigurowane
pole może być prawidłowym pomiarem równym0, więc nie jest automatycznie ukrywane.

12. Odbiór nowego modułu
Sprawdź heartbeat, zmianę sekwencji, wartości i zgodność pozycji.
Przetestuj liczbę ujemną, stały pomiar, awarię jednego czujnika, odłączenie
modułu, wyłączenie suwakiem oraz eksport/import backupu. Najpierw testuj
na stanowisku, nie na działających urządzeniach wykonawczych.
