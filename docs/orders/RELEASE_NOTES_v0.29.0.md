# Battery Manager v0.29.0

Die SOC-Prognose behandelt eine Speicher-Kaskade jetzt konsequent als Black
Box. Ihre gemeinsame Spur zeigt nur noch, wann die Kaskade am Root Energie
aufnimmt und wie viele Wh dort geplant sind. Interne Aux-Flüsse und einzelne
Fossibot-Aktivitäten vermischen sich damit nicht mehr mit der Gesamtprognose.

Die separate Kaskadenkarte erhält dafür einen vollständigen grafischen
6–96-Stunden-Zeitplan. Eigene Zeilen zeigen Root-Aufnahme, Laden, Entladen und
AC-Ausgang jedes Speichers sowie die Endlast; der Zeit-Hover nennt Energie,
Root-/Aux-Quelle, gespeicherte Energie und den geplanten SOC-Verlauf. Der
Backend-Vertrag liefert den benötigten Ausgangspfad explizit, und ältere
Karten ohne konfigurierte Entität finden den Forecast-Sensor automatisch.

Ein zurückgezogener Aux-Plan und die Phase `complete` erzwingen nun weiterhin
idempotentes Safe-OFF. Dadurch bleiben Fossibot-Ausgänge und Endlast nach dem
Planende nicht mehr eingeschaltet. Ein bereits extern ausgeschalteter Shared
Actor wird dabei als Konvergenz übernommen, wenn der frische Plan ebenfalls
Safe-OFF verlangt; die Diagnosephase `recovering` oder `complete` bleibt
trotzdem sichtbar.

751 Tests sind grün; Core-Coverage bleibt bei 100 %, die Gesamt-Coverage liegt
bei 95,20 %. Ruff, Formatprüfung, mypy, JavaScript-Syntaxprüfung und der
Frontend-DOM-Smoke-Test sind ebenfalls erfolgreich.
