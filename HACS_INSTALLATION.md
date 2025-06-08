# HACS Installation Guide

## Schritt-für-Schritt Anleitung zur Installation über HACS

### Voraussetzungen
- Home Assistant Core 2024.1.0 oder höher
- HACS bereits installiert und konfiguriert

### Installation

#### 1. Custom Repository hinzufügen
1. Öffnen Sie HACS in Home Assistant
2. Klicken Sie auf **"Integrationen"**
3. Klicken Sie auf die **drei Punkte** in der oberen rechten Ecke
4. Wählen Sie **"Benutzerdefinierte Repositories"**
5. Fügen Sie diese URL hinzu: `https://github.com/danielr0815/battery-manager-ha`
6. Wählen Sie Kategorie: **"Integration"**
7. Klicken Sie **"Hinzufügen"**

#### 2. Integration installieren
1. Suchen Sie in HACS nach **"Battery Manager"**
2. Klicken Sie auf **"Herunterladen"**
3. Starten Sie Home Assistant neu

#### 3. Integration konfigurieren
1. Gehen Sie zu **Einstellungen** → **Geräte & Dienste**
2. Klicken Sie **"+ INTEGRATION HINZUFÜGEN"**
3. Suchen Sie nach **"Battery Manager"**
4. Folgen Sie den Konfigurationsschritten

### Benötigte Entitäten

Für die Konfiguration benötigen Sie folgende Entitäten in Home Assistant:

- **SOC Sensor**: Aktueller Batterieladezustand (z.B. `sensor.battery_soc`)
- **PV Prognose Heute**: Tagesprognose für heute (z.B. `sensor.pv_forecast_today`)
- **PV Prognose Morgen**: Tagesprognose für morgen (z.B. `sensor.pv_forecast_tomorrow`)
- **PV Prognose Übermorgen**: Tagesprognose für übermorgen (z.B. `sensor.pv_forecast_day_after`)

### Fehlerbehebung

#### Problem: Integration nicht gefunden
- Stellen Sie sicher, dass HACS korrekt installiert ist
- Überprüfen Sie die Repository-URL
- Starten Sie Home Assistant neu

#### Problem: Konfiguration schlägt fehl
- Überprüfen Sie, ob alle benötigten Entitäten existieren
- Kontrollieren Sie die Entitäts-IDs in den Entwicklertools

#### Problem: Sensoren zeigen keine Daten
- Überprüfen Sie die Logs unter Einstellungen → System → Protokolle
- Stellen Sie sicher, dass die PV-Prognose-Entitäten gültige Werte liefern

### Support

Bei Problemen erstellen Sie bitte ein Issue im [GitHub Repository](https://github.com/danielr0815/battery-manager-ha/issues).
