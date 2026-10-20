# Parcel Tracking für Home Assistant

Eine Custom Integration für Home Assistant, die Pakete von **DHL, DPD, GLS, Hermes, UPS und Amazon Shipping** verfolgt. Dafür nutzt sie **deine eigenen API-Zugangsdaten** und keinen Drittanbieter wie AfterShip oder 17track.

- Ein Sensor pro Paket. Der Zustand ist einheitlich für alle Paketdienste: `not_found`, `pre_transit`, `in_transit`, `out_for_delivery`, `ready_for_pickup`, `delivered`, `exception`, `returned` oder `unknown`.
- Attribute: Statustext, Ort, letzte Aktualisierung, voraussichtliche Zustellung, die letzten 10 Ereignisse und ein Link zur Sendungsverfolgung.
- Pro Paketdienst ein Sensor **„Aktive Pakete“** mit der Anzahl der Pakete, die noch unterwegs sind.
- Pakete lassen sich über die Oberfläche oder per Dienst (`parceltracking.add_package` …) verwalten, also auch per Automation.
- Zugestellte Pakete werden nicht mehr abgefragt. Das schont das API-Kontingent.
- Werden die Zugangsdaten abgelehnt, startet Home Assistant automatisch die erneute Anmeldung.

## Installation

### HACS (benutzerdefiniertes Repository)

1. HACS → ⋮ → *Benutzerdefinierte Repositories* → `https://github.com/derpaul/parceltracking-integration`, Typ *Integration*.
2. „Parcel Tracking“ installieren und Home Assistant neu starten.

### Manuell

Den Ordner `custom_components/parceltracking` nach `<config>/custom_components/` kopieren und Home Assistant neu starten.

## Einrichtung

*Einstellungen → Geräte & Dienste → Integration hinzufügen → Parcel Tracking*. Wähle einen Paketdienst und gib seine Zugangsdaten ein. Für jeden weiteren Paketdienst fügst du die Integration erneut hinzu.

Pakete verwaltest du über **Konfigurieren** an der Integration: *Paket hinzufügen*, *Pakete entfernen* oder *Einstellungen* (Abfrageintervall, Standard 30 Minuten).

## Zugangsdaten je Paketdienst

| Paketdienst | Was du brauchst | Woher |
|---|---|---|
| **DHL** | API-Key | [developer.dhl.com](https://developer.dhl.com): App anlegen, API *Shipment Tracking – Unified* hinzufügen. Standardkontingent: 250 Abfragen pro Tag. |
| **UPS** | Client-ID + Client-Secret | [developer.ups.com](https://developer.ups.com): App mit dem Produkt *Tracking* anlegen (OAuth Client Credentials). |
| **GLS** | API-Key | [gls-group.eu – API-Zugang registrieren](https://gls-group.eu/DE/en/register-api-access). Der Key wird als `x-api-key` an `api-tt.de.gls-group.com` geschickt. |
| **DPD** | – | DPD bietet Privatkunden keine Tracking-API mit eigenem Token. Die Integration nutzt den Endpunkt von tracking.dpd.de ohne Zugangsdaten. |
| **Hermes** | optional API-Key | Hermes vergibt offizielle API-Zugänge (HSI) nur an Geschäftskunden. Die Integration nutzt den Endpunkt von myhermes.de. Ein eingetragener Key wird als `x-api-key` mitgeschickt. |
| **Amazon** | LWA-Client-ID, Client-Secret, Refresh-Token, Region, Business-ID, Carrier-ID | Selling Partner API mit der Rolle *Amazon Shipping*. Siehe den Hinweis unten. |

### Hinweis zu Amazon

Amazon hat **keine öffentliche Tracking-API für normale Amazon-Bestellungen**. Die Integration nutzt die *Amazon Shipping API v2* (`GET /shipping/v2/tracking`). Damit lassen sich nur Sendungen verfolgen, die über **Amazon Shipping** verschickt wurden, z. B. als Verkäufer. Dafür brauchst du eine registrierte SP-API-App. `business_id` (z. B. `AmazonShipping_UK`) und `carrier_id` (z. B. `AMZN_UK`) hängen vom Marktplatz ab.

## Dienste

```yaml
# Paket hinzufügen (ist es schon vorhanden, wird nur der Name aktualisiert)
service: parceltracking.add_package
data:
  carrier: dhl          # amazon, dhl, dpd, gls, hermes, ups
  tracking_number: "00340434161094042557"
  name: Neue Kopfhörer

# Paket entfernen
service: parceltracking.remove_package
data:
  tracking_number: "00340434161094042557"

# Alle zugestellten Pakete entfernen (optional nur für einen Paketdienst)
service: parceltracking.remove_delivered
data:
  carrier: dhl
```

## Beispiel-Automationen

Benachrichtigung, wenn ein Paket in Zustellung ist:

```yaml
automation:
  - alias: Paket kommt heute
    triggers:
      - trigger: state
        entity_id:
          - sensor.dhl_neue_kopfhorer
        to: out_for_delivery
    actions:
      - action: notify.mobile_app_handy
        data:
          message: "{{ state_attr(trigger.entity_id, 'friendly_name') }} wird heute zugestellt."
```

Jede Nacht zugestellte Pakete aufräumen:

```yaml
automation:
  - alias: Zugestellte Pakete entfernen
    triggers:
      - trigger: time
        at: "03:00:00"
    actions:
      - action: parceltracking.remove_delivered
```

## Entwicklung

```bash
pip install -r requirements_test.txt
pytest
```

Die API-Clients unter `custom_components/parceltracking/api/` hängen nicht von Home Assistant ab. Ein neuer Paketdienst braucht eine Unterklasse von `CarrierClient` und einen Eintrag in `CLIENTS`.

## Haftungsausschluss

Kein offizielles Produkt der genannten Paketdienste. Die Endpunkte von DPD und Hermes sind nicht offiziell dokumentiert und können sich jederzeit ändern.
