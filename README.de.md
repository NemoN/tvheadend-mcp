# Tvheadend MCP

[English](README.md) · **Deutsch**

Installation ohne Checkout: siehe [englische Kurzanleitung](README.md#quick-start).
Die Versionsnummer und den Pfad der Env-Datei in den Beispielen anpassen.

Eigener MCP-Server mit **39 Werkzeugen** für Tvheadend: DVR und die über dessen
JSON-API erreichbare DVB-Konfiguration. Er verwendet das offizielle MCP Python
SDK 2.x und unterstützt stdio sowie Streamable HTTP auf dem lokalen Rechner.

Der Server liest Klassen, Feldtypen, Optionen und aktuelle Werte aus der
verbundenen Tvheadend-Installation. Damit sind auch erweiterte Einstellungen
zugänglich, ohne sie auf eine feste Auswahl von Tuner-Modellen zu beschränken.

## Funktionsumfang

| Bereich | Funktionen |
| --- | --- |
| Hardware | Adapter/Frontend-Baum, aktivieren/deaktivieren, Prioritäten, SAT>IP-Erkennung |
| Satellit | Satconfig-Auswahl, LNB, DiSEqC, Rotor, Unicable/JESS über die tatsächlichen Unterobjekte und Felder |
| Netzwerke | Verfügbare Typen ermitteln, anlegen, ändern, Tuner-/Satelliten-Eingängen zuordnen, löschen |
| Muxes | Netzwerkspezifische Schemata, Frequenz/Symbolrate/Modulation/FEC/PLP/PLS, anlegen/ändern/löschen, Suchläufe |
| Sender | Services auflisten, nativer automatischer Mapper, explizite Senderzuordnung, Sendergruppen und EPG-Felder |
| DVR | EPG-Suche, EPG-Aufnahme, manuelle Timer, Autorec/Serienregeln, wiederkehrende Timer, Aufnahmeprofile, Stoppen/Abbrechen/Löschen |
| Diagnose | Serverversion, aktive Eingänge/Signalwerte, Subscriptions, Netzwerkzuordnung und Suchlaufstatus |
| Änderungen | Metadatenvalidierung, optionale Vorschau, Vorher-/Nachher-Werte, JSON-Snapshots ausgewählter Felder |

„Komplett“ bezieht sich auf die **von der laufenden Tvheadend-Version über die API
angebotenen Konfigurationsfelder**. Treiber/Firmware, physische Tuner, die reale
Satellitenverkabelung und die Unterstützung durch das Empfangsgerät bleiben
Voraussetzungen. Nicht jeder Tvheadend-Build bietet jede Klasse/jeden Endpunkt.
Ziel ist die 4.3-API; ältere Builds funktionieren im Umfang ihrer vorhandenen API.
Fehlende Endpunkte werden ausdrücklich als Fehler gemeldet.

## Lokal starten

Voraussetzungen: Python 3.11+ und [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/NemoN/tvheadend-mcp.git
cd tvheadend-mcp
uv sync --frozen
```

Kopiere `.env.example` nach `.env` und trage deine Tvheadend-URL sowie
`TVH_USER` und `TVH_PASS` ein. Die Vorlage startet mit aktivierter Schreibsperre;
setze `TVH_READ_ONLY=false`, wenn Änderungen ausgeführt werden sollen. Vorhandene Umgebungsvariablen haben Vorrang vor der
Datei. `.env` wird weder in Git noch in das Docker-Image aufgenommen.

Der Tvheadend-Benutzer benötigt **Web interface**, **Administrator** für
DVB-Verwaltung sowie die passenden **Video recorder**-Rechte. Zum Verwalten fremder
DVR-Einträge müssen auch die entsprechenden DVR-Zugriffsrechte gesetzt sein.
Die API-Berechtigungen werden weiterhin von Tvheadend durchgesetzt.

```dotenv
TVH_URL=http://tvheadend.example:9981
TVH_USER=mcp
TVH_PASS=hier-lokal-eintragen
TVH_AUTH=digest
TVH_VERIFY_SSL=true
TVH_TIMEOUT=30
TVH_TIMEZONE=Europe/Berlin
TVH_READ_ONLY=true
```

`TVH_AUTH` unterstützt `digest`, `basic` und `none`. Digest ist der Standard; Basic
bei einer entsprechend konfigurierten Tvheadend-Installation verwenden. Eine URL
mit Reverse-Proxy-Präfix wie `https://host/tvh` ist ebenfalls möglich. Kein `/api`
anhängen. Die URL enthält keine Zugangsdaten.

Verbindung und Status **nur lesend** prüfen:

```bash
uv run tvheadend-mcp --check
```

Exit-Code 0 bedeutet, dass alle drei Status-Endpunkte antworten; bei fehlenden
Rechten oder Verbindungsfehlern wird Exit-Code 1 ausgegeben. `--check` startet
keinen Suchlauf und ändert keine Konfiguration.

Als MCP-Prozess starten:

```bash
uv run tvheadend-mcp --env-file "/absolute/path/tvheadend.env"
```

Im stdio-Modus wartet der Prozess auf einen MCP-Client. Standardausgabe ist nur für
das MCP-Protokoll vorgesehen. Der Server selbst ruft keinen KI-Dienst auf und
benötigt keinen OpenAI-API-Schlüssel.

## Im MCP-Client einbinden

[examples/mcp-client.json](examples/mcp-client.json) enthält eine
stdio-Konfiguration mit Platzhaltern für Clients, die das
übliche `mcpServers`-JSON-Format verwenden. Clients mit einem anderen Format
erhalten dieselbe ausführbare Datei und Argumentliste über ihre MCP-Einstellungen.

```json
{
  "mcpServers": {
    "tvheadend": {
      "command": "uv",
      "args": [
        "run", "--directory", "/absolute/path/tvheadend-mcp",
        "tvheadend-mcp", "--env-file", "/absolute/path/tvheadend.env"
      ]
    }
  }
}
```

Alternativ Streamable HTTP unter `http://127.0.0.1:8000/mcp`:

```bash
uv run tvheadend-mcp --transport streamable-http --port 8000
```

Der HTTP-Modus bindet ausschließlich an Loopback. Für einen Betrieb auf einem entfernten Host
kann der lokale Port per SSH weitergeleitet werden. Für direkten LAN-/Internet-
Betrieb wäre eine gesonderte authentifizierte Bereitstellung nötig; diese Version
stellt keinen ungeschützten Admin-Endpunkt ins Netz.

## DVB-C und DVB-S/S2 einrichten

Typischer Auftrag an einen verbundenen Assistenten:

> Lies meine Tvheadend-Hardware und die vorhandenen DVB-C- und DVB-S/S2-Netzwerke
> aus. Zeige die Empfangswege und fehlende Zuordnungen. Nutze die tatsächlichen
> Feldoptionen meiner Tuner für Änderungen.

Der Ablauf ist auch als MCP-Ressource `tvheadend://workflow` verfügbar:

1. `tvh_status` und `tvh_hardware_tree(recursive=true)` aufrufen.
2. Den richtigen Frontend-/Satelliten-Eingang mit `tvh_get_node` und
   `tvh_get_field_options` untersuchen. Aktuelle UUIDs übernehmen.
3. Bei Bedarf `tvh_update_node` für `enabled`, `satconf` und die nachgelagerten
   LNB-/DiSEqC-/Unicable-Felder verwenden. Nach einem Wechsel des Konfigurationstyps
   den Hardwarebaum neu lesen: Unterobjekte können neue UUIDs bekommen.
4. Mit `tvh_network_types` die verfügbaren Netzwerkklassen und Felder ermitteln.
   DVB-C- und DVB-S-Netzwerke getrennt anlegen oder vorhandene weiterverwenden.
5. `tvh_assign_networks` am **tatsächlich zuständigen Objekt** verwenden. Bei
   DVB-C liegt `networks` am Frontend, bei DVB-S je nach Empfangsart am Satconfig-
   oder Satelliten-Eingang, eventuell als `network_a`, `network_aa` usw.
6. Scanlisten mit `tvh_api_read("dvb/scanfile/list", {"type":"dvbc"})`
   ermitteln oder Muxes anhand von `tvh_mux_schema` anlegen.
7. `tvh_scan_networks` starten. Über `tvh_list_networks` die `scanq_length` und
   über `tvh_list_muxes` die Suchlaufzustände/-ergebnisse beobachten.
8. Nach dem Scan `tvh_list_services`, dann `tvh_map_services` mit expliziten
   Service-UUIDs aufrufen. Mit `tvh_mapper_status` den Mapper verfolgen.
9. Sender über `tvh_list_channels` prüfen. Explizite Mehrfachzuordnungen erfolgen
   mit `tvh_assign_services`; EPG-/Senderfelder lassen sich mit `tvh_update_node`
   bearbeiten.

SCR-/Userband-Frequenzen, DiSEqC-Ports und Satellitenpositionen müssen zur realen
Anlage passen. Der Server stellt die Optionen bereit und erfindet diese Werte nicht.

### Frequenzen und Listen

`tvh_create_mux.frequency_hz` nimmt eine ganzzahlige Frequenz in **Hz** entgegen.
Bei DVB-S/S2 wird sie in die von Tvheadend verwendeten **kHz** umgerechnet; bei
DVB-C/T bleibt sie in Hz. Alternativ kann `settings.frequency` direkt in nativen
Tvheadend-Einheiten gesetzt werden. Beide Angaben gleichzeitig werden abgewiesen.
`symbolrate` wird in **Symbolen/Sekunde** angegeben.

`tvh_assign_networks` und `tvh_assign_services` verwenden standardmäßig `mode="add"`
und erhalten bestehende Zuordnungen. `replace` ersetzt die Liste, `remove` entfernt
nur die genannten Einträge. Allgemeine `tvh_update_node`-Updates ersetzen jede
übergebene Array-Eigenschaft vollständig; nicht genannte Felder bleiben bestehen.

Listenwerkzeuge liefern standardmäßig 100 Datensätze, die EPG-Suche 50. Die Grenze
pro Aufruf ist 1000. Für größere Bestände `start` anhand von `total` fortsetzen.
`tvh_diagnose_network` liefert maximal 1000 Muxes und kennzeichnet diese durch die
normalen Pagination-Felder; auch hier `total` beachten.

## DVR

- `tvh_search_epg`: Suche nach Titel/Beschreibung, optional auf einem Sender.
  Die Suchzeichenfolge ist ein regulärer Ausdruck wie in Tvheadend.
- `tvh_schedule_recording`: Aufnahme anhand der EPG-`eventId` programmieren.
  Ohne `config_uuid` wird das Standard-Aufnahmeprofil verwendet.
- `tvh_create_timer`: Manueller Timer mit Titel, Kanal, Start und Ende.
  ISO-8601-Zeitpunkte benötigen einen expliziten UTC-Offset, beispielsweise
  `2026-10-25T20:15:00+01:00`; so bleibt der Zeitpunkt bei Zeitumstellungen eindeutig.
- `tvh_create_dvr_rule`: `autorec` für automatische EPG-/Serienregeln oder `timerec`
  für wiederkehrende Uhrzeit-Timer. Felder und native Zeiteinheiten über das
  entsprechende Schema ermitteln; diese Regeln verwenden Tvheadends Server-Zeitzone.
- `tvh_create_dvr_profile`: Speicherpfad, Vor-/Nachlauf und Aufbewahrung über die
  vom Aufnahmeprofil angebotenen Felder konfigurieren.
- `tvh_recording_action`: `stop` beendet die laufende Aufnahme, `cancel` bricht
  einen Timer/eine Aufnahme ab. **`remove_files` löscht Aufnahmedateien dauerhaft.**

Regeln/Profile/Timer werden über `tvh_get_node` und `tvh_update_node` geändert.
Die endgültige Stop-/Abbruchbehandlung und Dateiaufbewahrung richtet sich nach
Tvheadend und dem Aufnahmeprofil. `TVH_TIMEZONE` steuert die Diagnoseanzeige, nicht
die Zeitzone des Tvheadend-Servers.

## Vorschau und Snapshots

Alle schreibenden Werkzeuge besitzen `dry_run`: `true` liefert den geplanten
Endpunkt und dessen Parameter, ohne zu schreiben. Normalerweise ist `dry_run=false`.
Mit `TVH_READ_ONLY=true` lassen sich Schreibzugriffe zentral sperren; lesende
Abfragen und Vorschauen funktionieren weiter.

`tvh_snapshot` exportiert die lesbaren, schreibbaren Einstellungen ausgewählter
UUIDs als JSON. Passwort-, Nur-Lese- und Write-once-Felder werden ausgelassen.
`tvh_restore_snapshot` validiert zunächst alle Objekte und zeigt standardmäßig
eine Vorschau (`dry_run=true`). Das Zurückschreiben ist **nicht atomar**;
Teilerfolge und verbleibende UUIDs werden ausgewiesen.

Ein Snapshot ist kein vollständiges Tvheadend-Backup und kann gelöschte Objekte,
Hardware-Unterobjekte nach einem Topologiewechsel oder Aufnahmedateien nicht
wiederherstellen. Für solche Änderungen wird zusätzlich das reguläre Backup des
Tvheadend-Konfigurationsverzeichnisses benötigt. Das Löschen eines Netzwerks kann
dessen Muxes und Services mit entfernen.

## Werkzeuge

Alle Namen besitzen das Präfix `tvh_`.

| Gruppe | Namen ohne Präfix |
| --- | --- |
| Status/Hardware | `status`, `hardware_tree`, `discover_satip`, `diagnose_network` |
| Schema/Objekte | `list_classes`, `get_schema`, `get_node`, `get_field_options`, `update_node`, `delete_node`, `api_read` |
| Netzwerke | `network_types`, `list_networks`, `create_network`, `assign_networks`, `scan_networks` |
| Muxes | `list_muxes`, `mux_schema`, `create_mux` |
| Services/Sender | `list_services`, `map_services`, `mapper_status`, `stop_mapper`, `list_channels`, `create_channel`, `assign_services`, `list_channel_tags`, `create_channel_tag` |
| DVR/EPG | `search_epg`, `list_recordings`, `schedule_recording`, `create_timer`, `recording_action`, `list_dvr_rules`, `create_dvr_rule`, `list_dvr_profiles`, `create_dvr_profile` |
| Snapshots | `snapshot`, `restore_snapshot` |

`tvh_api_read` erreicht zusätzliche lesende Endpunkte, etwa EPG-Grabber, Bouquets,
Streamingprofile, Scanlisten und Orbitalpositionen. Schreibende Endpunkte sind
darüber gesperrt. Nicht jedes API-Objekt besitzt einen eigenen Werkzeugnamen;
vorhandene Objekte werden über ihre UUID und die dynamischen Metadaten geändert.

## Docker

```bash
docker build -t tvheadend-mcp .
docker run --rm -i --env-file .env tvheadend-mcp
```

Für stdio **kein** `-t` verwenden. Die Netzwerknamenauflösung des Containers muss
`tvheadend.example` erreichen können; gegebenenfalls `TVH_URL` auf eine erreichbare
Adresse setzen. Das Image läuft ohne Root-Rechte. Das Docker-Image wird aus den
exportierten, festgeschriebenen Laufzeitabhängigkeiten gebaut.

## Entwicklung und Validierung

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv build
```

Die Tests verwenden nachgebildete HTTP-API-Antworten und einen echten MCP-
stdio-Unterprozess. Sie verändern keine Live-Konfiguration. Geprüft werden unter
anderem Digest-Anmeldung, Formulardaten, Versionsmetadaten, Satconfig/Unicable,
Frequenzeinheiten, Zuordnungen, Mapper, Timer über die Zeitumstellung, Schreibsperren,
Teilerfolge bei Wiederherstellung und MCP-Werkzeugschemata.

Nach Abhängigkeitsänderungen den Docker-Export aktualisieren:

```bash
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.lock
```

Implementierungsgrundlagen: offizielle [Tvheadend JSON-API](https://docs.tvheadend.org/documentation/development/json-api),
[MPEGts](https://docs.tvheadend.org/documentation/development/json-api/api-description/mpegts),
[idnode](https://docs.tvheadend.org/documentation/development/json-api/api-description/idnode),
[DVR](https://docs.tvheadend.org/documentation/development/json-api/api-description/dvr),
Abgleich mit dem [Tvheadend-Quellcode](https://github.com/tvheadend/tvheadend/tree/master/src/api)
und das [offizielle MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
