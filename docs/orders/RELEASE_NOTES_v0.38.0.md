# v0.38.0 — Durchgängiger Entfeuchterbetrieb vor Speicherzyklen

Kaskaden planen den vollständigen Entfeuchterlauf einschließlich vorgezogenem Betrieb und zulässigem Lückenschluss vor Speicherladungen. Zusätzliche Tiefenentlade-/Ladezyklen setzen lückenlosen Endlastbetrieb während der PV-Zeit und mehr als 50 Wh Restexport voraus. Eine Aux-Neuplanung darf bereits akzeptierte direkte Entfeuchterläufe nicht verkürzen.

Kleine bestehende Abweichungen vom Speicher-Recovery-Ziel bis zum größeren Wert aus 50 Wh und 2 % Kapazität lösen keine Nachladung aus. Neue Speicherladungen benötigen mindestens 15 Minuten und 50 Wh; eine längere konfigurierte Mindestlaufzeit bleibt gültig. Neue Aux-Quellenläufe benötigen mindestens 15 Minuten. Bei der Fensteraufteilung bleiben nutzbare längere Abschnitte erhalten, während kurze Quellenreste im Akku verbleiben. Bereits laufende Restzeiten und harte Schutzgrenzen bleiben berücksichtigt.

Der Schalter für vorzeitige Einspeisung pausiert nun auch die automatische Planung über den gesamten Prognosezeitraum. Bei eingeschalteter Automatik müssen kontinuierliche Lasten vom Einspeisebeginn bis zum tatsächlich erreichten Batterie-Maximum lückenlos eingeplant sein; fehlende Bestätigungen steuerbarer Lasten sperren die Freigabe. Natürlich anfallender Überschuss und externe manuelle Sollwerte bleiben als physische Eingaben abgebildet. Vorgezogene Dauerläufe erhalten eine feste Peak-Toleranz von einem SOC-Prozentpunkt pro Tag, ohne die Reservegrenzen zu lockern.

Die Hausbatterie-Simulation berücksichtigt die Ausgangsverluste der Kaskaden bereits bei der Lastbuchung. Die Karte zeigt Teilstundenenden korrekt, macht Planungsgründe auch für Kaskaden sichtbar und stellt Mouseover-Hinweise innerhalb der verfügbaren Kartenbreite dar. Nach dem Zusammenfassen von Laufblöcken bleiben Begründungen korrekt zugeordnet.

Ein versionierter Diagnoseexport ermöglicht die exakte lokale Wiederholung eines Planner-Aufrufs. Die mitgelieferten Skripte vergleichen außerdem passende Messintervalle und trennen Laufzeit- von Leistungsabweichungen. Der weiterführende Optimierungsplan bleibt offen, insbesondere für genaue Start-/Wartezeiten, automatische Messsammlung und einen vollständigen Executor-Tagesreplay. Eine empirische Tagesabnahme der neuen Strategie steht nach Installation noch aus.

Validierung: 1.682 Python-Tests und 31 Frontendtests bestanden; Kernabdeckung 100 %, Gesamt-Abdeckung 97,09 %, alle 24 Modul-Gates erfüllt. Ruff, Formatprüfung, mypy und Lockfile-Prüfung erfolgreich. Die Golden-Snapshots bleiben unverändert.

Installation wie üblich über HACS, anschließend Home Assistant neu starten.
