# DodatekEZD – alternatywa dla EZD AddIn dla Linuksa i macOS

**DodatekEZD** to funkcjonalny odpowiednik aplikacji **EZD AddIn** dla systemów Linux i macOS. Aplikacja integruje się z systemem EZD i obsługuje linki `ezd://`.

## Co potrafi DodatekEZD?

DodatekEZD realizuje te same główne zadania co EZD AddIn:

- **Bezpośrednie edytowanie dokumentów w EZD** – otwieranie dokumentów z EZD, edycja w programie biurowym i zapis z powrotem do EZD bez konieczności ręcznego pobierania i wgrywania.
- **Elektroniczne podpisywanie dokumentów** – integracja z lokalnie zainstalowanym serwerem podpisu [DSS WebApp](https://ec.europa.eu/digital-building-blocks/DSS/webapp-demo/) umożliwiającym składanie podpisu elektronicznego na dokumentach obsługiwanych przez EZD. DodatekEZD przy każdym uruchomieniu automatycznie sprawdza, czy serwer DSS WebApp działa i w razie potrzeby uruchamia go samodzielnie.

## Czego DodatekEZD nie robi?

Nie zaimplementowano funkcji:

- **wydruku kodów EPL (RPW/składu)**

---

## Struktura repozytorium

Skrypty instalacyjne wymagają następującej struktury katalogów – należy pobrać całe repozytorium, a nie tylko pojedynczy skrypt:

```tree
DodatekEZD/
├── app/
├── dss/
│   └── Dockerfile
├── install/
│   ├── setup_linux.sh
│   └── setup_macos.sh
└── uninstall/
    ├── uninstall_linux.sh
    └── uninstall_macos.sh
```

---

## Wymagania wstępne

### Linux

- dystrybucja z `apt` (Debian/Ubuntu) **albo** `dnf` (Fedora/RHEL)
- dostęp do `sudo` (instalacja pakietów)
- dostęp do internetu (pobierany jest pakiet DSS ~400 MB)

### macOS

- zainstalowany [Homebrew](https://brew.sh)
- dostęp do internetu (pobierany jest pakiet DSS ~400 MB)

---

## Instalacja

### Linux

```bash
chmod +x setup_linux.sh
./setup_linux.sh
```

Skrypt wykona kolejno:

1. Instalację wymaganych pakietów (Python, biblioteki GUI, Podman) przez `apt` lub `dnf`.
2. Skopiowanie plików aplikacji do katalogu użytkownika.
3. Utworzenie wirtualnego środowiska Pythona i instalację zależności.
4. Rejestrację obsługi linków `ezd://` w środowisku graficznym (plik `.desktop`).
5. Pobranie pakietu DSS do `~/Downloads/`.
6. Zbudowanie obrazu kontenera DSS w Podman.
7. Uruchomienie kontenera DSS nasłuchującego na `http://localhost:8080`.

### macOS

```bash
chmod +x setup_macos.sh
./setup_macos.sh
```

Skrypt wykona kolejno:

1. Instalację zależności przez Homebrew (`python-tk`, `podman`).
2. Skopiowanie plików aplikacji do katalogu użytkownika.
3. Utworzenie wirtualnego środowiska Pythona i instalację zależności.
4. Skompilowanie aplikacji `DodatekEZD.app` i zarejestrowanie jej jako obsługi linków `ezd://`.
5. Pobranie pakietu DSS do `~/Downloads/`.
6. Zbudowanie obrazu kontenera DSS w Podman.
7. Uruchomienie kontenera DSS nasłuchującego na `http://localhost:8080`.

Po wykonaniu odpowiedniego skryptu DodatekEZD jest gotowy do użycia.

---

## Ścieżki instalacji

| Zasób | Linux | macOS |
|-------|-------|-------|
| Pliki aplikacji | `~/.local/share/DodatekEZD/` | `~/Library/Application Support/DodatekEZD/` |
| Wirtualne środowisko Pythona | `~/.DodatekEzdVenv/` | `~/.DodatekEzdVenv/` |
| Serwer DSS | `~/.local/share/DssWebApp/` | `~/Library/Application Support/DssWebApp/` |
| Dane robocze (pobrane pliki) | `~/.DodatekEzdData/` | `~/.DodatekEzdData/` |
| Logi | `~/.cache/DodatekEzd.log` | `~/Library/Logs/DodatekEzd.log` |
| Cache ZIP DSS | `~/Downloads/dss-demo-bundle-6.3.zip` | `~/Downloads/dss-demo-bundle-6.3.zip` |

---

## Deinstalacja

### Linux

```bash
chmod +x uninstall/uninstall_linux.sh
./uninstall/uninstall_linux.sh
```

### macOS

```bash
chmod +x uninstall/uninstall_macos.sh
./uninstall/uninstall_macos.sh
```

---

## Konfiguracja innych podpisów elektronicznych

Plik `smart_card_config.toml` (w katalogu „Pliki aplikacji") zawiera gotową konfigurację dla:

- **Sigillum Karta Blue**
- **e-Dowód – Podpis Osobisty**

Aby dodać inny rodzaj podpisu, należy uzupełnić plik o:

- ścieżkę do biblioteki PKCS#11 (plik `.so` na Linuksie, `.dylib` na macOS),
- etykietę slotu tokenu (label) – **bez numeru slotu**, ponieważ numery mogą się zmieniać między sesjami.

Listę dostępnych tokenów i ich etykiet wyświetlisz poleceniem:

```bash
pkcs11-tool --module /ścieżka/do/biblioteki.so --list-token-slots
```

### Adres serwera DSS

W pliku `smart_card_config.toml` znajduje się również wpis:

```toml
base_url = "http://localhost:8080/services/rest"
```

Domyślnie DodatekEZD korzysta z serwera [DSS WebApp](https://ec.europa.eu/digital-building-blocks/DSS/webapp-demo/) zainstalowanego lokalnie na komputerze użytkownika. Można tu jednak podać adres serwera DSS działającego w sieci lokalnej – wówczas instalacja kontenera DSS na komputerze użytkownika nie jest konieczna.
