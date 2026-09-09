# Kaskaden im Verbrauchslernen

## Befund (2026-09-09)

Die Bad-Kaskade B1 → B2 → Entfeuchter war an allen drei Messpunkten als
`in_house_measurement` konfiguriert. Der Lerner zog die durchgeleitete Energie
mehrfach ab. Am 08.09., 09–10 Uhr MESZ, standen rund 490 Wh Hausbilanz
454 + 451 + 451 Wh Abzügen gegenüber. Das negative Residuum wurde als 0 Wh
in das Abwesenheitsprofil übernommen; 09–15 Uhr wurden mit 0 W prognostiziert.

## Regeln

- **R1:** Pro linearer Kaskade wird nur der Eingang des ersten Mitglieds als
  Bereinigungsquelle verwendet. Sein `in_house_measurement` bestimmt, ob er
  im Hausmesskreis liegt. Nachgelagerte Mitglieder und die Endlast werden
  unabhängig von ihren eigenen Flags nicht nochmals abgezogen. Das gilt
  auch bei Versorgung aus einem Kaskadenakku ohne Root-Bezug.
- **R2:** Eigenständige Lasten und Appliances behalten ihre bisherigen Regeln.
  Ein leerer Kaskadenentwurf unterdrückt keine Bereinigungsquelle.
- **R3:** Lernregelversion 5 invalidiert den Cache der bereinigten Stunden.
  Beim Laden startet der vorhandene Catch-up-Pfad den Neuaufbau des gesamten
  konfigurierten Lernfensters aus verfügbarer Recorder-Historie. Die bisherigen
  Profile dämpfen diese Korrektur nicht. Änderungen der wirksamen
  Bereinigungsquellen durch Hinzufügen/Entfernen einer Kaskade ändern ebenfalls
  den Fingerprint. Fehlende historische Daten bleiben den bisherigen
  Vollständigkeits- und Fallbackregeln unterworfen.

Die Messpunkt-Flags werden nicht verändert. Der Planner-Kern und seine
Golden-Szenarien bleiben unverändert; reale Pläne verwenden die korrigierte
Verbrauchsprognose. Historische Topologieänderungen besitzen weiterhin keine
zeitlich versionierte Messpunktzuordnung: Der Neuaufbau verwendet die aktuelle
Konfiguration, wie die bisherige Bereinigung bei Konfigurationsänderungen.

## Regression

`test_cascade_learning_rebuilds_history_without_repeated_pass_through` in
`tests/ha/test_history_recorder.py` verwendet echte Recorder-Statistiken und
prüft Root innerhalb/außerhalb des Messkreises, Durchleitung, akkubetriebene
Endlast, unabhängige Last sowie den Neuaufbau nach Topologieänderung und aus
alter Lernregelversion. Aus fehlerhaften 0-W-Bins werden direkt 36-W-Bins.
