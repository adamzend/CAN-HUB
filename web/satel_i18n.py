"""English presentation of the shared SATEL page; never translate stored user data."""
import json
import re

# Longest match first, in one pass, so translations cannot translate each other.
PAIRS = '''
← Panel CAN-huba|← CAN-hub dashboard
Ładowanie…|Loading…
Stan dodatku SATEL|SATEL integration status
Zakładki SATEL|SATEL tabs
Konfiguracja|Configuration
Ustawienia Modbus|Modbus settings
Ekspandery i manipulatory|Expanders and keypads
Sterowanie|Control
Połączenie z centralą|Control panel connection
Włącz obsługę SATEL|Enable SATEL integration
Dodatek pracuje w osobnym procesie. Zmiany zaczynają obowiązywać po zapisaniu. Wyłączenie usuwa wskaźnik SATEL z ekranu ILI.|The integration runs in a separate process. Changes take effect after saving. Disabling it removes the SATEL indicator from the ILI display.
Adres IP modułu ETHM|ETHM module IP address
Port integracji|Integration port
Odstęp między odczytami danego stanu [s]|Polling interval per state [s]
Rzeczywisty cykl zależy także od czasu odpowiedzi centrali.|The actual cycle also depends on the panel response time.
Limit czasu pojedynczej odpowiedzi [s]|Response timeout [s]
Integracja ETHM bez kodowania, zgodnie z obecną konfiguracją modułu. Flashowanie ETHM nie jest wymagane ani wykonywane. Nazwy i historia są pobierane w tle.|Unencrypted ETHM integration, matching the current module configuration. No firmware update is required or performed. Names and history are read in the background.
Zezwalaj na polecenia sterujące z tej strony|Allow control commands from this page
Kod użytkownika jest wymagany osobno przy każdym poleceniu. Nie zapisujemy go w konfiguracji. Modbus udostępnia na razie tylko odczyty.|A user code is required for each command and is not saved in the configuration. Modbus currently provides read-only access.
Zapisz i zastosuj|Save and apply
Sprawdź połączenie|Check connection
Kopia konfiguracji SATEL|SATEL configuration backup
Sygnały dla HMI|Signals for HMI
Modbus TCP · port 502 · Unit ID 1 · FC03 · adresy zerowe (wire). Każdy sygnał ma oddzielny rejestr wartości, jakości i wieku danych. Bity awarii mają własne ID CAN-huba; nie są to kody zdarzeń SATEL. Kilka awarii może być aktywnych jednocześnie. Grupy awarii są odczytywane co około 30 s — sprawdzaj jakość i wiek. Adresy zajęte przez CAN są chronione.|Modbus TCP · port 502 · Unit ID 1 · FC03 · zero-based wire addresses. Each signal has separate value, quality and age registers. Fault IDs belong to CAN-hub, not the SATEL event code system. Multiple faults may be active simultaneously. Fault groups are polled approximately every 30 s: check quality and age. CAN addresses are protected.
Wejścia i awarie: maska bitowa. Wejście: bit 0 = naruszenie (1), bit 1 = sabotaż (2), bit 2 = alarm (4), bit 3 = blokada czasowa (8), bit 4 = blokada trwała (16). Stany sumują się, np. 5 = naruszenie + alarm. Pozostałe sygnały: 0 / 1. Jakość:|Inputs and faults use bitmasks. Input: bit 0 = violation (1), bit 1 = tamper (2), bit 2 = alarm (4), bit 3 = bypass (8), bit 4 = isolation (16). States combine: e.g. 5 = violation + alarm. Other signals: 0 / 1. Quality:
1 = aktualne|1 = current
, 3 = brak aktualnych danych, 10 = diagnostyka niedostępna w danej odpowiedzi, 12 = dodatek wyłączony. Jakość maski jest poprawna dopiero, gdy wszystkie jej składowe są aktualne. Wiek maski = wiek najstarszego odczytu; 65535 = brak odczytu. Bity niewykorzystane mają wartość 0. HMI musi sprawdzać jakość przed użyciem wartości.|, 3 = stale or missing data, 10 = diagnostics unavailable in this response, 12 = integration disabled. A mask is valid only when all its components are current. Mask age is the age of the oldest reading; 65535 = no reading. Unused bits are 0. The HMI must check quality before using a value.
Filtr: opis, numer, adres|Filter: description, number, address
Filtr mapy Modbus|Modbus mapping filter
Opis sygnału|Signal description
Wartość — adres|Value address
Jakość — adres|Quality address
Wiek — adres|Age address
+ Dodaj powiązanie|+ Add mapping
Pobierz adresy i opisy CSV|Download addresses and descriptions CSV
Pobierz znaczenie bitów CSV|Download bit definitions CSV
Zapis obejmuje konfigurację połączenia i wszystkie powiązania. Powiązania pozostają zarezerwowane również po wyłączeniu dodatku. Jeden opis dotyczy całego sygnału: jego wartości, jakości i wieku danych. Ta konfiguracja ma osobną kopię od konfiguracji CAN.|Saving includes connection settings and all mappings. Addresses remain reserved when the integration is disabled. One description covers the value, quality and age of a signal. This configuration has a separate backup from the CAN configuration.
Stan centrali|Panel status
Odśwież nazwy|Refresh names
Odczytaj historię|Read history
Wejścia i wyjścia — także z ekspanderów|Inputs and outputs — including expanders
Pełna numeracja centrali 1–128. Pokazanie pozycji lub nazwy nie oznacza, że wejście jest fizycznie wykorzystane. Nieaktualne odczyty są oznaczone „?”.|Full panel numbering, 1–128. A listed number or name does not prove that the input is physically used. Stale readings are marked with “?”.
Wejścia (128)|Inputs (128)
Wyjścia (128)|Outputs (128)
Strefy (32)|Partitions (32)
Filtr numeru lub nazwy|Filter by number or name
Ostatnio odczytane zdarzenia|Recently retrieved events
Bufor do 100 zdarzeń. Opisy pochodzą z centrali. Protokół podaje rok modulo 4; pokazujemy dzień, miesiąc i godzinę bez zgadywania pełnego roku. Numer źródła zależy od rodzaju zdarzenia.|Buffer of up to 100 events. Descriptions come from the panel. The protocol reports the year modulo 4; day, month and time are displayed without guessing the full year. Source numbers depend on the event type.
Czas centrali|Panel time
Odśwież listę|Refresh list
Identyfikacja pochodzi z centrali. Skan obejmuje 64 adresy ekspanderów i 18 pozycji grupy LCD i jest wykonywany w tle przy uruchomieniu oraz co godzinę. Lista może pozostać w pamięci po utracie połączenia — bieżący stan jest wtedy nieznany.|Identification comes from the panel. A background scan covers 64 expander addresses and 18 LCD entries at startup and hourly. The list may remain cached after a disconnection; current device status is then unknown.
Stan / awarie|Status / faults
Potwierdzenie identyfikacji|Identification confirmation
Manipulatory i urządzenia magistrali LCD|Keypads and LCD bus devices
Adres / pozycja|Address / entry
Grupa LCD obejmuje również ETHM i INT-RS. Diagnostyka fizycznych adresów 00h–07h: komunikacja, podmiana, sabotaż, inicjalizacja; dla ETHM / INT-RS dodatkowo LAN / DSR. Pozycje wirtualne nie mają przypisanego bitu awarii — ich stan pozostaje nieznany.|The LCD group also includes ETHM and INT-RS. Diagnostics for physical addresses 00h–07h: communication, replacement, tamper and initialization; ETHM / INT-RS additionally provide LAN / DSR diagnostics. Virtual entries have no assigned fault bit and their status remains unknown.
Adresy lokalne: 00h–1Fh na każdej magistrali. Nazwy modułów nie określają przypisania numerów wejść; monitor wejść nadal obejmuje pełny zakres 1–128. Brak potwierdzenia identyfikacji nie oznacza automatycznie braku fizycznego modułu.|Local addresses: 00h–1Fh on each bus. Module names do not determine input assignments; input monitoring covers the full range 1–128. Missing identification confirmation does not necessarily mean a module is physically absent.
Polecenie do centrali|Panel command
Sterowanie wymaga włączenia w konfiguracji i aktualnego połączenia.|Control must be enabled in settings and requires a current connection.
Załącz pełne czuwanie strefy|Fully arm partition
Wyłącz czuwanie strefy|Disarm partition
Skasuj alarm w strefie|Clear partition alarm
Zablokuj czasowo wejście|Bypass input
Odblokuj wejście|Unbypass input
Włącz wyjście|Activate output
Wyłącz wyjście|Deactivate output
Numer strefy / wejścia / wyjścia|Partition / input / output number
Kod użytkownika (z prefiksem, jeśli używany)|User code (including prefix, if used)
Kod nie jest zapamiętywany przez formularz.|The form does not retain the code.
Wyślij po potwierdzeniu…|Send after confirmation…
Zakres uprawnień i możliwość sterowania wyjściem ustala centrala. Nie ponawiamy poleceń automatycznie. Przy braku potwierdzenia najpierw sprawdź stan w monitoringu.|Permissions and output control capability are determined by the panel. Commands are not retried automatically. If confirmation is missing, check the monitored state first.
Sesja wygasła. Zaloguj się w panelu CAN-huba, a następnie wróć na tę stronę.|Session expired. Sign in to CAN-hub, then return to this page.
Błąd żądania|Request failed
Masz niezapisane zmiany.|You have unsaved changes.
Usuń powiązanie|Remove mapping
Sygnał 0/1 — nie jest maską.|Binary signal (0/1), not a mask.
Zapisano. Usługa stosuje konfigurację.|Saved. The service is applying the configuration.
Przekazano do usługi SATEL. Oczekiwanie na wynik.|Submitted to the SATEL service. Waiting for the result.
Brak odczytanych awarii|No faults reported
Stan nieznany|Unknown state
Niepotwierdzona|Unconfirmed
Potwierdzono|Confirmed
Nie odczytano jeszcze żadnego urządzenia LCD.|No LCD devices have been retrieved yet.
Nie odczytano jeszcze żadnego ekspandera.|No expanders have been retrieved yet.
Obsługa SATEL wyłączona|SATEL integration disabled
urządzenia LCD:|LCD devices:
Pozostało adresów do sprawdzenia:|Addresses remaining:
Skan zakończony.|Scan complete.
Oczekiwanie na usługę|Waiting for the service
Wyłączone · pole SATEL na ILI pozostaje puste|Disabled · the SATEL area on the ILI display remains blank
Ostatnia odpowiedź:|Last response:
 s temu| s ago
 · centrala | · panel 
Wejścia: 128 · Wyjścia: 128 · Strefy: do 32. Dane obejmują wejścia ekspanderów rozpoznanych przez centralę.|Inputs: 128 · Outputs: 128 · Partitions: up to 32. Includes inputs on expanders recognized by the panel.
Bieżąca awaria:|Current trouble:
pamięć awarii:|Trouble memory:
Brak odczytu stanu awarii|No trouble status reading
Grupa diagnostyczna|Diagnostic group
aktywne bity|active bits
numeracja od 1|numbered from 1
Szczegółowe awarie: odczyt wyłączony.|Detailed faults: reading disabled.
Oczekiwanie na diagnostykę.|Waiting for diagnostics.
Brak aktywnych awarii w aktualnie odczytanych sygnałach.|No active faults in the current readings.
Nieaktualne:|Stale:
Niedostępne w odpowiedzi ETHM:|Unavailable in ETHM response:
Pozostało nazw:|Names remaining:
Odczyt opisu…|Reading description…
 (powrót)| (restore)
Brak odczytanych zdarzeń.|No events retrieved.
Sterowanie włączone. Wybierz działanie i konkretny obiekt.|Control enabled. Select an action and a specific target.
Sterowanie wyłączone w zapisanej konfiguracji.|Control is disabled in the saved configuration.
Wysłać polecenie do centrali?|Send the command to the panel?
Brak aktualnych danych o awariach.|No current fault data.
Brak danych ze strony CAN-huba|No data from CAN-hub
Maska stanu|State mask
maska awarii|fault mask
Naruszenie|Violation
Sabotaż|Tamper
Blokada czasowa|Bypass
Blokada trwała|Isolation
Alarm pożarowy|Fire alarm
Bieżąca awaria|Current trouble
Pamięć awarii|Trouble memory
Czuwanie|Armed
Aktywne|Active
Aktywna|Active
Wejście|Input
Wyjście|Output
Strefa|Partition
Ekspandery|Expanders
Pozycja |Entry 
Rodzaj|Type
Numer|Number
Sygnał|Signal
Magistrala|Bus
Adres|Address
Nazwa|Name
Źródło|Source
Działanie|Action
Wartość|Value
Znaczenie|Meaning
Zamknij|Close
Usuń|Remove
Bity|Bits
'''
TRANSLATIONS = dict(line.split('|',1) for line in PAIRS.strip().splitlines())
# Short ASCII labels must not replace substrings in JavaScript identifiers.
SHORT = {'Tak':'Yes','Nie':'No','TAK':'YES','nie':'no','Opis':'Description','Kod':'Code','Typ':'Type','Nr':'No.'}

RUNTIME = '''
Łączność poprawna, brak bieżących awarii centrali|Connection OK, no current panel trouble
Oczekiwanie na pełny zestaw aktualnych odczytów|Waiting for a complete set of current readings
Centrala zgłasza bieżącą awarię|The panel reports current trouble
Usługa SATEL nie odpowiada lub stosuje konfigurację|SATEL service is unavailable or applying configuration
Brak aktualnych odczytów|No current readings
ETHM-1: starszy format grupy awarii 1E (29 bajtów)|ETHM-1: legacy trouble group 1E format (29 bytes)
Konfiguracja zmieniona lub polecenie wygasło; nie wysłano.|Configuration changed or command expired; nothing was sent.
Centrala przyjęła polecenie. Sprawdź rezultat w monitoringu; polecenie nie będzie ponawiane.|Panel accepted the command. Check the monitored state; the command will not be retried.
Odczyt wersji centrali zakończony poprawnie.|Panel version read successfully.
Rozpoczęto odświeżanie nazw w tle (288 pozycji).|Background name refresh started (288 entries).
Rozpoczęto odczyt 64 ekspanderów i 18 pozycji grupy LCD.|Started scanning 64 expander and 18 LCD entries.
Rozpoczęto odczyt do 50 ostatnich zdarzeń.|Started reading up to 50 recent events.
Brak pewnego potwierdzenia. Sprawdź stan centrali przed ponowieniem.|Confirmation is uncertain. Check the panel state before retrying.
Nieprawidłowa konfiguracja.|Invalid configuration.
Nieprawidłowe pole wyboru.|Invalid checkbox value.
Ta wersja obsługuje integrację bez kodowania. Nie zmieniaj kodowania w centrali dla tego formularza.|This version supports unencrypted integration. Do not change panel encryption for this form.
Wpisz poprawny adres IPv4 modułu ETHM.|Enter a valid ETHM IPv4 address.
wymagane|required
Za dużo mapowań.|Too many mappings.
Nieprawidłowe mapowanie.|Invalid mapping.
Nieprawidłowy sygnał SATEL.|Invalid SATEL signal.
Sygnał jest już przypisany.|Signal is already mapped.
Kolizja lub nieprawidłowy adres Modbus:|Conflicting or invalid Modbus address:
Adresy CAN i SATEL muszą być unikalne.|CAN and SATEL addresses must be unique.
Opis adresu może mieć maksymalnie 160 znaków.|Address description must not exceed 160 characters.
Najpierw włącz i zapisz obsługę SATEL.|Enable and save SATEL integration first.
Nieznane działanie.|Unknown action.
Sterowanie nie zostało włączone w konfiguracji.|Control is not enabled in configuration.
Brak aktualnego połączenia z centralą. Sterowanie niedostępne.|No current panel connection. Control is unavailable.
Potwierdź polecenie.|Confirm the command.
Nieprawidłowe polecenie lub numer.|Invalid command or number.
Kod (z prefiksem, jeśli używany) musi mieć 4–16 cyfr.|Code (with prefix if used) must contain 4–16 digits.
Usługa SATEL nie jest uruchomiona.|SATEL service is not running.
Poprzednie polecenie trwa. Poczekaj na wynik.|Previous command is still running. Wait for its result.
Nieoczekiwane potwierdzenie polecenia|Unexpected command confirmation
Sterowanie wyłączone|Control disabled
Nieznana operacja|Unknown operation
Przekroczono czas odpowiedzi ETHM|ETHM response timed out
ETHM zamknął połączenie|ETHM closed the connection
Błąd CRC odpowiedzi|Response CRC error
Nieprawidłowe kodowanie ramki|Invalid frame encoding
Odpowiedź na inne polecenie|Response to a different command
Zbyt długa odpowiedź|Response too long
Nieprawidłowy kod użytkownika|Invalid user code
Nieprawidłowy numer|Invalid number
Awaria pomocniczego procesora STM (zbiorczo)|Auxiliary STM processor fault (aggregate)
Awaria EEPROM / dostępu do RTC|EEPROM / RTC access fault
Pamięć restartu centrali|Panel restart memory
Błąd inicjalizacji modemu zewnętrznego|External modem initialization error
Błąd polecenia modemu zewnętrznego|External modem command error
Brak napięcia linii telefonicznej|Telephone line voltage missing
Nieprawidłowy sygnał linii telefonicznej|Invalid telephone line signal
Brak sygnału linii telefonicznej|Telephone line signal missing
Awaria monitoringu do stacji|Reporting failure to station
Awaria akumulatora centrali|Panel battery fault
Brak zasilania AC centrali|Panel AC power missing
Brak akumulatora centrali|Panel battery missing
Awaria zasilania|Power supply fault
Awaria magistrali|Bus fault
Awaria zegara RTC|RTC clock fault
Brak sygnału DTR|DTR signal missing
Awaria pamięci RAM|RAM fault
Brak komunikacji|Communication loss
Podmiana urządzenia|Device replacement
Błąd inicjalizacji|Initialization error
Brak akumulatora|Battery missing
Brak AC|AC power missing
Akumulator|Battery
Przeciążenie|Overload
Podmiana modułu|Module replacement
brak komunikacji|communication loss
podmiana urządzenia|device replacement
błąd inicjalizacji|initialization error
brak LAN / DSR|LAN / DSR missing
brak AC|AC power missing
awaria akumulatora|battery fault
brak akumulatora|battery missing
przeciążenie zasilania|power supply overload
podmiana modułu|module replacement
sabotaż|tamper
awaria techniczna|technical fault
zwarcie / zagłuszanie|short circuit / jamming
słaba bateria|low battery
awaria monitoringu|reporting failure
Ekspander magistrala|Expander bus
Czytnik A ekspandera magistrala|Expander reader A, bus
Czytnik B ekspandera magistrala|Expander reader B, bus
Urządzenie radiowe pozycja|Wireless device entry
Wyjście radiowe pozycja|Wireless output entry
Pilot administratora|Administrator keyfob
Pilot użytkownika|User keyfob
 adres | address 
 pozycja | entry 
Awaria|Fault
awaria|fault
'''

def english_page(html):
    pattern = '|'.join(re.escape(s) for s in sorted(TRANSLATIONS,key=len,reverse=True))
    html = re.sub(pattern,lambda m:TRANSLATIONS[m.group()],html)
    for source,target in SHORT.items():
        html=html.replace('>'+source+'<','>'+target+'<').replace("'"+source+"'","'"+target+"'")
    html=html.replace('lang="pl"','lang="en"').replace("'pl-PL'","'en-GB'")
    html=html.replace('href="/can"','href="/can?lang=en"')
    # Only translate generated UI messages, never user names, event text or editable descriptions.
    dictionary=dict(TRANSLATIONS)
    dictionary.update(line.split('|',1) for line in RUNTIME.strip().splitlines())
    ordered=sorted(dictionary,key=len,reverse=True)
    script='const enDictionary='+json.dumps(dictionary,ensure_ascii=False)+';\n'
    script+='const enPattern=new RegExp('+json.dumps('|'.join(re.escape(s) for s in ordered),ensure_ascii=False)+',"g");\n'
    script+='function enText(value){return String(value??"").replace(enPattern,key=>enDictionary[key]);}\n'
    html=html.replace('<script>','<script>\n'+script,1)
    html=html.replace("$('message').textContent=text", "$('message').textContent=enText(text)")
    html=html.replace("Error(v.detail||", "Error(enText(v.detail)||")
    html=html.replace('esc(b.label)','esc(enText(b.label))')
    html=html.replace("r.fault_labels.join(', ')","r.fault_labels.map(enText).join(', ')")
    html=html.replace("s.message||", "enText(s.message)||")
    html=html.replace("+f.label", "+enText(f.label)")
    html=html.replace("s.compatibility||''", "enText(s.compatibility)")
    html=html.replace("textContent=s.operation.message", "textContent=enText(s.operation.message)")
    html=html.replace('${key}: ${error}', '${key}: ${enText(error)}')
    return html
