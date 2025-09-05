# Battery Manager - Startup-Optimierung

## Problem
Nach einem Home Assistant Neustart dauerte es bis zu 10 Minuten, bis die Battery Manager Entitäten verfügbar wurden.

## Identifizierte Ursachen

### 1. Langes Update-Interval
- **Problem**: Update-Interval war auf 600 Sekunden (10 Minuten) gesetzt
- **Lösung**: Reduziert auf 300 Sekunden (5 Minuten) für normale Updates

### 2. Verzögerte Startup-Phase
- **Problem**: Erste Aktualisierung wurde asynchron gestartet ohne zu warten
- **Lösung**: Erstes `async_refresh()` wird während Setup abgewartet

### 3. Adaptive Update-Intervalle
- **Problem**: Gleiche Update-Intervalle während Startup und normalem Betrieb
- **Lösung**: 
  - Startup-Phase: 30 Sekunden Update-Interval
  - Nach erfolgreichem Update: Umschaltung auf 300 Sekunden

### 4. Strikte Verfügbarkeitsbedingungen
- **Problem**: Entitäten wurden nur als verfügbar markiert wenn alle Daten gültig waren
- **Lösung**: Entitäten werden verfügbar sobald ein Update-Versuch stattgefunden hat

### 5. Verzögerte Entity-Listener
- **Problem**: Listener für Entitätsänderungen wurden erst nach erstem Update eingerichtet
- **Lösung**: Listener werden sofort während Initialisierung eingerichtet

## Implementierte Verbesserungen

### 1. Neue Konstanten (const.py)
```python
INITIAL_UPDATE_INTERVAL_SECONDS = 30  # Schnelle Updates während Startup
STARTUP_RETRY_ATTEMPTS = 5  # Maximale Startup-Versuche
MAX_HISTORICAL_SOC_AGE_HOURS = 6  # Emergency-Fallback für SOC (6 Stunden)
MAX_HISTORICAL_FORECAST_AGE_HOURS = 72  # Emergency-Fallback für Prognosen (72 Stunden)
```

### 2. Startup-Management (coordinator.py)
- Tracking von Startup-Versuchen und erfolgreichen Updates
- Automatische Umschaltung zu normalem Interval nach erfolgreichem Update
- Tolerantere Datenvalidierung während Startup-Phase
- **Emergency-Fallback System**: Verwendung sehr alter Daten (bis 6h SOC, 72h Prognosen) wenn keine aktuellen verfügbar sind
- Dreistufige Datenvalidierung: Normal → Startup-tolerant → Historischer Fallback

### 3. Verbesserte Entitätsverfügbarkeit (sensor.py)
- Entitäten werden nur verfügbar wenn gültige Daten (aktuell oder historisch) vorliegen
- **Keine Default-Werte**: Entitäten zeigen korrekt "unavailable" bis echte Daten verfügbar sind
- Bessere Fehlerbehandlung bei fehlenden Daten
- Historische Daten werden als gültige Quelle akzeptiert

### 4. Erweiterte Datenvalidierung
- Tolerantere Datenalter-Validierung während Startup
- **Erweiterte historische Datenunterstützung**: 
  - Normale Daten: SOC 1h, Prognosen 24h
  - Startup-Phase: SOC 2h, Prognosen 48h
  - **Historischer Fallback**: SOC 6h, Prognosen 72h
- Bessere Logging für Debugging
- Fallback auf zuletzt gültige Werte
- **Emergency-Fallback** auf sehr alte Daten wenn keine aktuellen Werte verfügbar sind

## Erwartete Verbesserungen

1. **Startzeit**: Entitäten werden verfügbar sobald gültige Daten vorliegen (30-60 Sekunden)
2. **Zuverlässigkeit**: Nur echte Daten werden angezeigt, keine Default-Werte
3. **Performance**: Reduzierte Update-Frequenz nach erfolgreichem Startup
4. **Debugging**: Umfangreiches Logging für Startup-Probleme

## Historische Daten-Unterstützung

### Ja, historische Werte werden umfassend unterstützt!

Der Battery Manager verwendet ein **dreistufiges Fallback-System** für Datenvalidierung:

#### 1. **Normale Betriebsphase**
- SOC-Daten: Maximal **1 Stunde** alt
- PV-Prognosen: Maximal **24 Stunden** alt

#### 2. **Startup-tolerante Phase**
- SOC-Daten: Maximal **2 Stunden** alt (doppelte Toleranz)
- PV-Prognosen: Maximal **48 Stunden** alt (doppelte Toleranz)

#### 3. **Emergency-Fallback System** (NEU)
- SOC-Daten: Maximal **6 Stunden** alt
- PV-Prognosen: Maximal **72 Stunden** alt
- Wird verwendet wenn keine aktuelleren Daten verfügbar sind
- Ermöglicht Weiterbetrieb auch bei länger andauernden Datenausfällen

### Automatischer Fallback-Mechanismus:
1. **Aktuelle Daten verfügbar**: Normale Berechnung
2. **Keine aktuellen Daten**: Verwendung der letzten gültigen Werte (wenn im Zeitrahmen)
3. **Auch keine kürzlich gültigen Daten**: Emergency-Fallback auf sehr alte historische Daten
4. **Keine Daten verfügbar**: Entitäten zeigen "unavailable" oder Fallback-Werte

### Logging:
- Ausführliche Protokollierung welche Daten verwendet werden
- Warnungen bei Verwendung alter Daten
- Information über Datenalter in Stunden

## Zusätzliche Empfehlungen
### 1. Home Assistant Konfiguration

Stelle sicher, dass die Input-Entitäten (SOC, PV-Prognosen) zuverlässig verfügbar sind:
# Beispiel für robuste Sensor-Definitionen
sensor:
  - platform: template
    sensors:
      battery_soc_stable:
        friendly_name: "Battery SOC (stable)"
        value_template: >-
          {% if states('sensor.battery_soc') not in ['unknown', 'unavailable'] %}
            {{ states('sensor.battery_soc') }}
          {% else %}
            {{ states('sensor.battery_soc_stable') | default(50) }}
          {% endif %}
```

### 2. Überwachung
- Überwache die Logs für "Battery Manager startup completed" Meldungen
- Prüfe Entity-Verfügbarkeit in Home Assistant nach Neustart
- Verwende `battery_manager.export_hourly_details` Service für detaillierte Diagnose

### 3. Weitere Optimierungen
Falls weiterhin Probleme auftreten:
- Reduziere `INITIAL_UPDATE_INTERVAL_SECONDS` auf 15 Sekunden
- Erhöhe `STARTUP_RETRY_ATTEMPTS` auf 10
- Prüfe die Zuverlässigkeit der Input-Entitäten

## Testing
1. Home Assistant neu starten
2. Logs überwachen: `grep "Battery Manager" home-assistant.log`
3. Entitätsverfügbarkeit in UI prüfen
4. Zeitmessung bis zur vollständigen Funktionalität
