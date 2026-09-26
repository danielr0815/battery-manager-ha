# Coding Guidelines

Diese Regeln gelten für neue Änderungen und gezielte Refactorings. Entscheidend
sind verständliche Verträge und nachgewiesenes Verhalten, nicht möglichst viele
Abstraktionen oder Testzeilen.

- **Verantwortung:** Physik, Kandidaten und Entscheidungsregeln gehören in den
  HA-freien Kern. I/O, Gerätestatus, Zeitgeber und Persistenz bleiben in der
  Integrationsschicht. Der Coordinator bestimmt die Reihenfolge; fachliche
  Teilaufgaben bekommen benannte Module und explizite Eingaben.
- **Typen:** Für fachliche Befehle und Ergebnisse Dataclasses, für feste
  JSON-Strukturen konkrete TypedDicts verwenden. Positionsabhängige Tupel und
  implizite äußere Schleifenvariablen vermeiden. `Mapping` genügt für lesende
  Verbraucher. Keine pauschalen mypy-Ausschlüsse; die gesamte Integration wird
  geprüft. Öffentliche Snapshots ersetzen Zugriffe auf private Manager-Dictionaries.
- **Unveränderlichkeit:** Ein `frozen`-Objekt darf veränderliche fremde Maps nicht
  ungeprüft übernehmen. Ergebnisse defensiv kopieren und schreibgeschützt
  veröffentlichen. Hypothesen mit `dataclasses.replace` aus gültigen Eingaben
  ableiten, damit neue Felder automatisch erhalten bleiben.
- **Fachliche Zahlen:** Grenzwerte, Fristen und Toleranzen einmal mit Einheit und
  Begründung definieren. Namen wie `_WH`, `_W`, `_H`, `_S` und `_PERCENT` machen
  Dimensionen sichtbar. Null, eins und mathematische Einheitenumrechnung müssen
  nicht künstlich benannt werden. Historische und Livewerte nutzen dieselbe
  Normalisierung; unbekannt ist kein Messwert null.
- **Zeit:** Persistierte Zeitpunkte tragen eine Zeitzone. Dauerberechnungen
  verwenden reale Intervalle; Tageszuordnung verwendet die ausdrücklich benannte
  HA- bzw. Archivzeitzone. Browserlokale Zeit ist keine Planungsquelle.
- **Aktorgrenze:** Gewünschter Zustand, ausstehender Auftrag und bestätigter
  Zustand sind getrennte Begriffe. Nach jedem `await` können Freigabe und Besitz
  veraltet sein. Rückmeldungen begrenzen, Aufgaben beim Entladen beenden und
  Schutzabschaltungen nicht hinter fremden Bestätigungen blockieren.
- **Persistenz:** Neue Strukturen besitzen Version, Validierung und
  Migrationsvertrag. Rekonstruierbare Lerndaten dürfen HA-Setup nicht verhindern.
  Einen gültigen Stand erst nach vollständiger Berechnung atomar ersetzen.
- **Tests:** Jeder Test beobachtet einen Fachvertrag, eine Zustandsänderung,
  Energie-/Zeitbilanz, Fehlerklasse oder Gerätefolge. Zunächst das Gegenbeispiel
  reproduzieren, dann beheben. Keine Quelltextfragmente, tautologischen Assertions
  oder bloßen Aufrufe zum Erreichen einer Coverage-Zahl. Mocks liegen an I/O-
  Grenzen; Golden-Snapshots schützen Verhaltensgleichheit beim Refactoring.
- **Testzeit:** Produktive Sekunden-/Minutenfristen nie real abwarten. Uhr oder
  Timeout kontrollieren und den produktiven Fristwert separat prüfen. Async-Tests
  synchronisieren sich auf beobachtete Ereignisse statt zufällige Sleeps.
- **Frontend:** Quellmodule bearbeiten, Bundle reproduzierbar bauen. Gemeinsame
  Übersetzungen, Styles und Zeit-/Energiefunktionen zentral halten. DOM-Verhalten,
  Fokus, Scrollen und Rendering zusätzlich im lokalen Browser ausführen.
- **Dokumentation:** Kommentare erklären Gründe und Invarianten. Änderungen an
  Verhalten, Konfiguration oder Speicherformat aktualisieren den aktuellen
  Vertrag und Changelog. Historische Entscheidungen klar kennzeichnen.

Verbindliche Befehle stehen in [CONTRIBUTING](../CONTRIBUTING.md).
