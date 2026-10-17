# Verbrauchsprofile bei knapper Messhistorie

## Befund

Am 17.10.2026 hatte das Live-System je AC-Wochenendstunde drei bis neun
gültige Vergleichstage. Die Mindestmenge von zehn ließ alle 24 Stunden auf
das Standardprofil zurückfallen. Werktags waren 21 Stunden nutzbar.
Nicht auflösbare Netzteilversorgung begrenzte den Nachschub; am 16.10. blieb
nur eine verwertbare Stunde. Fehlende Bereinigungsdaten sind weiterhin keine
Nullmessung und werden durch diese Änderung nicht repariert.

## Regeln ab 0.60.0

- **R1:** Jeder gültige, bereinigte Messwert darf zur P50-Schätzung beitragen.
  Tagesarten bleiben getrennt. Unterhalb der bisherigen Mindestmenge ist die
  Schätzung ausdrücklich vorläufig. Altersgewichtung, physische Begrenzung
  und Dämpfung gegenüber dem vorherigen Wert bleiben erhalten.
- **R2:** Ohne gültige Beobachtungen einer Stunde im Lernfenster bleibt deren
  bisherige P50-Schätzung erhalten, auch wenn die Rohdaten aus dem Fenster
  gefallen sind. Sie zählt nicht als neue Messung. Ohne bisherigen Wert gilt
  das Standardprofil. Auch eine gemessene Null bleibt eine gültige Schätzung.
- **R3:** P80 wird nur mit der bisherigen Mindestmenge neu berechnet. Ein altes
  Band wird bei knapper oder fehlender Evidenz nicht weitergeführt. Ohne ein
  einziges verfügbares Band verwendet die Planung den festen SOC-Puffer;
  gemischte Horizonte behalten den bestehenden Vertrag `measured_band_only`.
  Unbekannte Streuung wird in den Diagnosen ausdrücklich als unbekannt gezählt.
- **R4:** `bin_quality` kennzeichnet jede Stunde als `mature`, `provisional`,
  `retained` oder `fallback`. Die Karte zeigt dies neben den echten
  Vergleichstagen; die Mindestzahl bezeichnet jetzt die Bandreife. Die
  vorhandene Messfrische-Anzeige bleibt vom Berechnungszeitpunkt getrennt.
- **R5:** Beim ersten Start nach dem Update wird die vorhandene Historie neu
  aggregiert. Ein additiver Diagnosemarker verhindert erneute Upgrade-Läufe.
  Kein neuer Bereinigungszeitraum wird erfunden; Konfigurationsepochen,
  Ausschlüsse und AC-Gültigkeitsgrenzen bleiben erhalten. Explizite Reparatur
  verwirft weiterhin das betroffene alte Profil, damit es nicht zurückkehrt.

## Grenzen und Kompatibilität

Keine neue Konfiguration und kein inkompatibles Speicherformat. Standardmäßig
bleiben das Rohdatenfenster von 120 Tagen und die Gewichtshalbwertszeit von
30 Tagen bestehen. Die vorhandene Grenze von 14 Tagen seit erfolgreicher
Profilberechnung gilt weiterhin bei einem vollständig ausgefallenen Lernlauf;
sie ist keine Altersgrenze einzelner Messwerte. Quellenwechsel blockieren die
Planungsverwendung bis zum erfolgreichen Lernlauf nach bestehendem Vertrag.
Neue Messwerte können einen gehaltenen Wert wieder ablösen. Historische
Diagnosen vor dem Upgrade erhalten keine erfundene Qualitätsklassifikation.

Die Änderung beeinflusst aus Messdaten erzeugte Lastprognosen. Die bestehenden
Planner-Goldens mit expliziten Eingangslasten ändern sich dadurch nicht.

## Nachweise

`tests/core/test_load_profile.py`: knappe gewichtete Wochenendhistorie,
Verlust aller Stichproben, Wiederanlernen, Nullmessung und explizites Verwerfen.
`tests/ha/test_learning_evidence.py`: echter Lernlauf, Persistenz/Wiederladen,
Stichprobenqualität und einmalige Upgrade-Aggregation.
`tests/ha/test_dynamic_buffer.py`: vorläufige Last im Plan bei festem Puffer.
`tests/browser/cards.spec.mjs`: Qualitätszustände und Stichprobenzahlen in
deutscher und englischer Darstellung.
