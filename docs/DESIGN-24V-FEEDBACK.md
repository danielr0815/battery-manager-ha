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
