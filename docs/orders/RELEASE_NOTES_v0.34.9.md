# Battery Manager v0.34.9

Diese Version korrigiert die tagesweite Planung und das Aufwecken der
Speicher-Kaskade, stabilisiert den Inverter-Schwellwert vor Solarüberschuss und
behebt zwei Diagnosefehler.

## Kaskadenplanung

- Fossibot-Ladung darf nur den Speicherplatz verwenden, der im betreffenden
  Zeitpunkt bereits vorhanden ist. Eine spätere Aux-Entladung erzeugt keinen
  rückwirkenden Lade-Headroom mehr.
- Kann morgendliche Entladung der Kaskade späteren Solar-Export vermeiden,
  plant der Battery Manager sie vor der Solaraufnahme ein. Direkte Endlast,
  gemeinsame Recovery aller Mitglieder auf 50 %, normaler Top-up und
  vorzeitige Einspeisung behalten ihre dokumentierte Prioritätsfolge.
- Mehrere Aux-Episoden am selben Tag, parallele Recovery und fragmentierte
  freie Zeitfenster werden gemeinsam bewertet. Morgige Energie kann die
  heutige Recovery nicht erfüllen.
- Gelernte Ladeleistung, Hardware-Limit, SOC-Rechnung und Root-Energie nutzen
  dieselbe effektive Leistung. Neue Aux-Episoden reservieren ihre
  Actor-/Wake-/Proof-Zeit ohne sie als Endlastenergie zu zählen.

## Diagnose

- Der Hover-Wert für vorzeitige Einspeisung gehört wieder zum richtigen
  physischen Slot; die Einzelstunden ergeben die angezeigte Tagessumme.
- Der Prognose-Watchdog vergleicht ungelernte Stunden mit dem wirklich
  verwendeten statischen Fallback. Das gilt auch im Urlaubsmodus und für
  variable Lastfenster über Mitternacht.

## Zuverlässiger Fossibot-Wake

- Eine neue Input-/Output-/SOC-Publikation zeigt zunächst nur, dass der
  Fossibot versorgt wird. Der interne AC-Ausgang gilt erst nach bestätigtem
  Zielzustand als schaltbereit.
- Antwortet der AC-Ausgang beim ersten Versuch noch nicht, bleibt der bereits
  sichere Root-Wake bestehen. Der Battery Manager versucht den offenen Ausgang
  in folgenden Zyklen bis zum konfigurierten Mitglieds-Wake-Timeout erneut,
  statt Root nach dem ersten 30-Sekunden-Timeout auszuschalten. Ein garantierter
  Refresh an der Wake-Deadline verhindert dabei, dass ein während des Wartens
  eingetroffenes SOC-Update im Debounce verloren geht.
- Ein dauerhaft unerreichbarer Ausgang endet weiterhin begrenzt mit Fault und
  geordnetem Safe-OFF. Die Live-Diagnose nennt Ausgang, Deadline,
  Versuchszahl und letzte Fehlerursache.

## Stabiler Inverter-Schwellwert vor Solarüberschuss

- Die Stärke eines sicher erwarteten Solar-Clippings wird jetzt über die ganze
  zusammenhängende Episode am vollen Hausakku gemessen. Zuvor zählte nur der
  erste – häufig kleine – Stundenrest.
- Dadurch bleibt T* vor einem mehrstündigen Überschuss im Nutzen-/Entlade-Regime,
  statt wegen eines schwachen letzten Prognosetags zwischen 20 % und etwa
  60–64 % zu springen. Die P10-/Unsicherheitsabsicherung bleibt erhalten.

Validiert mit 833 grünen Tests, 100 % Core-Coverage und 95,31 %
Gesamt-Coverage sowie Ruff, Formatter, mypy und JavaScript-Syntaxprüfung.
