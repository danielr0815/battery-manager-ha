# F-CASCADE-STORAGE — kaskadierte Überschusslasten mit Speichern

Status: normative Feature-Spezifikation. Der Planungsnachtrag vom 2026-09-02
ersetzt den bisherigen Ein-Episoden-/Recovery-Sperrvertrag. Die hier relevante
Recovery-/Feed-in-Priorisierung ist der verbindliche Abnahmevertrag; der
Implementierungsstand wird nicht aus einer früheren Release-Nummer abgeleitet,
sondern durch die in diesem Dokument genannten Regressionstests nachgewiesen.

## Ziel und Geltungsbereich

Battery Manager darf mehrere disjunkte lineare Ketten steuern. Jede Kette
besteht aus mindestens einem energiebegrenzten Speicher und genau einer
abschließenden, nicht energiebegrenzten Last:

```text
Root/PV/Grid → B1.input → B1.output → B2 … → Bn.output → Endlast
```

Verzweigungen, gemeinsam genutzte Lasten oder Aktoren und parallele Verbraucher
an einem Storage-Output sind nicht Teil dieses Features. Ohne `cascades` bleibt
das bisherige Planner- und Entity-Verhalten unverändert.

## Konfigurationsvertrag

Eine `cascade`-Subentry enthält den Namen, die geordneten Storage-Load-IDs, die
terminale Load-ID und den Actor-Confirmation-Timeout. Storage-Load-Subentries
tragen zusätzlich Output-Aktor und -Leistung, Sicherheits-Floor,
Kaskaden-Entladeziel,
Wake-/Handover-Parameter, Actor-Besitzmodi sowie optionale Wirkungsgrade,
Leistungslimits, Output-Overhead und das vollständige Standalone-Idle-Paar.

Es gelten zwingend:

- mindestens ein Mitglied; jedes Mitglied und jeder Actor kommt nur einmal vor;
- Mitglieder sind `energy_limited`, die Endlast ist es nicht;
- alle Mitglieder besitzen SOC, Charge-Gate, Output und Output-Leistung;
- B1 besitzt den einzigen Input-Control-Actor;
- `0 <= Sicherheits-Floor < Kaskaden-Entladeziel <= Ladeziel <= 100`;
- Wirkungsgrade liegen in `(0, 1]`, Caps und Timeouts sind positiv;
- Handover-Timeout ist mindestens 60 Sekunden;
- Idle-Schwelle und -Dauer werden nur gemeinsam konfiguriert.

Eine ungültige Referenz bleibt fail-closed sichtbar und muss durch Reconfigure
oder Löschen der Kaskade behoben werden.

## Planungsregeln

### Prioritäts- und Tagesvertrag (Operator-Entscheidung 2026-09-02)

Die globale Load-Reihenfolge bleibt die äußerste Prioritätsordnung. Die
Kaskade belegt dabei die Position ihres ersten konfigurierten Teilnehmers.
Innerhalb dieses Verbunds gilt folgende lexikographische Reihenfolge:

1. Safety-Floors, kein zusätzlicher Netzimport und die physische
   Quellenexklusivität sind harte Bedingungen.
2. Die direkte Endlast hat Vorrang vor jeder Wiederaufladung und jedem
   zusätzlichen Laden der Mitglieder. Ein Recovery-Bedarf darf eine direkt
   versorgbare Endlast weder pausieren noch aus ihrem Plan verdrängen.
3. Der nach der Endlast verbleibende Root-Überschuss wird zuerst so auf die
   Mitglieder verteilt beziehungsweise für sie reserviert, dass alle
   erreichbaren Kaskaden-Entladeziele gesichert sind.
4. Erst die danach verbleibende Energie lädt Mitglieder über ihr
   Kaskaden-Entladeziel hinaus bis zum normalen Ladeziel.
5. Vorzeitige Einspeisung ist die letzte Planungsoption. Sie darf nur Export
   zeitlich verschieben, der trotz aller zulässigen Endlast-, Recovery-,
   Top-up- und Aux-Allokationen später unvermeidlich bleibt.

Damit wird nutzbare Energie weder verlustbehaftet in einem Fossibot
zwischengespeichert, während die Endlast pausiert, noch für einen Top-up eines
Mitglieds verbraucht, wenn dadurch ein anderes Mitglied sein erreichbares
Kaskaden-Entladeziel verfehlt. Ein Top-up über das Entladeziel darf schon vor
der tatsächlichen Recovery eines anderen Mitglieds nur Energiefragmente
belegen, die dieses Mitglied wegen Leistung, Mindestlaufzeit, Caps oder
Topologie nachweislich nicht nutzen kann und die dessen Tagesend-Recovery
nicht verschlechtern.

Der Tagesnachweis simuliert ab dem aktuellen Rolling-Slot bis zum Ende des
lokalen Kalendertages die unverändert vorrangige Endlast, sämtliche
Mitglieds-SOCs, Wirkungsgrade, Leistungs- und Passthrough-Caps,
Mindestlaufzeiten sowie alle bereits gebuchten Root-, Aux- und
Recovery-Abschnitte gemeinsam. Jeder beim Replan unter seinem Entladeziel
liegende Speicher bildet dabei ein Recovery-Defizit, unabhängig davon, ob es
durch eine frühere Planner-Entladung, manuelle Nutzung, einen Neustart oder
Messkorrekturen entstand.

Für die Speichereingänge gilt eine strengere Quellenregel als für normale
Überschusslasten: Jeder physische Ladeabschnitt muss vollständig durch die im
selben Slot nach höher priorisierten Lasten noch verbleibende PV-Leistung
gedeckt sein; ein Kaskadenmitglied darf dabei nie die Hausbatterie entladen
oder zusätzlichen Netzimport erzeugen. Recovery bis zum Kaskaden-Entladeziel
darf PV verwenden, welche die Hausbatterie sonst laden würde, aber nur wenn der
vollständige Plan desselben lokalen Kalendertages beweist, dass späterer Export
mindestens in derselben AC-Energiemenge sinkt und die geschützten Haus-SOC-Ziele
erhalten bleiben. Das gilt ausdrücklich auch parallel für mehrere Mitglieder,
wenn die verbleibende PV-Leistung ihre gleichzeitigen Eingänge deckt.

Top-up oberhalb des Entladeziels bleibt strenger: Er darf nur in einem Slot
stattfinden, dessen bereits sichtbarer Restexport jeden belegten Laufabschnitt
vollständig deckt. `battery_tolerance`, At-Max-Topup aus der Hausbatterie und
die allgemeine prognosegestützte Pass-2-Vorladung bleiben für
Kaskadenmitglieder gesperrt. Die terminale Endlast behält dagegen die
allgemeinen Strategien und darf unter deren No-Import-, Stress- und G4-Gates
auch aus der Hausbatterie versorgt werden. Weil ihr kontinuierlicher
Pre-Drain-Block erst nach dem ersten Direktüberschuss-Pass feststeht, wird
offene Recovery nach dem vollständigen Endlastplan erneut allokiert, bevor
Restexport an die vorzeitige Einspeisung geht. Nachdem jedes Mitglied alle
streng bis zum Entladeziel begrenzten Recovery-Möglichkeiten erhalten hat,
darf ein vollständig exportgedeckter Mindestlaufzeit-Block dieses Ziel
geringfügig überschreiten. Diese abschließende Raster-Rundung zählt nicht als
bevorrechtigter Top-up; sie darf keine noch mögliche Recovery eines anderen
Mitglieds verdrängen. Ein neuer Aux-Start ist nur
zulässig, wenn die vollständige Mindestlaufzeit erbracht werden kann. Dabei
gelten zwei Reservestufen:

1. Energie oberhalb des Kaskaden-Entladeziels (standardmäßig 50 %) ist für die
   vorrangige Endlast ohne Wiederaufladezusage verfügbar. Das Entladeziel ist
   daher keine unmittelbare Schaltgrenze für die Endlast.
2. Energie zwischen Entladeziel und Sicherheits-Floor (standardmäßig 20 %)
   darf nur vorab genutzt werden, wenn der vollständige Plan des lokalen
   Kalendertages ansonsten späteren unvermeidlichen Grid-Export ausweist, die
   zusätzliche Speicheraufnahme diesen Export tatsächlich reduziert, keinen
   zusätzlichen Netzimport erzeugt und alle Mitglieder trotz der unverändert
   vorrangig eingeplanten Endlast spätestens am Tagesende wieder mindestens
   ihr Entladeziel erreichen. Die nutzbare Energie wird inklusive
   Ladewirkungsgrad auf genau den noch unvermeidlich exportierten AC-Betrag
   begrenzt.

Eine bereits laufende Episode darf ihren Rest bis zum jeweils aktuellen
Slot-Ziel nur in einem weiterhin gültigen Tagesnachweis fortsetzen. Auch ein
beim Rolling Replan bereits unter 50 % liegendes Mitglied darf weiter bis zu
dem exportgedeckten Ziel laufen, wenn der verbleibende Tagesplan einschließlich
der vorrangigen Endlast weiterhin die Rückkehr aller Mitglieder zu ihren
Entladezielen beweist. Ohne diesen Nachweis wird der zusätzliche Tiefenhub
sofort zurückgezogen. Späteres Laden bis zum höheren normalen Ladeziel nutzt
innerhalb der Top-up-Stufe die globale Priorität und die beschriebene
PV-only-Quellengrenze; es darf keine erreichbare Recovery eines anderen
Mitglieds verzögern oder verhindern.

Die Vorschau bewertet alle für den verbleibenden lokalen Kalendertag möglichen
Aux-, Root- und Recovery-Abschnitte gemeinsam. Ohne Exportnutzen beginnt eine
neue Aux-Episode innerhalb ihres zulässigen Fensters am spätestmöglichen
Forecast-Slot, der den vollständigen Tagesvertrag noch erfüllt. Soll ihre
Entladung dagegen spätere PV-Aufnahme ermöglichen, wird sie so früh wie nötig
vor diese Aufnahme gelegt. Lade-Headroom wird dabei als kumulatives
Zeitreihenbudget geführt: Ein Root-Slot darf nur Energie buchen, deren
Speicherplatz durch den bis dahin beobachteten Anfangs-SOC oder eine zeitlich
bereits vorhergehende Aux-Entladung existiert. Künftige Entladung darf niemals
rückwirkend eine frühere Ladung rechtfertigen. Nur Slot 0 ist unmittelbar
ausführbar; ein künftiger Start bleibt Vorschau und wird bei jedem Rolling
Replan neu bewertet. Sobald der Start in Slot 0 rückt oder die Episode bereits
läuft, bleibt sie dort bis zum nächsten sicheren Umschaltpunkt verankert.

Mehrere Aux-Episoden und ein erneutes Anwachsen bereits teilweise oder
vollständig abgebauter Recovery-Schuld sind am selben Tag zulässig. Jede
zusätzliche Entladung muss im dann aktuellen Gesamttagesplan erneut späteren
unvermeidlichen Export verhindern und die Rückkehr aller Mitglieder zu ihren
Entladezielen bis Tagesende beweisen. Der globale Replan berücksichtigt die
vollständige zusätzliche Ladekapazität und alle Recovery-Reservierungen,
bevor Restexport für die vorzeitige Einspeisung gebucht wird. Root-Aufnahme
und Aux-Entladung dürfen weder real noch in der Prognose gleichzeitig
auftreten; ein Root-/PV-Slot trennt daher zwei Aux-Abschnitte elektrisch,
verbietet aber keinen weiteren, erneut nachgewiesenen Aux-Abschnitt desselben
Tages.

Der Core simuliert die Speicher-SOCs mit Lade-/Entladewirkungsgrad, Charge-,
Output- und Passthrough-Caps sowie Output-Overhead. `HourFlows.extra_ac_wh`
bleibt die externe Root-Bilanz; interne Durchleitung und Aux-Energie erscheinen
nur in `CascadePlan`. `LoadPlan.managed_by_cascade` unterdrückt die unabhängige
Aktuation der Mitglieder und der Endlast.

Für jeden Lastpfad existiert pro Planlauf genau eine effektive Wirkleistung.
Sie ist die normale robuste Planungsleistung der Last, bei einem
Kaskadenmitglied zusätzlich hart durch `max_charge_power_w` begrenzt. Dieselbe
Leistung bestimmt Lastenergie, Root-Grenzenergie, Mitglieds-SOC, Aktivitäten
und Kartenwerte. Nominal-, Lern- und Cap-Leistung dürfen nicht in
unterschiedlichen Bilanzschichten parallel verwendet werden. Der
Output-Overhead wird nur für tatsächlich benötigte Ausgänge und genau einmal
an der Root-Grenze beziehungsweise an der entladenden Aux-Quelle gebucht.

Ein geplanter neuer Root- oder Aux-Abschnitt enthält vor der ersten
Nutzenergie ein konservatives Übergangsbudget aus den konfigurierten
Actor-Bestätigungs-, Mitglieds-Wake- und Handover-Zeiten. Während dieses
Budgets wird keine noch nicht nachgewiesen gelieferte Endlast- oder
Speicherenergie gutgeschrieben. Reicht das verbleibende Slot-/Tagesfenster
nicht für Übergang plus vollständige Mindestlaufzeit, ist der Start nicht
planbar. Bereits laufende oder nachgewiesene Pfade bezahlen das Budget nicht
erneut.

Recovery-Schuld ist eine tagesweite Verpflichtung und kein pauschales
Episodenverbot. Sie bleibt über Rolling Replans, Home-Assistant-Neustarts und
Mitternacht erhalten und geht als Anfangsdefizit in jeden neuen Tagesplan ein.
Vor Tagesende darf sie erneut anwachsen, solange der vollständige aktuelle
Tagesplan ihre Rückführung beweist. Wird das Entladeziel entgegen dem Plan bis
Mitternacht nicht erreicht, bleibt die Schuld offen; am Folgetag ist eine
weitere Nutzung der geschützten Reserve nur mit einem neuen vollständigen
Tagesnachweis zulässig. Die direkte Endlast behält auch dann ihre höhere
Priorität.

Jeder Rolling Replan beginnt bei den aktuellen gemessenen Mitglieds-SOCs und
dem aktuellen partiellen Slot. Bereits tatsächlich gelieferte Root- und
Aux-Energie wird nur in den Tageszählern ausgewiesen; sie darf weder ein
zweites Mal als verfügbare Energie noch als zukünftige Recovery angerechnet
werden. Umgekehrt darf geplante Energie nach dem heutigen lokalen Tagesende
kein heute offenes Recovery-Defizit erfüllen. Der Tagesendnachweis wird nach
jeder angenommenen Aux-Episode und jeder Root-Allokation neu berechnet, bis
keine weitere zulässige Endlast-/Recovery-/Top-up-/Aux-Allokation den
Restexport reduzieren kann.

Kann nicht genügend Energie für alle Recovery-Defizite genutzt werden, bleibt
Recovery als Stufe insgesamt vor Top-up. Innerhalb dieser Stufe entscheidet
die globale Kaskaden-/Mitgliedspriorität über knappe nutzbare Energie. Ein
niedriger priorisiertes Mitglied darf also keine Recovery eines höher
priorisierten Mitglieds verdrängen; Top-up darf umgekehrt erst Fragmente
nutzen, die kein noch offenes Recovery-Defizit physisch verwenden kann.

Cache-SOCs bis sieben Tage dürfen Vorschau und Aggregat speisen; vor Entladung
sind neue numerische Live-SOCs aller Mitglieder zwingend. Der
Sicherheits-Floor bleibt in Planung und Ausführung die absolute Untergrenze.

## Executor- und Safety-Vertrag

Neue oder sicherheitsrelevant geänderte Kaskaden starten mit Automation AUS.
Der zentrale `CascadeManager` ist alleiniger Actor-Besitzer. Ketten sind
untereinander parallelisierbar, Operationen innerhalb einer Kette sind
serialisiert.

Der Erst-Wake ist geordnet:

1. alle Charge-Gates AUS;
2. Root-Eingang AN;
3. eine neue numerische Geräte-Telemetrie von B1 nach Root-AN abwarten;
4. B1-Output AN, danach eine neue Geräte-Telemetrie von B2 abwarten und dieses
   Muster bis zum letzten für den Pfad benötigten Mitglied fortsetzen;
5. erst nach dessen Aufwachnachweis Charge-Gate bzw. optionalen Endlast-Aktor
   AN;
6. übersprungene Upstream-Outputs trennen, Root AUS;
7. gewählte Quelle mit zwei ausschließlich nach der Umschaltung beobachteten,
   mindestens 60 Sekunden getrennten Leistungssamples bestätigen.

Eine bereits vor dem Zuschalten vorhandene numerische Meldung gilt dabei nur
als Baseline und niemals als Aufwachnachweis. Maßgeblich ist eine nach der
jeweils vorgelagerten Schalthandlung erfolgte HA-Publikation von SOC,
Eingangsleistung oder Ausgangsleistung; auch ein unveränderter Zahlenwert zählt
über `last_reported`. Diese Meldung beweist, dass die vorgelagerte Versorgung
den konkreten Speicher erreicht hat, aber noch nicht, dass dessen intern
startender Befehlskanal bereits schaltbereit ist. Erst die bestätigte
Zielstellung des AC-Ausgangs schließt die jeweilige Stufe ab.

Jede Stufe besitzt deshalb zwei getrennte Grenzen: Das konfigurierte
Wake-Timeout begrenzt das Warten auf eine neue Telemetriepublikation. Nach
dieser Publikation darf ein einzelnes Actor-Confirmation-Timeout den sicheren
Wake-Präfix nicht sofort abbrechen. Der Executor lässt Root und alle bereits
bestätigten Upstream-Ausgänge unverändert an und wiederholt den noch offenen
Output-Befehl in folgenden Coordinator-Zyklen bis zum konfigurierten
Wake-Timeout des Mitglieds. Erst dessen Ablauf führt mit Anzahl der
Versuche und letzter Actor-Ursache zu Wake-Fehler und Safe-OFF. Dadurch erhält
insbesondere B2 seinen AC-Ausgangsbefehl erst, nachdem B2 durch B1 tatsächlich
versorgt wurde, und ein langsamer Befehlskanal löst keinen verfrühten
Root-Abbruch aus. Ein einmaliger Refresh an jeder absoluten Wake-Deadline
begrenzt auch ein vollständig stilles Mitglied unabhängig vom regulären
Coordinator-Poll. Er stellt zugleich den letzten Output-Versuch sicher, wenn
eine während der blockierenden Actor-Bestätigung eingetroffene
Sensorpublikation vom laufenden Coordinator-Debounce absorbiert wurde; der
Timer wird an jeder Abschluss-, Stufenwechsel- oder Safe-OFF-Grenze entfernt.

Ein angenommener Root-Wake ist innerhalb seines Wake-Fensters ebenso atomar
wie ein Aux-Wake: Ein Rolling Replan ohne Root-Segment darf die bereits
eingeschaltete Versorgung nicht vor der nächsten frischen numerischen
Publikation des gerade aufwachenden Mitglieds trennen. Kehrt die Root-Planung
bis dahin zurück, wird der geordnete Wake fortgesetzt. Bleibt sie zurückgezogen
oder wechselt der Plan zu Aux, ist die frische Publikation die sichere
elektrische Grenze: Die Kette geht geordnet auf Safe-OFF, ohne einen weiteren
Ausgang, ein Charge-Gate oder die Endlast einzuschalten. Das ursprüngliche
absolute Wake-Timeout bleibt dabei unverändert; fehlende Telemetrie endet auch
bei zurückgezogenem Plan weiterhin fail-closed. Globale Safety-Gates,
deaktivierte Mitglieder, Ownership-Verstöße und Tageswechsel dürfen den Wake
weiterhin sofort unterbrechen.

Der vollständige Wake erhält genau einen Retry nach 15 Minuten; dieser
Retry-Zustand bleibt auch vor dem Setzen des erst nach Leistungsnachweis
verfügbaren `episode_day` über Rolling Refreshes erhalten. Ein
erfolgreicher Service-Aufruf bestätigt einen `assumed_state`-Actor logisch; die
fehlende physische Rückmeldung ist als Diagnoseeinschränkung zu verstehen.
Bei Actoren mit echter Zustandsrückmeldung gilt der Service-Aufruf allein
dagegen nicht als Bestätigung: Der Manager wartet je Versuch innerhalb des
konfigurierten Actor-Confirmation-Timeouts auf den Zielzustand und setzt erst
dann seinen Claim. Die begrenzte Wiederholung gilt ausschließlich für den noch
stromlos sicheren Output-AN-Schritt eines Mitglieds-Wakes; andere
Actor-Fehler bleiben unmittelbar fail-closed. Ein bereits bestätigter
Zielzustand wird ohne redundanten Service-Aufruf übernommen; damit bleibt
insbesondere wiederholtes Safe-OFF idempotent.

Eine abgeschlossene `episode_day`-Marke des Vortags wird aus einem stabilen,
bestätigt ausgeschalteten Zustand entfernt, bevor am neuen Tag ein Root- oder
Aux-Wake beginnt. Ein bereits transient laufender Übergang über Mitternacht
wird einmal geordnet Safe-OFF geschaltet; ohne offene Recovery-Schuld wird die
alte Tagesmarke danach ebenfalls entfernt, mit offener Schuld auf den neuen
Tag übernommen. Der Tageswechsel ist damit in beiden Fällen konsumiert und
darf weder Root im Coordinator-Takt AUS/AN schalten noch die Wake-Deadline
wiederholt neu beginnen lassen. Eine gleichlautende Marke des aktuellen Tages
bleibt bewusst erhalten: Manuelles AUS→AN eröffnet keine zweite unbewiesene
Aux-Episode desselben Tages.

Der Executor stoppt jede Aux-Quelle am SOC-Endwert des aktuellen Planslots,
begrenzt diesen aber immer durch den Sicherheits-Floor. Dadurch kann ein
exportgedeckter Plan unter 50 % tatsächlich ausgeführt werden, ohne dass ein
veraltetes festes 50-%-Laufzeitlimit den Core-Plan vorzeitig abbricht. Sobald
ein Plan eine Unterschreitung des Entladeziels vorsieht, wird die
Wiederaufladezusage bereits beim Leistungsnachweis persistiert und nicht erst
nach dem gemessenen SOC-Abfall.

Ziel-, Floor- und Safety-Abbrüche übersteuern Dwell. Safe-OFF schaltet die
Endlast, Outputs downstream→upstream, Charge-Gates und Root aus. Ein
Safe-OFF-Fehler setzt einen Hard-Fault und Repair; Reset versucht Safe-OFF erneut, löscht nur
bei Erfolg und lässt Automation AUS. Nach einem erfolgreich abgeschlossenen,
faultbedingten Safe-OFF bleibt der Fault sichtbar und sperrt Automation-AN,
aber der Manager gibt die Actors für manuelle Diagnose frei und wiederholt
Safe-OFF nicht bei jedem Refresh. `exclusive` wertet Fremdänderungen bei
aktiver Automation als Fault. `shared` beendet Claims und Automation ohne
Rollback (Hands-off); Wiederaufnahme erfordert bewusst AUS→AN. Bewusstes
Automation-AUS führt genau einmal Safe-OFF aus und gibt danach alle Actors für
manuelle Bedienung frei. Vor einem erneuten Automation-AN muss die Kette
vollständig AUS sein.

Der globale G4-Floor-Guard und der Datenverlust-Shed gelten unverändert für
Kaskaden. Weil der unabhängige Load-Executor Kaskadenmitglieder nie besitzt,
delegiert der Coordinator beide Zwangsstopps an den `CascadeManager`; dieser
führt dieselbe vollständige geordnete Safe-OFF-Sequenz dwell-frei aus. Eine
bewusst deaktivierte oder bereits hands-off übergebene Kaskade wird dabei nicht
berührt. Zusätzlich wird der Kaskadenbesitz unter dem finalen Aktor-Lock erneut
geprüft: Ein noch vor der Kaskadenübernahme eingereihter generischer
Schaltauftrag darf keinen Kaskadenaktor mehr verändern. Als zweite, von der
Load-ID unabhängige Sicherung löst jede physische Entity-Schaltung ihren
Kaskadenbesitzer unmittelbar an der Service-Grenze neu auf. Nur ein Aufruf des
zuständigen `CascadeManager` mit passender Kaskaden-ID darf Root, Charge-Gates,
Outputs oder den Endlast-Aktor erreichen; alle generischen, Support-,
Kalibrierungs- und veralteten Hintergrundpfade bleiben fail-closed. Ein
unterbrochener Aux-Lauf beendet die aktuelle Episode und behält jede offene
Recovery-Schuld. Ein späterer Aux-Neustart am selben Tag benötigt den frischen
vollständigen Tagesnachweis aus den Planungsregeln.

Ein akzeptierter Aux-Wake und `proving` werden dem Core als bereits laufende
Episode gemeldet. Sonst verschiebt die Latest-First-Planung den eben begonnenen
Wake beim nächsten Rolling Replan wieder in die Zukunft und lässt den Executor
den Root-Eingang takten. Der Wake bleibt deshalb bis zum ersten sicheren
Proof-Grenzpunkt eine atomare, durch sein Wake-Timeout begrenzte
Aktor-Transition. Floor-/Daten-Safety, deaktivierte Mitglieder und manuelle
Besitzübergaben brechen weiterhin sofort ab. Während des anschließenden
Leistungsnachweises fließt schon Endlastenergie; auch dort darf ein Rolling
Replan nicht erneut eine vollständige Mindestlaufzeit als Startbedingung
verlangen. Ein danach tatsächlich zurückgezogener Plan wird im normalen
`running`-Zweig geordnet Safe-OFF geschaltet.

Eine erfolgreiche bewusste Aktivierung aus vollständig bestätigtem Safe-OFF
ist immer eine neue Besitzepisode. Alte Wake-Indizes, Deadlines, Claims,
Quellen- und Retry-Evidenz werden davor verworfen. `unknown`, `unavailable`
oder fehlende Actor-Zustände gelten dabei nicht als AUS. Aktivierung,
Deaktivierung, Fault-Reset und automatische Übergänge verwenden denselben
Kaskaden-Lock. Eine abgelehnte Aktivierung veröffentlicht einen
maschinenlesbaren Grund sowie gegebenenfalls die nicht als AUS bestätigten
Actor-Entity-IDs.

Automation-AN ist zustandsbezogen idempotent: Ist die Kaskade bereits aktiv,
beginnt derselbe AN-Aufruf keine neue Besitzepisode und verändert insbesondere
weder Phase, Wake-Deadline, Claims noch Proof-/Retry-Evidenz. Nur eine echte
Flanke von AUS nach AN darf nach bestätigt vollständigem Safe-OFF den
transienten Zustand verwerfen. Eine während der aktiven Episode beobachtete
Shared-Abweichung bleibt Eigentum des normalen Executor-Passes und führt wie
oben beschrieben zu `hands_off`; ein redundantes AN darf sie nicht als neue
Übernahme umdeuten.

## HA-Datenvertrag

Je Kaskade entstehen Root-Empfehlung, Mode/Forecast, kapazitätsgewichteter SOC,
Automation, Fault und Fault-Reset. Alte Member-/Leaf-Empfehlungen bleiben für
API-Kompatibilität vorhanden, sind aber AUS und tragen
`managed_by_cascade=<id>`.

Ein Hard-Fault veröffentlicht zusätzlich `fault_detail` mit Actor-Entity,
Zielzustand, zuletzt beobachtetem Zustand und Fehlerart. Deaktivierte, faulted
oder Hands-off-Kaskaden tragen sofort keine geplante Root-/Aux-Energie und
keinen ausführbaren Zeitplan mehr; im folgenden Replan werden ihre Mitglieder
und die Endlast auch aus der globalen SOC-Trajektorie entfernt. Dadurch können
nicht ausführbare Kaskaden weder Haus-SOC, Feed-in noch die Allokation anderer
Lasten verfälschen. Root-Empfehlung und Timeline sind bei Automation AUS leer;
der aktuelle Aggregat- und Mitglieder-SOC bleibt zur Diagnose sichtbar. Eine
spätere hypothetische Inbetriebnahme-Vorschau müsste als ausdrücklich
nicht-operativer, separater Rechenlauf veröffentlicht werden und dürfte nie in
die globale Trajektorie zurückwirken.

Der Forecast-Sensor veröffentlicht je Kaskade zusätzlich den lesbaren
`source_name`, `member_details`, `terminal_name` und `schedule`.
`member_details` enthält für jeden Speicher Namen, aktuellen SOC,
Kaskaden-Entladeziel und die vollständige SOC-Zeitreihe. `schedule` enthält genau ein
Block pro belegtem Plan-Slot mit
`start`/`end`, Root-Grenzenergie, terminaler Energie, den Quellen `root`/`aux`
und einer Aktivitätsliste. Aktivitäten unterscheiden Mitgliedsladung,
Mitgliedsentladung, benötigte AC-Ausgänge und die von Root bzw. einem
Aux-Speicher gelieferte Endlastenergie. Lade-/Entladeaktivitäten tragen die
Slot-SOC-Grenzen; Laden nennt zusätzlich die gespeicherte Energie.

Die SOC-Forecast-Card behandelt die gesamte Kaskade als Black Box: Eine
einzige Spur zeigt ausschließlich Slots mit Root-Aufnahme und deren Wh.
Aux-only-Aktivität und interne Details erscheinen dort nicht. Die separate
`battery-manager-cascade-card` zeichnet die SOC-Verläufe aller Mitglieder
übereinander und darunter Root-Aufnahme, Laden, Entladen, AC-Ausgänge sowie die
Endlast. Aktueller SOC und Entladeziel stehen direkt an jeder SOC-Kurve. Die
Kopfzeile beschreibt Phase sowie geplante Energie kurz als Speicher- bzw.
Root-Bezug; der Slot-Hover unterdrückt redundante Output- und Root-Angaben.
Damit bleibt der elektrische Außenbezug im Gesamtbild eindeutig, während die
eigene Kaskaden-Zeitspur den vollständigen internen Plan erklärt.
Kaskadenmitglieder bleiben aus den normalen Lastspuren ausgeschlossen.

Ein Slot ohne Mitgliedsladung und ohne Endlastsegment hat exakt `0 Wh`
Root-Energie. Insbesondere darf der interne Output-Overhead bei der
Sentinel-Position `deepest = -1` keine Phantomenergie erzeugen und B1 dadurch
ohne geplante Aktivität starten.

`cascade_plans` trennt geplante Root- und Aux-Energie sowie tatsächliche
Aux-Energie. `today_kwh`, `tomorrow_kwh` und `daily` teilen die geplante
Root-Grenzenergie nach lokalem Slot-Starttag auf und entsprechen damit dem
Tagesvertrag normaler Lastspuren. Root-/Surplusbilanz wird ausschließlich am
Root-Messpunkt gebucht; interne Zähler werden nie summiert. Topologie,
Automation, stabile Episode, Zielunterschreitungen, bestätigte Claims,
Fault/Hands-off, Retry, SOC-Cache und Tagesenergie werden persistiert;
Wake-Indizes, Wake-Deadlines, Proof-Fenster und HA-Offline-Zeit nicht. Nur die
beabsichtigte Aux-Quelle bleibt als nicht beweisender Restart-Hinweis erhalten,
damit der erste Rolling Plan die begonnene Episode weiter einplant. Beim
Neustart werden alte Claims nicht blind verwendet, sondern aus expliziten
Live-ON-/OFF-Zuständen aller Actors rekonstruiert. Dafür wartet der Executor
beim HA-Start höchstens 60 Sekunden auf deren Publikation. Ein zum frischen
Plan passender vollständiger Root-/Aux-Pfad bleibt unverändert; Aux durchläuft
ein neues 60-s-Leistungsfenster. Ein elektrisch geordneter Wake-Präfix wird ab
der ersten noch offenen Stufe mit einer neuen Gerätepublikation fortgesetzt.
Nur ein widersprüchlicher oder weiterhin unbekannter Vektor führt zum
geordneten Safe-OFF. Eine bewusst deaktivierte Kaskade bleibt unangetastet. Ein
laufender Wake veröffentlicht Modus, Member-Index, Baseline, Deadline und die
zuletzt akzeptierte Telemetrie-Evidenz ausschließlich für die Live-Diagnose.
Während eines verzögerten Output-Starts kommen Actor-Entity,
Wake-Deadline, Versuchszahl und letzter Actor-Fehler hinzu.

Die Diagnose weist für jede geplante Episode Nutzenergie und Übergangsbudget
getrennt aus. Dadurch bleibt sichtbar, ob ein kurzer Forecast-Abschnitt an
Wake-/Bestätigungszeit statt an fehlender PV oder Kapazität scheitert.

Die Executor-Phasen `recovering` und `complete` sind bei aktiver Automation
reine Diagnosen, keine Actor-Freigabe. Auch in diesen Phasen wird Safe-OFF
idempotent durchgesetzt. Sie sperren keinen späteren, durch einen frischen
Gesamttagesplan nachgewiesenen Aux-Abschnitt.
Entfällt ein Aux-Segment während `running`, wird der Pfad sofort beendet und
wechselt abhängig vom offenen Recovery-Ziel nach `recovering` oder `complete`.

## Migration und Betrieb

Die Runtime-Store-Hülle ist Version 2 und übernimmt die defensiv gelesene
v1-Payload. Vor Aktivierung im exklusiven Modus müssen Fremdautomationen auf
denselben Aktoren deaktiviert werden, insbesondere die bekannte
`automation.f2400_b_ac_out_off`; alternativ wird der betreffende Actor bewusst
auf `shared` gestellt.

Wird ein `shared` Actor entgegen dem letzten Manager-Claim extern geändert,
obwohl der frische Slotplan den geclaimten Zustand weiterhin benötigt, ist
Automation AUS bei `hands_off=true` eine kontrollierte Besitzübergabe und kein
Hard-Fault. Ein externes AUS, das bei einem inzwischen unbelegten Slot bereits
dem frischen Safe-OFF-Ziel entspricht, wird dagegen als Konvergenz übernommen;
das unterstützt insbesondere Root-Steckdosen mit normalem Nullleistungs-
Auto-Off. Bei aktivem `exclusive` bleibt eine Abweichung ein Fault. Der
Automation-Schalter veröffentlicht `phase`, `hands_off` und `fault` direkt als
Attribute; nach Safe-OFF kann `shared` bewusst wieder aktiviert werden. Bei
bewusst ausgeschalteter Automation bleiben nach dem einmaligen Safe-OFF auch
exklusive Actors manuell bedienbar; sie werden erst mit erfolgreichem
Automation-AN erneut geclaimt.
