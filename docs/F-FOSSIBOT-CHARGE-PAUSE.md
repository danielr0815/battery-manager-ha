# Fossibot-Ladepause bei erhaltenem AC-Passthrough

Vertrag ab 0.59.2. Der Blueprint ist ein Geräteadapter für die bestehenden
Lade-Helfer. Die Planung und Freigabe bleiben beim Battery Manager; eine neue
Planungs- oder Schaltengine wird nicht eingeführt.

## Anlass und geprüfter Gerätebefehl

Die bisherige Ladesteuerung setzte die maximale SOC-Grenze zum Stoppen auf
20 % und zum Laden auf 90 %. Die am 14.10.2026 installierte Fossibot-Integration
3.0.1 akzeptiert für diese Zahl nur 60–100 %. Ein 20-%-Befehl scheitert deshalb
mit `out_of_range`. Auch die gültige Grenze 60 % stoppt keine darunterliegende
Batterie. Ein ausgeschalteter Helfer konnte somit neben weiterlaufender Ladung
stehen.

Die Number-Entity mit Entity-ID-Suffix `stop_charge_after` heißt im Gerät
beziehungsweise in der Oberfläche auch „Delay Charge“. Der Name darf nicht
als spätere Abschaltung missverstanden werden: Ein positiver Wert verzögert
den Ladestart; null hebt die Verzögerung auf. Der Adapter verwendet die am
Gerät geprüften Werte **10 Minuten** und **0 Minuten**. Die größere von der
Entity angebotene Obergrenze ist kein Nachweis getesteten Geräteverhaltens.

Bei der Live-Prüfung am 14.10.2026 blieb am F2400-B2 ein angeschlossener
Verbraucher während der Ladepause versorgt:

| Einstellung | Eingang / Ausgang laut Fossibot | Ladeanzeige |
|---|---|---|
| Ladeverzögerung 10 Minuten | 76 W / 76 W | Netz- und Batterieladung aus |
| Ladeverzögerung wieder 0 Minuten | 357 W / 53 W | Ladung wieder aktiv |

Während der Pause lag die AC-Ausgangsspannung bei rund 223 V; der externe
Stecker meldete rund 93 W einschließlich Eigenverbrauch. Diese Beobachtung
belegt Passthrough bei pausierter Batterieladung für B2. HA-Telemetrie kann
keine Unterbrechungsfreiheit im Millisekundenbereich nachweisen. Dieser Test
ist kein eigener Hardware-Nachweis für F2400-B.

## Installation und Zuordnung

Quelle: [fossibot_charge_pause.yaml](../blueprints/automation/battery_manager/fossibot_charge_pause.yaml).
Den Blueprint separat unter
`config/blueprints/automation/battery_manager/fossibot_charge_pause.yaml`
installieren und pro Fossibot eine Automation daraus anlegen. Ein Update der
Custom-Integration ersetzt bestehende Benutzerautomationen nicht automatisch.

Am 14.10.2026 wurden beide bestehenden Ladesteuerungen im Referenzsystem auf
diesen Blueprint umgestellt und beide Eingangsregeln auf `keep_on` gesetzt.
Die automatische Erneuerung an B2 wurde um 22:36 Uhr MESZ am Gerät bestätigt
(Resttimer 9 → 10 Minuten, AC-Ausgang weiterhin eingeschaltet). Bei dieser
Wiederholungsprüfung nahm der angeschlossene Verbraucher gerade keine Leistung
ab; der Lastnachweis stammt aus der oben dokumentierten vorherigen Prüfung.
Alle vorübergehenden Testschaltungen wurden anschließend zurückgesetzt.
[Vorherige Konfiguration](investigations/2026-10-14-fossibot-charge-pause-before.json)
und [Deployment-Nachweis](investigations/2026-10-14-fossibot-charge-pause-deployment.json)
sind für eine spätere Prüfung gesichert.

| Blueprint-Eingabe | Aufgabe |
|---|---|
| `charge_enable` | Bisheriger Lade-Helfer, etwa `input_boolean.charge_f2400_b2` |
| `input_switch` | Externer Eingangsstecker; wird ausschließlich gelesen |
| `charge_delay` | Fossibot-Number-Entity mit Suffix `stop_charge_after` |
| `grid_charging` | Geräte-Binary für gemeldete Netzladung; dient als Abgleich-Trigger und Rückmeldung für eine fehlgeschlagene Pause |

Je Last im Battery Manager den bisherigen Lade-Helfer beibehalten und die
Eingangsregel **`keep_on`** wählen, wenn Netz-Passthrough beim Ladestopp
erhalten bleiben soll. Die bisherige Automation mit den 20-%-/90-%-Befehlen
ersetzen oder deaktivieren, damit nur ein Adapter den Ladestopp umsetzt.
Die unterstützte maximale SOC-Grenze kann als reguläre Ladezielbegrenzung
bestehen bleiben; der Blueprint verändert sie nicht.

`keep_on` bedeutet: Ein eingeschalteter Eingang wird beim Ladestopp nicht
abgeschaltet. Die Einstellung schaltet keinen ausgeschalteten Stecker von
sich aus ein. Der normale geplante Ladestart kann den Eingang weiterhin
einschalten und damit das offline befindliche Gerät aufwecken. Der Adapter
aktiviert weder den AC-Ausgang noch den Eingangsstecker.

## Laufender Abgleich

Ein Schreibvorgang ist nur zulässig, wenn der Eingangsstecker exakt `on`
meldet und der Timer numerisch verfügbar ist. Damit wird ein ausgeschaltetes
Gerät nicht allein für eine Ladepause aufgeweckt. Sobald die Bedingungen
erfüllt sind, gilt:

| Aktueller Lade-Helfer | Timerbefehl |
|---|---|
| exakt `on` | `0`: Ladestart erlauben |
| `off`, `unknown`, `unavailable` oder fehlend | `10`: Ladestart verzögern |

Der Wert wird unmittelbar beim Serviceaufruf aus dem aktuellen Helferzustand
ermittelt. Die Automation arbeitet mit `mode: queued`, maximal fünf Läufen,
und ohne längere Wartephasen. Ein bereits laufender Gerätebefehl wird nicht
durch einen neuen Trigger abgebrochen; wartende Läufe verwenden den dann
aktuellen Wunsch statt eines alten Triggerzustands.

Der Abgleich läuft bei Änderungen des Lade-Helfers, beim Einschalten des
Eingangssteckers, nach HA-Start und Automations-Neuladen, bei Rückkehr des
Timers aus `unknown`/`unavailable`, bei Beginn oder Ende gemeldeter Netzladung
und zusätzlich alle zwei Minuten. Das Ausschalten des Eingangs löst ebenfalls
einen Abgleich der Fehlermeldung aus, aber keinen Timerbefehl. Der periodische
Lauf schreibt den Timer erneut, auch wenn dessen angezeigter Wert unverändert
ist. Ein gecachter Countdown darf die
physische Erneuerung nicht verhindern. Normale Countdown-Änderungen sind
kein Trigger und erzeugen keine Schleife durch die eigenen Schreibvorgänge.

Der vorhandene BM-Startpfad setzt zunächst den Lade-Helfer und dann den
Eingang. Ist die Fossibot noch offline, kann der erste Timerbefehl ausfallen;
die Rückkehr der Geräte-Entity und der periodische Abgleich setzen die
Freigabe anschließend um. Beim Aufwachen kann das Gerät vor dem ersten
erfolgreichen Befehl kurz laden.

## Manuelle Bedienung und Rückmeldung

Der Lade-Helfer bleibt bedienbar: `off` fordert eine erneuerbare Ladepause an;
`on` erlaubt das Laden bei bereits eingeschaltetem Eingang. Der Helfer
schaltet keinen AC-Ausgang ein und hebt das konfigurierte SOC-Ziel des
Gerätes nicht auf. Solange die BM-Steuerung der Last aktiv ist, kann der
nächste Planungslauf eine manuelle Änderung wieder überschreiben.

Für eine dauerhafte manuelle Pause die BM-Steuerung dieser Last ausschalten
und den Lade-Helfer ausschalten. Die BM-Pause verhindert neue Starts; ihr
normaler Stopppfad berücksichtigt eine noch laufende Mindestlaufzeit. Das
zusätzliche manuelle Ausschalten des Lade-Helfers fordert die Ladepause direkt
beim Adapter an. Ein ausgeschalteter BM-Schalter ist kein allgemeiner
Hands-off-Modus: vorhandene Stopplogik und Schutzregeln bleiben aktiv.

Ein bestätigter Helferzustand oder Timerwert beweist keinen physisch erfolgten
Ladestopp. Die gemeldete Netz-/Batterieladung und die tatsächliche Leistung
müssen dazu passen. `total_input` enthält auch Passthrough-Leistung und ist
allein kein Nachweis von Batterieladung. Eingangs- und Ausgangsleistung
zusammen erlauben eine Plausibilitätsprüfung, wobei versetzte oder gecachte
Gerätemeldungen zu berücksichtigen sind.

Bleibt bei eingeschaltetem Eingang die Netzlade-Rückmeldung trotz gesperrter
Freigabe zwei Minuten durchgehend `on`, erzeugt der Adapter eine dauerhafte
HA-Meldung „Fossibot-Ladepause nicht bestätigt“. Dies gilt auch dann, wenn der
Timer inzwischen nicht verfügbar ist. Besteht der Zustand bereits beim
Neustart oder Neuladen, prüft ihn der periodische Abgleich anhand der aktuellen
Zustandswechselzeiten. Auch ein wartender Fehlertrigger darf eine inzwischen
neu begonnene Zweiminutenfrist nicht überspringen. Der betreffende Lauf versucht
die Pause erneut, sofern der Timer erreichbar ist. Ein erfolgreicher Schreibvorgang
allein löscht die Meldung nicht: Dazu muss entweder das Laden wieder erlaubt
sein, die Netzladung `off` melden oder der Eingangsstecker `off` bestätigen.
Eine unbekannte Rückmeldung wird nicht als bestätigter Ladestopp gewertet.

Fehlgeschlagene Serviceaufrufe sind außerdem in der HA-Automationsspur sichtbar.
Der Zweiminutentakt versucht den Abgleich erneut. Die Automation nimmt einen
Fehler nicht zum Anlass, den Eingangsstecker oder AC-Ausgang abzuschalten.
Sie setzt keine SOC-Werte und löscht keine G2-/F4-Sperre des Battery Managers.

Die begleitende BM-Korrektur in 0.59.2 verhindert neue F4-Freeze-Evidenz, wenn
das konfigurierte Lade-Gate bestätigt `off` ist. Eine konstante positive
Passthrough-Leistung darf in dieser Pause nicht als eingefrorene Ladung zählen.
Eine bereits gesetzte Sperre aus positiver Ladeleistung wird dadurch nicht
gelöscht; auch G2 bleibt unabhängig erhalten. Diese Python-Korrektur benötigt
das reguläre Integrationsupdate, während eine installierte Adapter-Automation
bereits mit dem bestehenden Lade-Helfer funktioniert.

## Testnachweis

[`tests/ha/test_fossibot_charge_pause.py`](../tests/ha/test_fossibot_charge_pause.py)
führt den Blueprint über die HA-Automationsengine aus: explizite Freigabe,
Pause, unveränderte Timer-Rückmeldung, Geräte-Wiederkehr, Reload und
fehlgeschlagene Gerätebefehle werden an den tatsächlich ausgelösten Services
geprüft. Produktionsintervalle werden mit virtueller Zeit geprüft. Netzstecker,
AC-Ausgang und maximale SOC-Grenze bleiben als Hardware-Vertrag unverändert.

Die ergänzenden F4-Regressionen liegen in
[`tests/ha/test_load_switching.py`](../tests/ha/test_load_switching.py).

## Ausfallgrenze

Die Ladepause ist ein erneuerbarer Gerätetimer, kein unbefristeter
Hardware-Ladestopp. Bei regulärem Zweiminutentakt verbleiben zwischen zwei
erfolgreichen Erneuerungen ungefähr acht bis zehn Minuten. Ist Home Assistant
oder die Verbindung zum Gerät länger unterbrochen, kann der Timer ablaufen
und das Gerät wieder laden. Auch das Deaktivieren der Adapter-Automation
beendet weitere Erneuerungen. Ihre letzte Pause läuft dann aus.

Der Adapter kann bei fehlender Verbindung weder die Pause verlängern noch
deren Erfolg bestätigen. Eine Abschaltung des Eingangs als Ersatz würde den
geforderten Netz-Passthrough beenden und ist deshalb kein Bestandteil dieses
Vertrags.
