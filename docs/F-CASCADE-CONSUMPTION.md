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
- **R3:** Relevante Konfigurationsänderungen öffnen einen gespeicherten
  Zeitabschnitt mit Beginn, Messquellen und Bereinigungsregeln. Beim Reload
  wird der neue Stand vor dem Recorder-Zugriff erfasst. Schon bereinigte
  Stunden und ihre Tagestypen bleiben unverändert. Noch ausstehende Stunden
  werden nur mit dem damals gültigen Stand ausgewertet; eine Stunde, in der
  der Stand wechselt, bleibt ungelernt. Alte Abschnitte werden außerhalb des
  Lernfensters entfernt, wobei der am Fensterbeginn gültige Stand erhalten
  bleibt. Ein unveränderter Stand erzeugt keinen weiteren Abschnitt.
- **R4:** Nur die explizite Aktion `repair_consumption_history` mit `since`
  darf AC-Tageswerte ab einem bestätigten Datum neu bereinigen. Sie setzt die
  Bestätigung voraus, dass die aktuelle Verkabelung und Messpunktzuordnung
  seitdem unverändert sind. Frühere Tage, DC-Werte und bereits gespeicherte
  Tagestypen bleiben erhalten. Originalmesswerte im Recorder werden nie
  verändert. Fehler, Abbruch und ein Zeitraum ohne verwendbare AC-Stunden
  lassen den bisherigen Lernstand bestehen. Der neue AC-Stand wird nach
  Erfolg ohne Dämpfung durch bekannte fehlerhafte Werte übernommen und die
  Prognose aktualisiert.
- **R5:** Das Upgrade besitzt keine rückwirkende Konfigurationshistorie.
  Bestehende Tageswerte bleiben erhalten. Bei konfigurierter Kaskade werden
  alte AC-Werte als unsicher vom aktiven Lernen ausgeschlossen; das normale
  Fallback gilt bis neue oder ausdrücklich reparierte Werte verfügbar sind.
  Ohne alte Profile kann die Ersteinrichtung mit der vom Betreiber gewählten
  Konfiguration das initiale Lernfenster einlesen. Danach wird ein erweitertes
  Lernfenster nicht mit einer unbekannten früheren Verkabelung aufgefüllt.

## Bestätigte Reparatur der Referenzanlage

Der Betreiber hat am 09.09.2026 bestätigt, dass die aktuelle Kaskade seit dem
Beginn der laufenden Urlaubsperiode unverändert ist. Recorder: Urlaubsmodus
am 29.08.2026 um 22:08:34 MESZ eingeschaltet. Der erste vollständige Tag ist
**30.08.2026**. Für diese Anlage ist daher `since: "2026-08-30"` der bestätigte
Reparaturbeginn. Das Datum ist kein fest verdrahteter Produktstandard.

## Regression

Die Recorder-Tests `test_cascade_learning_preserves_historical_attribution`,
`test_explicit_repair_only_changes_confirmed_ac_days`,
`test_repair_failure_preserves_previous_state` und
`test_failed_second_epoch_retries_whole_missing_day` prüfen die realen
Statistikabfragen, Konfigurationswechsel in einem noch ungelernten Tag,
mehrfache Durchleitung, Akkubetrieb, den unveränderten Cache, explizite
Reparatur, Persistenz und Abbruch. Weitere Tests decken Migration,
Aktionsvalidierung und die Veröffentlichung der neuen Prognose ab.
