# Spezifikation für 24-V-Quellenwechsel und Einspeise-Nullbefehl

Arbeitsstand vom 28. November 2026. Die Anforderungen beschreiben das
beobachtbare Sollverhalten und die Abnahme. Design und Implementierung
folgen als eigene Arbeitsschritte.

## 24-V-Quellenwechsel

**Anlass.** In einer lokalen Home-Assistant-Gegenprobe zeigte das
24-V-Netzteil nach dem Einschaltauftrag `on` mit `assumed_state`. Der
Auftrag stand noch aus. Der Battery Manager meldete die Umschaltung als
abgeschlossen und schaltete den bis dahin versorgenden DC/DC-Wandler ab.
Die Gegenprobe belegt einen Softwarefehler. Ein Ausfall der realen
24-V-Schiene wurde dadurch nicht nachgewiesen.

**Geltungsbereich.** Automatische Umschaltungen in beide Richtungen zwischen
24-V-Netzteil und DC/DC-Wandler im koordinierten Betrieb, einschließlich
Netzausfall, Netzrückkehr, Wiederanlauf und Rückgabe aus dem Handbetrieb.
Der bestehende Hardwarevertrag erlaubt dabei parallelen Betrieb während
der Überlappung. Bei der dokumentierten Anlage hat der DC/DC-Wandler mit
24,3 V Vorrang vor dem 24,05-V-Netzteil. Ein bestätigtes eingeschaltetes
Netzteil belegt daher seine Schaltbereitschaft. Die erfolgte Lastübernahme
ist erst nach dem bestätigten Abschalten der bisherigen Quelle ableitbar.
Ein separater physischer Leistungsnachweis braucht die entsprechenden
Messdaten.

| ID | Anforderung |
|---|---|
| **24V-01** | Die bisherige Quelle bleibt eingeschaltet, bis der tatsächliche Ein-Zustand der Zielquelle belastbar bestätigt ist. Bei einem neu erteilten Einschaltauftrag muss der Nachweis zur aktuellen Umschaltung gehören. Diensterfolg, ein ausstehender Auftrag, `unknown`, `unavailable`, `assumed_state`, ein wiederhergestellter Zustand und eine veraltete oder nicht mehr passende Rückmeldung genügen jeweils nicht. Eine bereits eingeschaltete Zielquelle darf verwendet werden, wenn ihr bestätigter Zustand für diese Umschaltung belastbar ist und kein offener oder unklarer Ausschaltauftrag ihn entwertet. |
| **24V-02** | Die vollständige konfigurierte Überlappung beginnt frühestens mit der belastbaren Bestätigung der Zielquelle. Sie zählt nur, solange deren Bestätigung und die Freigabe für dieselbe Umschaltung fortbestehen. Unmittelbar vor dem Ausschaltauftrag für die bisherige Quelle müssen Zielzustand, Freigabe, Besitz und Überlappung erneut erfüllt sein. Eine unterbrochene Überlappung beginnt erst mit einer wieder belastbaren Bestätigung von vorn. |
| **24V-03** | Kann die Zielquelle nicht bestätigt werden, behält die bisherige Quelle ihren Einschaltauftrag. Zielwechsel, Netzverlust, Netzrückkehr, Planablauf und Moduswechsel werden nach aktueller Lage bewertet. Ist der Ausschaltauftrag bereits gesendet, gilt sein Ergebnis bis zur Rückmeldung als offen. Die bestätigte Ersatzquelle bleibt verfügbar, während der tatsächliche Zustand geklärt oder die Versorgung nach dem bestehenden Schutzvertrag wiederhergestellt wird. Verspätete Rückmeldungen aktualisieren den beobachteten Zustand, erteilen aber keine Freigabe für den nächsten Schritt einer überholten Umschaltung. |
| **24V-04** | Der Wechsel ist erst abgeschlossen, wenn die Zielquelle bestätigt ein und die bisherige Quelle bestätigt aus ist. Bei unklarer Rückmeldung zeigt die Diagnose den ausstehenden Schritt und Grund. Folgeaktionen, die eine abgeschlossene Umschaltung voraussetzen, bleiben gesperrt. Dazu gehört die automatische Inverterfreigabe bei unbestätigt ausgeschaltetem, automatisch geführtem Netzteil. Bestehender Schutz bei Netzverlust und die Entladesperre vor einem automatischen Netzteilstart behalten Vorrang. |
| **24V-05** | Die manuell übernommenen Aktoren bleiben unter Bedienerhoheit. Die Rückgabe an die Automatik wird aus den aktuellen bestätigten Zuständen neu bewertet. Eine alte Überlappungszeit, ein gespeicherter Sollzustand oder ein früherer Schaltauftrag reichen dafür allein nicht aus. |

### Abnahmeszenarien

| Szenario | Erwartetes Ergebnis |
|---|---|
| Normaler Weg in beide Richtungen | Das Ziel wird zuerst bestätigt. Es folgt die volle konfigurierte Überlappung. Der Ausschaltauftrag für die bisherige Quelle ist erst danach zulässig und die Umschaltung endet erst mit bestätigtem Aus. |
| Ziel meldet angenommenes, wiederhergestelltes oder unbekanntes Ein oder meldet gar nicht | Kein Ausschaltauftrag für die bisherige Quelle. Die Diagnose bleibt offen. Eine nachweislich aktuelle Bestätigung startet die volle Überlappung. |
| Bestätigung oder Freigabe geht während der Überlappung verloren, auch unmittelbar vor dem Ausschaltauftrag | Der Ausschaltauftrag unterbleibt. Für eine fortgesetzte Umschaltung zählt die gesamte Überlappung ab der erneuten gültigen Bestätigung. |
| Rückmeldung auf bereits gesendetes Aus verspätet sich oder der Dienst meldet einen Fehler mit unklarem Ergebnis | Die bestätigte Ersatzquelle bleibt eingeschaltet. Kein Abschluss und keine davon abhängige automatische Inverterfreigabe, solange der relevante Zustand offen ist. |
| Netz oder Betriebsmodus wechselt während des Vorgangs, auch nach HA-Neustart oder bei Rückgabe aus Handbetrieb | Die aktuelle Freigabe und die bestätigten Zustände entscheiden. Alte Rückmeldungen können den Zustand klären, aber keine alte Umschaltfolge fortsetzen. |

Grundlage sind [Koordinierte DC-Stützung](F-COORDINATED-DC-SUPPORT.md) und
[Aktuelle Verträge](CURRENT_CONTRACTS.md). Der reproduzierte Ausgangsfall
ist im lokalen Live-Audit vom 27. November festgehalten.

## Einspeise-Nullbefehl

**Anlass.** Eine lokale Gegenprobe hielt den automatischen Schreibauftrag
für 500 W Einspeisung offen. Währenddessen wurde der SOC-Schutz aktiv.
Nach Abschluss des Auftrags blieb `−500 W` der einzige geschriebene
Sollwert. Der Schutz hatte keinen Nullauftrag durchgesetzt.

**Geltungsbereich.** Der vom Battery Manager automatisch geführte Sollwert
der vorgezogenen Einspeisung. An der Home-Assistant-Schnittstelle bedeutet
`−500 W` eine angeforderte Einspeisung von 500 W. `0 W` nimmt diese
Anforderung zurück. Der bestätigte Helferwert ist ein Sollwertnachweis.
Natürlicher Überschussexport am Netzanschluss kann weiterhin auftreten.
Der bestehende Schutz des Wechselrichters und eine bestätigte manuelle
Übernahme des Sollwerts haben eigene Verträge.

| ID | Anforderung |
|---|---|
| **NULL-01** | Jeder nach dem bestehenden Vertrag wirksame Grund für einen automatischen Nullbefehl hat Vorrang vor einer positiven automatischen Einspeiseanforderung. Dazu zählen SOC-Untergrenzenschutz, Abschaltung bei fehlenden oder veralteten Daten und die Deaktivierung der automatischen Einspeisung. Die übrigen bestehenden Stopps und Schutzschwellen behalten ihre Auslöser. Ein erfolgreicher Planungslauf darf keine Voraussetzung für einen fälligen Schutzstopp sein. |
| **NULL-02** | Tritt der Schutz nach Buchung, aber vor Versand eines Exportauftrags ein, darf der Auftrag nicht gesendet werden. Ist der Auftrag bereits gesendet, bleibt das Nullziel trotz ausstehender Bestätigung, Dienstfehler oder verspäteter positiver Rückmeldung wirksam. Sobald ein korrigierender Auftrag möglich ist, muss `0 W` ohne eine wirtschaftliche Mindestlauf- oder Drosselfrist durchgesetzt werden. Auch ein kurzer Schutzimpuls entwertet den überholten Auftrag. Ein neuer Export benötigt eine neue, gültige Freigabe. |
| **NULL-03** | Ein erfolgreicher Dienstaufruf allein erledigt einen fälligen Nullbefehl nicht. Ist noch eine ältere positive Anforderung wirksam oder ihr endgültiger Ausgang unbekannt, kann ein zwischenzeitlich angezeigtes `0 W` die Pflicht nicht erledigen. Nachweis ist ein bestätigter Nullzustand am richtigen Sollwert bei geklärter Reihenfolge der weiterhin wirksamen Aufträge. Jede spätere zuordenbare Rückmeldung eines überholten eigenen Exportauftrags muss erneut eine Nullkorrektur auslösen, solange dessen Export nicht durch eine neue Freigabe gedeckt ist. |
| **NULL-04** | Mehrere gleichzeitige Schutzgründe sperren eine erneute Freigabe, bis alle wirksamen Gründe geklärt sind. Mehrfachmeldungen sollen nach bestätigtem stabilen Nullzustand keine überflüssigen Schreibvorgänge erzeugen. Bei nicht erreichbarem Sollwert, Zeitüberschreitung oder unklarer Rückmeldung zeigt die Diagnose den unbestätigten Stopp samt Auslöser. Bei erneut erreichbarem automatischem Sollwert bleibt der Stopp fällig und wird fortgesetzt. Nach Wiederanlauf oder Änderung der Zuordnung gelten Besitz und Freigabe für den tatsächlich betroffenen Sollwert weiter gemäß dem bestehenden Freigabe- und Entbindungsvertrag. |
| **NULL-05** | Eine bestätigte manuelle Übernahme behält die Schalthoheit nach dem bestehenden Vertrag. Ein verspäteter eigener Schreibauftrag gilt nicht allein deshalb als manuelle Übernahme. Bei bestätigter Übergabe an den Bediener darf der Manager keinen weiteren Sollwertauftrag für dessen Helfer senden. Ungeklärte alte automatische Befehle bleiben als solche diagnostizierbar. |

### Abnahmeszenarien

| Szenario | Erwartetes Ergebnis |
|---|---|
| Schutz vor Versand von `−500 W` | Der Exportauftrag wird verworfen. Ein eigener bestehender Export wird auf `0 W` zurückgeführt. |
| Schutz nach Versand von `−500 W`, Bestätigung kommt verspätet | Der Stopp bleibt fällig. Der letzte für den fortbestehenden Schutz wirksame und bestätigte automatische Sollwert ist `0 W`. Ein später eintreffendes eigenes `−500 W` wird nochmals korrigiert. |
| Der Helfer zeigt noch den vorherigen Wert `0 W`, während ein Exportauftrag offen ist | Die Nullanzeige beendet den Schutzauftrag nicht. |
| Mehrere Schutzgründe, fehlende Daten oder vorübergehend nicht erreichbarer Helfer | Kein weiterer automatischer Export. Die Diagnose unterscheidet Auftrag und bestätigten Stopp. Wiederkehr ermöglicht die Nullkorrektur ohne erfolgreichen neuen Plan. Nach bestätigtem stabilen Nullzustand folgen keine doppelten Nullaufträge. |
| Freigabe geht kurz verloren und kehrt zurück oder der Bediener übernimmt während eines alten Auftrags | Der alte automatische Auftrag wird nicht wieder freigegeben. Eine neue automatische Exportanforderung benötigt eine gültige Freigabe. Eine bestätigte Übernahme schützt den Bedienerwert. |

Grundlage ist [Vorgezogene Einspeisung, insbesondere R7 bis R9 und R12](F-FEEDIN.md).
Zur Abnahme sind steuerbare Zeit und gezielt verzögerte, fehlerhafte oder
umgeordnete Rückmeldungen zu verwenden. Die Szenarien prüfen bestätigte
Zustände, Befehlsreihenfolge und Diagnose. Sie setzen keine Live-Schaltung
am Standort voraus.
