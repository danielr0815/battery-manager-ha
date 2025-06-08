# Battery Manager

Eine intelligente Home Assistant Integration für die Verwaltung von Batteriespeichersystemen mit PV-Anlagen.

## Features

- 🔋 **Intelligente SOC-Verwaltung**: Automatische Berechnung optimaler Batterieladestände
- ☀️ **PV-Prognose Integration**: Nutzt Wettervorhersagen für bessere Entscheidungen
- 📊 **Predictive Analytics**: Vorhersage von Min/Max SOC-Werten über mehrere Stunden
- 🔄 **Automatische Wechselrichter-Steuerung**: Ein-/Ausschalten basierend auf intelligenten Algorithmen
- 📈 **Detaillierte Sensoren**: Umfassende Überwachung aller relevanten Werte
- 🏠 **Home Assistant Native**: Vollständig integriert in die HA-Oberfläche

## Bereitgestellte Entitäten

Die Integration stellt 4 Hauptentitäten zur Verfügung:

| Entität | Typ | Beschreibung |
|---------|-----|--------------|
| **Wechselrichter Status** | Binary Sensor | Aktueller Zustand des Wechselrichters (an/aus) |
| **SOC Schwellwert** | Sensor | Berechneter Schwellwert für Wechselrichter-Steuerung |
| **Min SOC Prognose** | Sensor | Minimal erwarteter SOC in der Vorhersageperiode |
| **Max SOC Prognose** | Sensor | Maximal erwarteter SOC in der Vorhersageperiode |

## Konfiguration

Die Integration wird über die Home Assistant Benutzeroberfläche konfiguriert:

1. Gehe zu **Einstellungen** → **Geräte & Dienste**
2. Klicke auf **"+ INTEGRATION HINZUFÜGEN"**
3. Suche nach **"Battery Manager"**
4. Folge den Konfigurationsschritten
5. Gib die erforderlichen Entitäten für SOC und PV-Prognosen an

## Systemanforderungen

- **Home Assistant**: Version 2024.1.0 oder höher
- **Python**: 3.11 oder höher
- **Abhängigkeiten**: Keine externen Python-Pakete erforderlich

## Unterstützte Systeme

- Batteriespeicher mit SOC-Sensor
- PV-Anlagen mit Prognose-Entitäten
- Wechselrichter mit schaltbaren Ausgängen
- Kompatibel mit den meisten europäischen Energiesystemen

## Erweiterte Features

- **Simulation Engine**: Integrierte Simulationsmöglichkeiten für Testing
- **Flexible Konfiguration**: Anpassbare Parameter für verschiedene Systemgrößen
- **Robuste Fehlerbehandlung**: Graceful Handling von fehlenden Daten
- **Performance Optimiert**: Effiziente Algorithmen für Echtzeitbetrieb

## Support & Community

- **GitHub Issues**: [Probleme und Feature-Requests](https://github.com/danielr0815/battery-manager-ha/issues)
- **Dokumentation**: Vollständige Dokumentation im Repository
- **Community**: Aktive Entwicklung und Unterstützung

## Lizenz

MIT License - Siehe LICENSE-Datei für Details.
