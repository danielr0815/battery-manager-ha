# Ausführbarkeit und Entscheidungsgründe (0.41.0)

## Vertrag

1. Bekannte Mindestpausen bleiben harte Startgrenzen. Bei normalen gesteuerten
   kontinuierlichen Lasten begrenzt zusätzlich die Stabilitätswartezeit nur neue
   Pass-3-Vorläufe. Direkter Überschuss bleibt nutzbar. Die Frist stammt aus dem
   ersten passenden Vorschlag plus zehn Minuten; drei passende Vorschläge
   bleiben zusätzlich erforderlich. Neuplanung verschiebt eine unveränderte
   Frist nicht; ein anderer Block beginnt neu. Laufende Lasten warten nicht erneut.
2. Für tatsächlich eingeschaltete normale Lasten wird die noch bindende
   Mindestlaufzeit an den Kern übergeben und vor neuen Buchungen reserviert.
   Energie-, Reserve-, Tagesziel- und Versorgbarkeitsprüfungen gelten weiter.
   Unversorgbare Fortsetzung wird ausdrücklich abgelehnt und rechtfertigt
   keinen neuen Netzbezug. Schutzabschaltung und ein erreichtes Ladeziel mit
   Ladefreigabe haben Vorrang. Physische Mindestlaufzeit und eingefrorenes
   geplantes Laufende sind verschiedene Grenzen.
3. Vorschau und Diagnose unterscheiden frühesten Start, frühestes reguläres
   Laufende, fehlende stabile Vorschläge und unbestätigte Hardware. Kaskaden
   zeigen laufende Wake-/Proof-/Recovery-/Neustartphasen mit vorhandener Frist.
   Eine solche Frist ist eine Prüfgrenze, kein zugesicherter Laststart.
   Atomare Quellenwechsel bleiben unter der bestehenden Executor-Verantwortung.
4. Eine Kaskaden-Endlast ohne eigenen Stecker gilt für automatische Einspeisung
   erst mit bestätigtem letzten AC-Ausgang als aktiv. Laufende Übergänge und
   fehlende Rückmeldungen dürfen keine Freigabe liefern.
5. Einspeisungsgründe stammen aus derselben Kernentscheidung: deaktiviert,
   pausiert, manuell, kein Restexport, fehlender Überschuss, SOC-Grenze,
   Zeitfenster, Endlast-/Maximum-Nachweis, Stressreserve oder gebucht.
   Karte und Diagnose zeigen diese Gründe mit dem zugehörigen Planstand.
6. Keine Toleranzänderung, freie Startoptimierung oder vorsorgliche
   Tankabschaltung. Unbekannte Hardware-Reaktionszeiten werden nicht erfunden.

## Abnahme

Regressionen prüfen Fristen, Laufzeitenergie ohne Doppelbuchung, Schutzvorrang,
Neustart, Vorschlagswechsel, ausstehende Bestätigung und deutsche/englische
Darstellung. Bekannte neutrale Kerneingaben behalten ihre bisherigen Energie-
und Schaltentscheidungen; neue Diagnosefelder erweitern den Replay-Vertrag.
Produktive Wartezeiten werden ausschließlich virtuell gesteuert.
