# Design des automatischen Einspeisestopps

Der [freigegebene Vertrag](F-SAFE-TRANSITIONS.md#einspeise-nullbefehl) und die
[Anforderungen BM-ENER-006 bis BM-ENER-314](anforderungen/04-energie.md#automatischer-einspeise-nullbefehl)
gelten für den BM-geführten Einspeise-Sollwerthelfer. Planwerte bleiben in
positiven Exportwatt, der HA-Dienst erhält negative Exportwatt.

## Auftragsfolge

Ein Schutzgrund bucht das Nullziel synchron vor jeder blockierenden Arbeit.
Der Einspeiseaktor verwendet weiter die gemeinsame Aktorsperre. Er prüft nach
Erwerb der Sperre Sollwertzuordnung, manuellen Besitz, Nullpflicht und die
bisherigen Freigaben. Der Schutzpfad bucht nur im gemeinsamen Zustand, wenn
bereits ein Einspeiseauftrag arbeitet. Der laufende Auftrag prüft anschließend
unter derselben Sperre, ob die Nullpflicht übernommen werden muss. Damit kann
der Schutzpfad aus einem rückgekoppelten Dienst heraus aufgerufen werden.

Vor Versand erhält jeder eigene Sollwertauftrag einen HA-Kontext. Die Nullpflicht
ist an die betroffene Entität gebunden. Sie überdauert den Schutzgrund und
bleibt bei ausstehender oder fehlgeschlagener Rückmeldung erhalten. Ein
unbestätigter älterer Export kann bereits durch einen Nullauftrag begrenzt
werden. Seine offene Reihenfolge bleibt dabei Teil der Nullpflicht. Eine aktuelle,
real gemeldete Null nach Klärung der Reihenfolge schließt sie ab. Eine bereits
stabile Null ohne offenen Export erfüllt die Bedingung unmittelbar.

Die Zuordnung eigener widerrufener Aufträge bleibt für spätere Istzustände
erhalten. Ein aktueller alter Exportwert kann die Nullpflicht erneut öffnen.
Ein zwischenzeitlich angezeigter historischer Zustand wird gegen den
gegenwärtigen Zustand derselben Entität geprüft. Eine bestätigte Übernahme durch
den Bediener stoppt weitere automatische Aufträge an diese Entität. Ein
ungeklärter älterer eigener Auftrag bleibt diagnostizierbar.

Offene Stopps werden zusammen mit dem bisherigen Sollwertbesitz gespeichert.
Bei Wiederkehr des Helfers oder einer regulären Schutz-/Planungsprüfung kann
der Stopp ohne neuen erfolgreichen Plan weiterlaufen. Eine neue Zuordnung
erhält eine eigene Freigabeprüfung erst nach Klärung eines noch offenen
BM-geführten alten Sollwerts. Die Diagnose enthält Entität, Auslöser und offene
Bestätigung.

Nach einem Neustart prüft die bisherige Übernahmelogik zuerst auf eine
Änderung während der Offline-Zeit. Belegt die gespeicherte Auftragshistorie
den weiterhin anliegenden eigenen Export am selben Helfer, stellt der BM
vor weiterer Freigabe geordnete Null her. So bleibt auch ein Schutzstopp
wirksam, der beim Abbruch unmittelbar vor dem verzögerten Speichern verloren
ging. Bei einem gespeicherten Bedienerbesitz hat die Übernahmelogik Vorrang.

## Nachweise

HA-Tests halten die gemeinsame Aktorsperre oder einen Dienst gezielt offen.
Sie prüfen Schutz vor Versand, einen kurzen Schutz nach Versand, Null-Echo
bei offenem Export, verspätete Wirkung nach neuem Auftrag, Rückmeldungen an
einer anderen Entität und manuelle Übernahme. Zeit und Zustandsereignisse werden
explizit gesteuert. Die vorhandenen Fälle zu PV-Nachweis, SOC-Schutz,
Laufzeitschalter, Trim und Neustart prüfen den bestehenden Ausführungspfad.
