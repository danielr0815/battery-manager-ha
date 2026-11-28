# Spezifikation für 24-V-Quellenwechsel und Einspeise-Nullbefehl

Fachspezifikation zur Fassung 1.1 vom 28. November 2026 mit den
freigegebenen Betreiberentscheidungen D1 bis D3 aus der Vorfassung 1.0.
Die Anforderungen beschreiben das beobachtbare Sollverhalten und die
Abnahme. Design und Implementierung folgen als eigene Arbeitsschritte.

## 24-V-Quellenwechsel

**Anlass.** In einer lokalen Home-Assistant-Gegenprobe zeigte das
24-V-Netzteil nach dem Einschaltauftrag `on` mit `assumed_state`. Der
Auftrag stand noch aus. Der Battery Manager meldete die Umschaltung als
abgeschlossen und schaltete den bis dahin versorgenden DC/DC-Wandler ab.
Die Gegenprobe belegt einen Softwarefehler. Ein Ausfall der realen
24-V-Schiene wurde dadurch nicht nachgewiesen.

**Geltungsbereich.** Automatische Umschaltungen in beide Richtungen zwischen
24-V-Netzteil und DC/DC-Wandler im koordinierten Betrieb mit Invertersteuerung
und im automatischen Pfad ohne Invertersteuerung. Das gilt bei Netzrückkehr,
Wiederanlauf und Rückgabe aus dem Handbetrieb. Die Bedienerhoheit und die
bestehenden Schutzaktionen bei Netzausfall behalten ihre Wirkung.
Der bestehende Hardwarevertrag erlaubt dabei parallelen Betrieb während
der Überlappung. Bei der dokumentierten Anlage hat der DC/DC-Wandler mit
24,3 V Vorrang vor dem 24,05-V-Netzteil. Ein bestätigtes eingeschaltetes
Netzteil belegt daher seine Schaltbereitschaft. Die erfolgte Lastübernahme
ist erst nach dem bestätigten Abschalten der bisherigen Quelle ableitbar.
Ein separater physischer Leistungsnachweis braucht die entsprechenden
Messdaten.

| ID | Anforderung |
|---|---|
| **24V-01** | Die bisherige Quelle bleibt eingeschaltet, bis der tatsächliche Ein-Zustand der Zielquelle nach D2 belastbar bestätigt ist. Bei einem neu erteilten Einschaltauftrag muss der Nachweis zur aktuellen Umschaltung gehören. Diensterfolg, ein ausstehender Auftrag, `unknown`, `unavailable`, `assumed_state`, ein wiederhergestellter Zustand und eine veraltete oder nicht mehr passende Rückmeldung genügen jeweils nicht. Eine bereits eingeschaltete Zielquelle darf verwendet werden, wenn ihr bestätigter Zustand für diese Umschaltung belastbar ist und kein offener oder unklarer Ausschaltauftrag ihn entwertet. |
| **24V-02** | Die vollständige konfigurierte Überlappung beginnt frühestens mit der belastbaren Bestätigung der Zielquelle. Sie zählt nur, solange deren Bestätigung und die Freigabe für dieselbe Umschaltung fortbestehen. Unmittelbar vor dem Ausschaltauftrag für die bisherige Quelle müssen Zielzustand, Freigabe, Besitz und Überlappung erneut erfüllt sein. Eine unterbrochene Überlappung beginnt erst mit einer wieder belastbaren Bestätigung von vorn. |
| **24V-03** | Kann die Zielquelle nicht bestätigt werden, behält die bisherige Quelle ihren Einschaltauftrag. Zielwechsel, Netzverlust, Netzrückkehr, Planablauf und Moduswechsel werden nach aktueller Lage bewertet. Ist der Ausschaltauftrag bereits gesendet, gilt sein Ergebnis bis zur Rückmeldung als offen. Die bestätigte Ersatzquelle bleibt verfügbar, während der tatsächliche Zustand geklärt oder die Versorgung nach dem bestehenden Schutzvertrag wiederhergestellt wird. Verspätete Rückmeldungen aktualisieren den beobachteten Zustand, erteilen aber keine Freigabe für den nächsten Schritt einer überholten Umschaltung. |
| **24V-04** | Der Wechsel ist erst abgeschlossen, wenn die Zielquelle belastbar bestätigt ein und die bisherige Quelle nach D2 belastbar bestätigt aus ist. Bei unklarer Rückmeldung zeigt die Diagnose den ausstehenden Schritt und Grund. Folgeaktionen, die eine abgeschlossene Umschaltung voraussetzen, bleiben gesperrt. Dazu gehört die automatische Inverterfreigabe bei unbestätigt ausgeschaltetem, automatisch geführtem Netzteil. Bestehender Schutz bei Netzverlust und die Entladesperre vor einem automatischen Netzteilstart behalten Vorrang. |
| **24V-05** | Die manuell übernommenen Aktoren bleiben unter Bedienerhoheit. Die Rückgabe an die Automatik wird aus den aktuellen bestätigten Zuständen neu bewertet. Eine alte Überlappungszeit, ein gespeicherter Sollzustand oder ein früherer Schaltauftrag reichen dafür allein nicht aus. |

### Freigegebene Betreiberentscheidungen zu 24 V

- **D1 · Gleicher Nachweisvertrag in beiden automatischen Pfaden.** Die
  Nachweisbedingungen für Ziel-EIN, Überlappung und Abschluss gelten in
  beiden Umschaltrichtungen sowohl mit als auch ohne koordinierte
  Invertersteuerung. Das schließt die erneute Prüfung von Zielzustand,
  Besitz, Freigabe und Überlappung vor AUS ein. Nach einer Unterbrechung
  beginnt die volle Überlappung erneut. Der Betreiber behält die
  Schalthoheit über manuell übernommene Aktoren. Bei Netzausfall beginnt
  die Wiederherstellungsprüfung sofort. Ein notwendiges DC/DC-EIN wartet
  weder auf die Inverterbestätigung noch auf einen hierfür unerheblichen
  Abschaltnachweis. Ist bereits ein ungeklärtes AUS am DC/DC-Wandler
  gesendet, wird zuerst dessen Ergebnis geklärt. Solange es offen ist,
  hält der BM den bestätigten Alternativpfad verfügbar. In dieser Zeit
  unterbleiben ein widersprechender EIN-Auftrag an den Wandler und das
  Ausschalten der Ersatzquelle. Anschließend gelten die Nachweise für
  Zielbestätigung und Überlappung vor dem Abschalten der bisherigen Quelle.
- **D2 · Belastbarer Kanalzustand je Aktor.** EIN und AUS müssen für Netzteil
  und DC/DC-Wandler jeweils durch einen für diesen Aktor qualifizierten,
  frischen und zum Vorgang passenden Istzustand belegt sein. Eine Rückmeldung des
  Schaltkanals reicht aus, wenn sie dessen tatsächliche Schaltstellung
  zuverlässig wiedergibt. Fehlt diese Eignung, ist eine unabhängige
  Rückmeldung über den tatsächlichen Zustand desselben Aktors erforderlich.
  Bis dahin bleibt die Zustandsbestätigung offen. Eine Diensterfolgsquittung,
  eine Leistungsanzeige von `0 W`, ein Auftragsecho, eine angenommene,
  wiederhergestellte, alte oder anders zugeordnete Meldung genügen nicht.
  Meldet Home Assistant zunächst den beauftragten Zustand, zählt erst
  die geeignete Kanalrückmeldung als Nachweis. Auch ein gleichbleibender
  Wert oder eine zyklische Meldung braucht eine Prüfung auf Herkunft,
  Frische und Gültigkeit für den aktuellen Vorgang.

### Abnahmeszenarien

| Szenario | Erwartetes Ergebnis |
|---|---|
| Normaler Weg mit und ohne Invertersteuerung in jeweils beiden Richtungen | Das Ziel wird zuerst bestätigt. Es folgt die volle konfigurierte Überlappung. Der Ausschaltauftrag für die bisherige Quelle ist erst danach zulässig und die Umschaltung endet erst mit nach D2 bestätigtem Aus. |
| Ziel meldet angenommenes, wiederhergestelltes oder unbekanntes Ein oder meldet gar nicht | Kein Ausschaltauftrag für die bisherige Quelle. Die Diagnose bleibt offen. Eine nachweislich aktuelle Bestätigung startet die volle Überlappung. |
| Bestätigung oder Freigabe geht während der Überlappung verloren, auch unmittelbar vor dem Ausschaltauftrag | Der Ausschaltauftrag unterbleibt. Für eine fortgesetzte Umschaltung zählt die gesamte Überlappung ab der erneuten gültigen Bestätigung. |
| Rückmeldung auf bereits gesendetes Aus verspätet sich oder der Dienst meldet einen Fehler mit unklarem Ergebnis | Die bestätigte Ersatzquelle bleibt eingeschaltet. Kein Abschluss und keine davon abhängige automatische Inverterfreigabe, solange der relevante Zustand offen ist. |
| Kanalstatus für EIN und AUS je Aktor: identischer HA-Zustandswert aus dem Schaltauftrag, danach echte Kanalrückmeldung | Die erste Meldung beginnt keine Überlappung und beendet keinen Wechsel. Erst der qualifizierte, frische, aktuelle Kanalstatus zählt. Ist der Kanal ungeeignet, bleibt der Schritt bis zu einer geeigneten unabhängigen Rückmeldung für denselben Aktor offen. Eine Leistungsanzeige von `0 W` oder eine ungeprüfte alte, gleichbleibende oder zyklische Meldung schließt den Schritt nicht ab. |
| Netz oder Betriebsmodus wechselt während des Vorgangs, auch nach HA-Neustart oder bei Rückgabe aus Handbetrieb | Die aktuelle Freigabe und die bestätigten Zustände entscheiden. Alte Rückmeldungen können den Zustand klären, aber keine alte Umschaltfolge fortsetzen. |
| Netz fällt aus und die Inverterrückmeldung oder ein hierfür unerheblicher Abschaltnachweis fehlt | Die Wiederherstellungsprüfung beginnt sofort. Der notwendige DC/DC-EIN-Auftrag erfolgt ohne diese Rückmeldungen. Vor dem späteren Abschalten der bisherigen Quelle gelten bestätigtes Ziel-EIN und volle Überlappung. |
| Netz fällt aus, während ein bereits gesendetes DC/DC-AUS ungeklärt ist | Die Wiederherstellungsprüfung beginnt sofort. Bis zur Klärung bleibt das bestätigte 24-V-Netzteil verfügbar. Weder ein widersprechendes DC/DC-EIN noch ein AUS für das Netzteil wird gesendet. Nach Klärung des Auftrags folgen bei notwendigem Wechsel bestätigtes Ziel-EIN und die volle Überlappung vor AUS der bisherigen Quelle. [Vorhandener Teilnachweis](../tests/ha/test_reserve_runtime.py#L517) |

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
| **NULL-02** | Tritt der Schutz nach Buchung, aber vor Versand eines Exportauftrags ein, darf der Auftrag nicht gesendet werden. Ist der Auftrag bereits gesendet, bleibt das Nullziel trotz ausstehender Bestätigung, Dienstfehler oder verspäteter positiver Rückmeldung wirksam. Sobald ein korrigierender Auftrag möglich ist, muss `0 W` ohne eine wirtschaftliche Mindestlauf- oder Drosselfrist durchgesetzt werden. Auch ein kurzer Schutzimpuls entwertet den überholten Auftrag. Eine neue Exportfreigabe setzt D3 voraus. |
| **NULL-03** | Ein erfolgreicher Dienstaufruf allein erledigt einen fälligen Nullbefehl nicht. Ist noch eine ältere positive Anforderung wirksam oder ihr endgültiger Ausgang unbekannt, kann ein zwischenzeitlich angezeigtes `0 W` die Pflicht nicht erledigen. Nachweis ist ein bestätigter Nullzustand am richtigen Sollwert bei geklärter Reihenfolge. Wirkt ein überholter eigener Exportauftrag später tatsächlich oder wird seine Reihenfolge wieder unklar, verliert der frühere Nullabschluss seine Gültigkeit. Eine zwischenzeitliche neue Freigabe gilt ausschließlich für den dazugehörigen Auftrag. Am weiterhin BM-geführten Helfer ist geordnete Null wiederherzustellen, bevor erneut freigegeben wird. Eine rein historische Meldung bei nachweislich unverändertem, gültigem neuen Sollwert löst keinen Schreibauftrag aus. |
| **NULL-04** | Mehrere gleichzeitige Schutzgründe sperren eine erneute Freigabe, bis alle wirksamen Gründe geklärt sind. Mehrfachmeldungen sollen nach bestätigtem stabilen Nullzustand keine überflüssigen Schreibvorgänge erzeugen. Bei nicht erreichbarem Sollwert, Zeitüberschreitung oder unklarer Rückmeldung zeigt die Diagnose den unbestätigten Stopp samt Auslöser. Bei erneut erreichbarem automatischem Sollwert bleibt der Stopp fällig und wird fortgesetzt. Nach Wiederanlauf oder Änderung der Zuordnung gelten Besitz und Freigabe für den tatsächlich betroffenen Sollwert weiter gemäß dem bestehenden Freigabe- und Entbindungsvertrag. |
| **NULL-05** | Eine bestätigte manuelle Übernahme behält die Schalthoheit nach dem bestehenden Vertrag. Ein verspäteter eigener Schreibauftrag gilt nicht allein deshalb als manuelle Übernahme. Bei bestätigter Übergabe an den Bediener darf der Manager keinen weiteren Sollwertauftrag für dessen Helfer senden. Ungeklärte alte automatische Befehle bleiben als solche diagnostizierbar. |

### Freigegebene Betreiberentscheidung zum Wiederanlauf

- **D3 · Geordnete Null vor erneuter Exportfreigabe.** Nach jedem
  wirksamen Schutzstopp, auch nach einem kurzen Schutzimpuls, setzt
  eine erneute automatische Exportfreigabe bestätigte `0 W` am richtigen
  und weiterhin vom Battery Manager geführten Sollwerthelfer voraus.
  Ein bereits versandter überholter Exportauftrag ist nach NULL-02 und
  NULL-03 zu korrigieren.
  Kein überholter eigener Schreibauftrag darf offen sein. Erst wenn alle
  Schutzgründe entfallen sind, gilt eine neu geprüfte, nach den aktuellen
  Bedingungen gültige Freigabe. Eine bereits bestätigte stabile Null
  erfüllt den Nullnachweis, wenn kein alter Exportauftrag offen ist.
  Ihr Wert braucht keinen erneuten Schreibauftrag. Sobald alle
  Voraussetzungen erfüllt sind, ist keine zusätzliche Wartefrist allein
  wegen des früheren Schutzimpulses erforderlich. Bestehende sonstige
  Freigabebedingungen gelten weiter.

### Abnahmeszenarien

| Szenario | Erwartetes Ergebnis |
|---|---|
| Schutz vor Versand von `−500 W` | Der Exportauftrag wird verworfen. Ein eigener bestehender Export wird auf `0 W` zurückgeführt. |
| Schutz nach Versand von `−500 W`, Bestätigung kommt verspätet | Der Stopp bleibt fällig. Der letzte für den fortbestehenden Schutz wirksame und bestätigte automatische Sollwert ist `0 W`. Ein später eintreffendes eigenes `−500 W` wird nochmals korrigiert. |
| Der Helfer zeigt noch den vorherigen Wert `0 W`, während ein Exportauftrag offen ist | Die Nullanzeige beendet den Schutzauftrag nicht. |
| Mehrere Schutzgründe, fehlende Daten oder vorübergehend nicht erreichbarer Helfer | Kein weiterer automatischer Export. Die Diagnose unterscheidet Auftrag und bestätigten Stopp. Wiederkehr ermöglicht die Nullkorrektur ohne erfolgreichen neuen Plan. Nach bestätigtem stabilen Nullzustand folgen keine doppelten Nullaufträge. |
| Der Schutzgrund entfällt vor der verspäteten Rückmeldung eines alten Exportauftrags | Der alte Auftrag bleibt entwertet. Selbst bei bereits neu verfügbaren Plan- und Messdaten bleibt eine erneute Freigabe gesperrt, bis am weiterhin BM-geführten Helfer geordnete Null bestätigt ist, keine überholten Aufträge offen sind und alle Schutzgründe entfallen sind. Anschließend entscheidet eine neue gültige Freigabe. |
| Nach geordneter Null und einer neuen gültigen Freigabe wird der alte Exportwert erneut wirksam oder die Befehlsreihenfolge wird wieder unklar | Der frühere Nullabschluss verliert seine Gültigkeit. Der BM stellt am weiterhin eigenen Helfer erneut geordnete Null her und prüft danach eine neue Freigabe. Die zwischenzeitliche Freigabe deckt ausschließlich ihren eigenen Auftrag. |
| Eine rein historische verspätete Rückmeldung eines überholten Auftrags trifft ein. Der aktuell gültige neue Sollwert ist nachweislich unverändert. | Der BM belässt den gültigen Sollwert und sendet keinen zusätzlichen Schreibauftrag. |
| Vor dem kurzen Schutzimpuls war der weiterhin BM-geführte Helfer bereits bestätigt stabil auf `0 W`, ohne offenen Exportauftrag | Die bestätigte Null darf weiter als Nachweis gelten. Sobald alle Schutzgründe entfallen und die neue gültige Freigabe vorliegt, folgen weder ein zusätzlicher Nullauftrag noch eine zusätzliche Wartefrist wegen des Schutzimpulses. |
| Der Bediener übernimmt während eines alten Auftrags | Die bestätigte Übernahme schützt den Bedienerwert. Der alte Auftrag bleibt als solcher diagnostizierbar. |

Grundlage ist [Vorgezogene Einspeisung, insbesondere R7 bis R9 und R12](F-FEEDIN.md).
Zur Abnahme sind steuerbare Zeit und gezielt verzögerte, fehlerhafte oder
umgeordnete Rückmeldungen zu verwenden. Die Szenarien prüfen bestätigte
Zustände, Befehlsreihenfolge und Diagnose. Sie setzen keine Live-Schaltung
am Standort voraus.
