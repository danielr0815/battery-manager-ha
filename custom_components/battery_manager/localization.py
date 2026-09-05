"""Text for notifications without a frontend user context.

Cards and HA entity/action translations follow the user's UI language. Push
notifications have no recipient locale, so use HA's configured system language.
Keep protocol keys, user-assigned names and diagnostic exports unchanged.
"""

from homeassistant.core import HomeAssistant

MESSAGES = {
    "data_lost_title": (
        "⚠️ Battery Manager: data loss — loads switched off",
        "⚠️ Battery Manager: Datenausfall — Lasten ausgeschaltet",
    ),
    "data_lost": (
        "No valid battery SOC or PV forecast data for {hours}+ hours. All controlled surplus loads were force-switched off (fail-safe); they resume automatically once data returns.",
        "Seit mindestens {hours} Stunden fehlen gültige Batterie-SOC- oder PV-Prognosedaten. Alle gesteuerten Überschusslasten wurden zur Sicherheit ausgeschaltet. Sobald wieder Daten vorliegen, werden sie automatisch gemäß Planung gesteuert.",
    ),
    "data_recovered_title": (
        "✅ Battery Manager: data recovered",
        "✅ Battery Manager: Daten wieder verfügbar",
    ),
    "data_recovered": (
        "Valid battery SOC and PV forecast data are back; the fail-safe load shed has ended and normal planning resumes.",
        "Gültige Batterie-SOC- und PV-Prognosedaten sind wieder verfügbar. Die Sicherheitsabschaltung ist beendet; die normale Planung wird fortgesetzt.",
    ),
    "power_warning_title": ("⚠️ {name}: power warning", "⚠️ {name}: Leistungswarnung"),
    "power_warning": (
        "{name} draws {raw:.0f} W but {nominal:.0f} W are configured (sustained over {dwell:g} min). Check the device — full water tank, wrong configured power, or a foreign consumer.",
        "{name} nimmt {raw:.0f} W auf, konfiguriert sind {nominal:.0f} W (seit {dwell:g} min). Bitte das Gerät prüfen: voller Wassertank, falsch konfigurierte Leistung oder ein fremder Verbraucher.",
    ),
    "power_resolved_title": (
        "✅ {name}: power warning cleared",
        "✅ {name}: Leistungswarnung aufgehoben",
    ),
    "power_resolved": (
        "{name} draws its configured power again.",
        "{name} nimmt wieder die konfigurierte Leistung auf.",
    ),
    "tank_title": ("🪣 {name}: tank nearly full", "🪣 {name}: Tank fast voll"),
    "tank": (
        "{name}: tank likely full within {minutes} min of runtime — please empty it.",
        "{name}: Der Tank ist voraussichtlich innerhalb von {minutes} min Laufzeit voll. Bitte leeren.",
    ),
    "support_title": (
        "⚠️ Battery Manager: support switching failing",
        "⚠️ Battery Manager: Netzstützung lässt sich nicht schalten",
    ),
    "support": (
        "The support actuators (48 V PSU / 24 V rail) failed {count} times in a row — service call failed or switchover not confirmed. Check the configured switches; the 5-minute cycle keeps retrying.",
        "Die Schaltgeräte der Netzstützung (48-V-Netzteil / 24-V-Versorgung) haben {count} Mal in Folge nicht reagiert: Der Aktionsaufruf schlug fehl oder die Umschaltung wurde nicht bestätigt. Bitte die konfigurierten Schalter prüfen. Im 5-Minuten-Takt wird es erneut versucht.",
    ),
    "download": (
        "[Download {name}]({url}) — the file is reachable under /local/ without login and is deleted automatically after 1 hour.",
        "[Datei herunterladen: {name}]({url}) — die Datei ist unter /local/ ohne Anmeldung erreichbar und wird nach einer Stunde automatisch gelöscht.",
    ),
    "export_title": ("Battery Manager Export", "Battery Manager Export"),
}


def message(hass: HomeAssistant, key: str, **values: object) -> str:
    """Format a notification in the system language, falling back to English."""
    language = (hass.config.language or "en").lower().replace("_", "-").split("-")[0]
    return MESSAGES[key][language == "de"].format(**values)
