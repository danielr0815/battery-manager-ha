# Design der 24-V-Umschaltung

Fachgrundlage sind D1 und D2 sowie BM-ENER-001 bis 005, 301 bis 307 und 315, 321 und 322. Beide automatischen Versorgungspfade verwenden die gemeinsame Umschaltfolge des Coordinators. Für D3 gilt der gesonderte Entwurf zur Einspeisung.

## Rückmeldung je Kanal

Die Konfiguration nimmt pro 24-V-Aktor entweder eine unabhängige Binärsensor-Entität oder ein KNX-Statuspaar aus Gruppenadresse und physikalischer Absenderadresse auf. Eine explizite Zuordnung bindet den Nachweis an den Aktor. Für nachweislich rückmeldende Integrationen kann der Betreiber die eigene Schalterentität ausdrücklich als Nachweis angeben. Nach einem Update bleibt der automatische Abschluss bei registrierten Aktoren ohne nachgewiesene Zuordnung gesperrt. Bei KNX ist die explizite Zuordnung erforderlich, weil die Schalterentität auf den geschriebenen Sollzustand vorlaufen kann. Die Zuordnung wird für jede Anlage separat eingetragen.

Der Adapter hört auf den KNX-Dispatcher von Home Assistant. Er übernimmt eingehende GroupValueWrite- und GroupValueResponse-Werte für das vereinbarte Adresspaar. Er führt einen Zeitstempel und eine Kennung der letzten bestätigten Zustandsänderung. Ein neuer eigener Auftrag braucht einen Bericht nach seiner Erteilung. Die Auswertung speichert keine Buszustände über einen Neustart. Ein fehlender Eingang und eine widersprüchliche Meldung liefern keinen Nachweis. Der zwischengespeicherte KNX-Bericht und die Entitätsrückmeldung verlieren nach 65 Minuten ihre Gültigkeit. Dieser Zeitraum deckt die dokumentierte stündliche Statusabfrage ab. Die erste Bestätigung für einen bereits erteilten Auftrag muss nach dessen Versand eintreffen. Verliert Home Assistant die Verbindung zum KNX-Schalter, verfallen die zwischengespeicherten Kanalwerte. Die Fortsetzung braucht ein neues Statustelegramm. Ereignisse des Nachweises stoßen bei einem relevanten Zustandswechsel oder der Klärung eines Auftrags die normale Auswertung an.

## Umschaltfolge

Der Manager führt offene Aufträge für beide 24-V-Aktoren in beiden automatischen Pfaden. Erfolg des HA-Dienstes ist eine Transportinformation. Erst belegtes Ziel-EIN startet die Überlappung. Bei Zustandsänderung am Ziel verfällt das begonnene Intervall. Unmittelbar vor AUS prüft die gemeinsame Folge Nachweis und aktuelle Freigabe. Sie meldet den Abschluss erst nach belegtem AUS und erneuter Prüfung des Zielzustands. Ein Abschaltauftrag an einen Aktor mit expliziter Rückmeldung wird vor dem Versand dauerhaft gebucht. Rückmeldungen während der Buchung können ihn noch nicht bestätigen. Die Zuordnung wird bei einem Aktorwechsel verworfen, wenn das Formular die bisherige Rückmeldung unverändert übernimmt. Die Rückfallprüfung bei Netzausfall startet unmittelbar. Fällt ein ungeklärtes AUS und ein neues EIN am selben Aktor zusammen, bleibt der Alternativpfad verfügbar, bis die Reihenfolge geklärt ist.

Die Tests simulieren Schalteranzeige vor Telegramm, fremden Absender, Wechsel des Zielzustands während der Überlappung und ausbleibende AUS-Rückmeldung. Sie benötigen keine Busverbindung.

## Konfiguration

Im Abschnitt zur Versorgungssteuerung sind Statusgruppe und physikalischer Absender als `Gruppenadresse@Absender` einzutragen. Eine durch die ETS-Zuordnung belegte Statusgruppe ist die Grundlage. Die Schaltadresse bleibt in der KNX-Schalterkonfiguration.


## Erneute Auswertung eines offenen KNX-EIN

Im koordinierten Betrieb bleibt der offene KNX-EIN-Auftrag des DC/DC-Wandlers bis zum qualifizierten Bericht oder bis zur Feststellung im Auftragsbuch, dass die Quelle weiterhin AUS ist. Diese Feststellung ist für abgeschlossene Dienstaufrufe und wiederhergestellte Aufträge möglich, sobald eine AUS-Meldung vom zugeordneten KNX-Aktor zeitlich nach dem Auftrag liegt. Der Auftrag muss mindestens 30 Sekunden alt sein. Die Meldung darf höchstens fünf Minuten alt sein. Damit kann die reguläre Planung auch einen kurz nach dem Auftrag eintreffenden AUS-Bericht nach Fristablauf berücksichtigen. Ein unverändert gemeldetes AUS kann die normale Auswertung wecken.

Der Manager markiert den EIN-Auftrag als unerfüllt und hält ihn für die Übergabe der Schalthoheit weiterhin offen. Bei weiterhin gewünschtem EIN sendet die nächste zulässige Auswertung einen neuen EIN-Auftrag mit neuer Frist. Bei gewünschtem AUS bleibt es beim bestätigten physischen AUS. Falls der alte EIN-Auftrag doch noch wirksam wird, führt die nächste Auswertung den dann erforderlichen Wechsel nach den bestehenden Regeln aus. Insbesondere schützt ein spät wirksames EIN keine Abschaltentscheidung ohne Zielnachweis und volle Überlappung. Die Wiederaufnahme betrifft ausschließlich offene EIN-Aufträge. Die KNX-Verriegelung wirkt unabhängig als anlagenseitige Sicherung gegen das Abschalten der letzten Quelle.

Ein wiederholtes EIN kann wegen fehlender Einzelquittungen mit einer einzigen EIN-Meldung den Zustand der Schiene belegen. Der offene Herkunftsnachweis bleibt im Laufzeitspeicher für eine manuelle Übergabe erhalten. Erst der qualifizierte Wechsel von EIN nach AUS nach einem Auftrag des gemeinsamen automatischen Übergangs beendet diese Sperre. Dafür gilt die Reihenfolge der Schalttelegramme desselben KNX-Kanals über dieselbe Verbindung. Ein vor dem Versand abgebrochener Auftrag belässt den bisherigen Nachweis im Auftragsbuch.

Der Marker wird vor dem erneuten EIN dauerhaft und überprüfbar geschrieben. Für seine Freigabe muss der laufende Prozess unmittelbar vor dem bewachten AUS noch EIN beobachtet haben. Ein neuer AUS-Zustand muss nach Versand beginnen. Nach einem Neustart oder nach einem erkannten KNX-Verbindungsverlust kann frisches AUS die physische Lage klären. Ohne den beobachteten Wechsel bleibt der Nachweis für die manuelle Übergabe offen.
