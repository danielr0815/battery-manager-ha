# Deutsch und Englisch

Stand: v0.36.1.

## Befund

Die Kaskadenkarte übersetzte `recovering` mit „lädt Speicher“, obwohl der
Executor diesen Zustand auch ohne aktive Ladung beibehält. Feste englische
Kartentitel, fehlende Phasen- und Auswahltexte, englische Push-Nachrichten und
deutsche Exporttabellen führten zusätzlich zu einer gemischten Oberfläche.

## Regeln

- R1: Karten, Kartenauswahl und Karteneditor verwenden die Sprache des
  angemeldeten HA-Benutzers. Alle drei Karten aktualisieren ihre Texte bei
  einem Sprachwechsel auch dann, wenn der Sensorzustand unverändert bleibt.
  Deutsch und Englisch sind vollständig hinterlegt; andere Sprachen fallen
  auf Englisch zurück.
- R2: Konfiguration, Entitätszustände, Reparaturmeldungen und Aktionsfehler
  verwenden Home Assistants Übersetzungsschlüssel. Schlüssel und Platzhalter
  müssen in `translations/de.json` und `translations/en.json` übereinstimmen.
- R3: Push-Nachrichten, Download-Hinweise und lesbare Exporttabellen verwenden
  die HA-Systemsprache. Der Notify-Dienst liefert keine Sprache des Empfängers.
- R4: `recovering` bedeutet „Wiederaufladung ausstehend“ / „recharge pending“.
  Der Status belegt keine gemessene Ladeleistung. `complete` beschreibt einen
  abgeschlossenen Zyklus, nicht zwangsläufig ein gerade erreichtes Entladeziel.
- R5: Vom Benutzer vergebene Karten-, Geräte- und Entitätsnamen bleiben erhalten.
  Interne IDs, Zustands-/Fehlercodes, JSON-Exportfelder und Protokolle bleiben
  stabile technische Schnittstellen. Reparaturmeldungen erklären Fehler in der
  gewählten Sprache und führen technische Diagnosedetails ergänzend auf.

## Verifikation

`tests/frontend/cascade-card.test.mjs` prüft beide Sprachen, dieselben Sensordaten
beim Sprachwechsel, Kartenauswahl/Editor, alle Kaskadenphasen sowie eigene Titel.
`tests/ha/test_localization.py` lädt die Sprachdateien über HA, prüft das
Enum des Kaskadenstatus, Aktionsfehler, echte Notify-Aufrufe an einen Testdienst,
Exporttabellen und die vollständige Schlüssel-/Platzhaltersymmetrie.
Die vorhandenen Tests für Exportdienste und Kaskadenabläufe sichern die
unveränderte Funktionsweise ab.

Der Karteneditor nutzt die dokumentierten `computeLabel`-Callbacks:
[Home Assistant: Custom card](https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card/).
