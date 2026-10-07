# Lokaler Releasekandidat 0.56.0

Umfang: Reviewkorrekturen zur PV-/DC-Marktversorgung, Quellenbesitz,
Energiebilanz und Veröffentlichungsdokumentation. Stand 07.10.2026; keine
Veröffentlichung, Installation oder Live-Abnahme. Assistent, Erklärungstimeline,
anonymisierter Export und Kostenbericht bleiben separate Features.

## Fachliche Änderungen und Nachweise

- PV, Hauslast, gebuchte Lasten und tatsächliche Netzteilaufnahme teilen den
  saldierenden Anschluss. `psu_grid_import_wh` erfasst verbleibenden Netzteilbezug;
  alte Aufzeichnungen ohne das Feld bleiben lesbar. Ideale 200 Wh PV / 100 Wh
  Haus / 100 Wh DC liefern null Import/Export bei verschiedenen Quellen und SOC.
- Der marginale Quellenvergleich entfernt dominierte Kombinationen. Höhere Preise
  erweitern weder Entnahmebudget noch AC-Freigaben. Gemeinsame Nachbearbeitung
  gilt für Lastkandidaten, normale Planung, Vergleich und Replay.
- Planrevision/Ablaufzeit sichern Quellenaufträge nach Refresh und Sperrenwartezeit.
  Jede Bestätigung und der 24-V-Überlappungsschritt prüfen aktuelle Voraussetzungen.
  Batterieschutz wartet nicht auf wirtschaftliche Fristen. Auch eine verzögerte
  Inverterfreigabe wird bei entfallener Freigabe physisch zurückgenommen.
- Eine gemeinsame Aktorzuordnung prüft Lasten, Kaskaden und Versorgung vor
  Konfigurationsschreiben und Befehlen. Bestehende Konflikte erzeugen Reparaturen;
  reine Lastaktoren werden mit Gates/Outputs vor Inputs abgeschaltet. Versorgung
  behält ihren Besitzer und gültige Konfigurationsteile bleiben nutzbar.
- Prognose-Watt plus Puffer-Wh / eine feste Stunde ersetzen den uhrzeitabhängigen
  DC-Ersatzwert. Endliche Modellwerte und zulässige Energie/Dauer werden validiert;
  ungültige Replay-Dateien liefern einen verständlichen Fehler.
- Eine während der Suite reproduzierte sofortige Feed-in-Publikation erhält einen
  expliziten Schreibmarker: eigene Befehle werden vor Task-Zuweisung nicht als
  manuelle Eingriffe interpretiert.

Die Regressionen stehen in `tests/core/test_review_corrections.py`,
`tests/core/test_dc_pv_market.py`, `tests/ha/test_actor_ownership.py`,
`tests/ha/test_live_dc_pv.py` und den bestehenden Config-/Feed-in-Suiten.
Produktive Verzögerungen sind gemockt; Ereignisse und bestätigte Zustände bilden
die Synchronisationsgrenzen. Siehe [Testqualität](TEST_QUALITY_AUDIT.md).

## Nachtrag: Netzbezug trotz ausgeschaltetem Lade-Helfer

Am 07.10.2026 gegen 14:14 Uhr CEST zeigte die bestehende Live-Installation
0.55.2 rund 1950 W Netzbezug und 300 W Powerstation-Ladung bei Planempfehlung
und Lade-Helfer OFF. Der letzte Eingang-OFF war unbestätigt. Die externe
Automation schrieb einen Ladezielwert unter der vom Gerät gemeldeten Untergrenze;
ihre HA-Traces belegen `out_of_range`. Ein Helfer-OFF bedeutete hier keine
physische Ladeunterbrechung. Der Eingang wurde gezielt ausgeschaltet; um 14:16
und erneut um 14:23 Uhr waren Eingang OFF und beide Eingangsmessungen 0 W.
Das ist eine Sofortmaßnahme am bisherigen Stand, keine Live-Abnahme von 0.56.0.
Die externe Automation wurde nicht umgeschrieben; ihr ungültiger Zielwert muss
durch einen tatsächlich wirksamen Lade-Stopp ersetzt werden.

Die lokale Korrektur verfolgt einen eigenen unvollständigen Eingang-Stopp auch
bei Helfer-OFF weiter. Normalplanung und Fünfsekundentakt nutzen dieselben
bestätigten Aktoren mit begrenzten Wiederholungen. Zusätzlich sperrt frischer
gemessener Netzbezug über 50 W automatische Starts und beendet Zusatzlasten
ohne Mindestlaufzeit-Warten. Freigaben werden nach jeder Bestätigung neu gelesen;
Fremd-Passthrough, Kalibrierung und Kaskadenbesitz bleiben erhalten.
Der Ist-Schutz braucht die vorhandene optionale Zuordnung des Netzleistungssensors
(`live_ac_grid_power_entity` oder `operation_import_power_entity`); sie war in
der untersuchten Live-Konfiguration noch leer. Es wird kein Gerät aus einem
fest codierten privaten Entity-Namen erkannt. Details und Grenzen stehen in
[LOAD_CONTROL](LOAD_CONTROL.md#measured-import-and-interrupted-stops-0560).
Regressionen: `tests/ha/test_load_grid_safety.py`; fehlgeschlagene Dienste und
fehlende Bestätigungen werden getrennt geprüft, Wiederholungszeit ist virtuell.

## Begründete Golden-Änderungen

Vier von 16 Szenarien ändern sich. Alle anderen bleiben identisch. Werte stammen
von `scripts/gen_golden.py`; Referenz ist der zuvor eingefrorene Golden-Stand.

| Szenario | Import vorher → nachher (kWh) | Export vorher → nachher (kWh) | Grund |
| --- | --- | --- | --- |
| `coordinated_low_reserve` | 4,966367 → 4,967116 | 0 → 0 | PV, die bereits ein 24-V-Netzteil speist, kann nicht gleichzeitig die Batterie laden. Die reale Quellen-/Schutzfolge erreicht dadurch früher die Batterieuntergrenze. Der Mehrbezug von 0,749 Wh folgt aus der korrigierten Schutz-/Verlustbilanz; er ist keine preisbedingte Budgeterweiterung. |
| `coordinated_sunny_recovery` | 0,209345 → 0,190618 | 12,656973 → 12,638247 | Verfügbare PV deckt die Netzteilaufnahme am Anschluss. Derselbe Betrag wird nicht mehr gleichzeitig importiert und exportiert. |
| `forced_dc24` | 4,600000 → 4,340000 | 0 → 0 | Manuell gehaltene 24-V-Versorgung nutzt vorhandene PV. Diese PV wird nicht zusätzlich als Batterieladung verbucht; SOC-Kurve und Verlustverteilung ändern sich. |
| `reserve_sunny_winter` | 2,848221 → 2,767416 | 0 → 0 | Physisch saldierter Netzteilbezug ändert DC-Erhaltung und Reservevergleich. Schutzgrenzen bleiben erhalten; das Maximum liegt jetzt in Slot 19 statt 21. |

Die geringeren SOC-Maxima sind eine Folge entfallener doppelter PV-Gutschriften.
Importsteigerungen werden ausdrücklich begründet; Goldens sind keine Behauptung
gleichbleibender Energie bei einer fehlerhaften alten Bilanz.

## Vollständige Planner-Replays

`scripts/benchmark_planner.py` führt vollständige `plan`-Aufrufe mit frischem,
aufruflokalem Cache aus. Referenz ist die vor den Reviewkorrekturen gesicherte
lokale 0.56.0-Implementierung, nicht ein anderer Tarif oder eine andere Prognose.
Je Fall werden drei Läufe, Median, physische Schritte, angefragte Schritte,
Policy-Läufe, Import, Export, DC-Versorgung, AC-Abgabe und Endenergie berichtet.
Geräteidentitäten und Rohaufzeichnungen bleiben lokal.

Linux-Entwicklungsumgebung, Python 3.14.7. Der vollständige Fall enthält 65
Slots, drei Zusatzlasten und zwei Geräteprogramme; die öffentliche Oktober-Fixture
enthält dieselben 65 Grundlast-/PV-Slots ohne optionale Lasten und Programme.
Zeiten sind lokale Messwerte, keine hardwareunabhängige Grenze. Alle drei Läufe
jedes Standes liefern identische physische Ergebnisse und Arbeitszahlen.

| Fall | Median (s) | Policy-Läufe | Schrittanfragen | Physische Schritte | Import (Wh) | Export (Wh) | DC-Ausfall (Wh) | Endenergie (Wh) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Vollständiger Oktoberplan — vorher | 30.4746 | 680 | 3164600 | 1815820 | 5744.954 | 0.000 | 0.000 | 3559.529 |
| Vollständiger Oktoberplan — korrigiert | 37.9208 | 682 | 3173622 | 1821546 | 5702.033 | 0.000 | 0.000 | 3521.226 |
| Vollständiger Oktoberplan — optimiert | 37.1640 | 682 | 3173622 | 1821546 | 5702.033 | 0.000 | 0.000 | 3521.226 |
| Oktober-Fixture — vorher | 0.3022 | 6 | 27988 | 15235 | 5549.388 | 0.000 | 0.000 | 3354.779 |
| Oktober-Fixture — korrigiert | 0.3444 | 6 | 28012 | 15259 | 5549.304 | 0.000 | 0.000 | 3358.738 |
| Oktober-Fixture — optimiert | 0.3340 | 6 | 28012 | 15259 | 5549.304 | 0.000 | 0.000 | 3358.738 |

DC-Versorgung bleibt jeweils 4545,270 Wh; AC-Abgabe im vollständigen Fall
532,715 Wh und in der Fixture 589,924 Wh. Die korrigierte Anschlussbilanz senkt
Import um 42,921 Wh im vollständigen Fall; die Endenergie sinkt dabei um
38,303 Wh, weil zuvor bereits zur Netzteilversorgung verwendete PV nochmals
Batterieladung erhielt. Diese Versionsdifferenz ist eine Physikkorrektur;
Marktverschiebungen werden gegen ihre jeweils physisch korrigierte Referenz auf
Budgetneutralität und Endenergie geprüft. Die Fixture verbessert Endenergie um
3,959 Wh und vermindert Import um 0,084 Wh. Keine DC-Unterversorgung und kein
zusätzlicher Export entstehen.

Die Optimierung wertet Batterie-/Netzkosten je Kombination einmal aus und
verwendet bereits berechnete Netzteil-AC-Aufnahme wieder. Ein Profil der Fixture
zeigte wiederholte Quellenbewertung und physische Schritte im betroffenen Pfad.
Die Zahl der `net_draw`-Aufrufe sinkt in diesem Profil von 40.780 auf 18.488,
`grid_dc` von 28.916 auf 10.136, bei identischen physikalischen Ergebnissen.
Der Median sinkt gegenüber dem korrigierten Zwischenstand um
2.0 % im vollständigen Fall und
3.0 % in der Fixture; alle Ergebnisse
und Arbeitszahlen bleiben exakt gleich. Die Zeitstreuung erlaubt keine starke
Aussage über andere Rechner. Gegenüber dem Ausgangsstand kostet die vollständige
Korrektur weiterhin rund 22.0 % mehr
Laufzeit. Es wird kein allgemeiner Geschwindigkeitsgewinn behauptet.

Drei vollständige Laufzeiten (s): vorher 29.973, 30.475, 31.691; korrigiert 37.578, 37.921, 39.723; optimiert 35.965, 37.164, 38.354.
Die Wiederverwendung reduziert wiederholte Kostenrechnungen, ohne zusätzliche
physische Berechnungen oder Cache-Lebensdauer. Kernregressionen und Golden-Gates
bestehen nach der Optimierung unverändert.


Reproduzierbarer Aufruf:

```bash
uv run python scripts/benchmark_planner.py recording.json --repeats 3
uv run python scripts/benchmark_planner.py recording.json --core-dir /path/to/baseline/core --repeats 3
```

## Releaseinhalt und Auslieferung

Manifest, Projekt und Lockfile bleiben 0.56.0; das Kartenbundle wird aus `frontend/`
gebaut und eingecheckt. Der neue Featurevertrag und dieser Bericht sind Teil des
vorgesehenen Repository-Releases. Neue Links verweisen auf vorhandene Dokumente.
Der vorhandene lokale 0.49.0-Leitfaden und seine interaktiven Diagramme bleiben
als historischer Nutzerinhalt im Arbeitsverzeichnis erhalten. Sie gehören nicht
zu diesem Release; Verweise auf diese lokalen Dateien werden nicht mit ausgeliefert.
Lokale Rohanalysen und Aufzeichnungen werden nicht pauschal aufgenommen.

SECURITY beschreibt Korrekturen über veröffentlichte Releases/HACS. Der aktuelle
Betriebsleitfaden verwendet neutrale Beispiele und erklärt Verbrauchsdaten in
Diagnosen. Die HACS-Prüfung enthält keinen `brands`-Ignore mehr. Remote-HACS- und
hassfest-Jobs bleiben Teil der GitHub-Releasevalidierung; sie werden durch lokale
Tests nicht als bereits ausgeführte Remote-Prüfungen dargestellt.

## Abschließende lokale Gates

- Vollständige Python-/HA-Suite einschließlich Netzbezugs-Nachtrag:
  **2711 bestanden**, 193,52 s; Gesamt-Coverage **97,89 %**, Kern **100 %**,
  alle **65 Module** über ihren Gates (HA ≥ 95 %).
  Regulärer Befehl: `uv run pytest tests -n4 --dist=loadscope --cov --cov-report=json`.
- Separate HA-freie Kern-Suite: **758 bestanden**, 100 % Coverage einschließlich
  aller neuen Budget-, Physik-, Modell- und Replay-Pfade.
- Ruff Check und Format-Check, mypy über die komplette Integration, Modul-Gates,
  `uv sync --locked --group dev` und Release-Metadatenprüfung bestehen.
- `npm ci`, Build, Bundle-Abgleich, ESLint/Prettier, **80 Frontendtests** und
  **41 lokale Browsertests** bestehen. Manifest, pyproject und Lockfile sind 0.56.0;
  Karten-Version wird weiterhin aus dem Manifest abgeleitet.
- Neue Dokumentlinks wurden lokal auf vorhandene Ziele geprüft. Private Rohdaten
  und Untersuchungen bleiben außerhalb des vorgesehenen neuen Releaseinhalts.

Ein während der Abnahme eingeführter Test-Uhrpatch wurde wegen falscher
verschachtelter Wiederherstellung korrigiert. Quellen- und Archivtests bestehen
auch gemeinsam in einem Prozess; produktive Übergabezeiten werden weiter gemockt.
Ein zusätzlicher Diagnose-Timeout war für einen mehrtägigen CPU-Replay zu knapp;
der reguläre vollständige Lauf besteht ohne diesen Zusatz. Es wurden keine
Coverage-Gates oder Fachtests abgeschwächt.

Der Stand ist lokal geprüft. Öffentliche GitHub-/HACS-Validierung, Veröffentlichung,
HACS-Installation und anschließende Abnahme an der Live-Anlage folgen separat.
