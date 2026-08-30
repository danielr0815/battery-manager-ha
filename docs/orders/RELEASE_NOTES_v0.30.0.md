# Battery Manager v0.30.0

Das Kaskaden-Entladeziel ist jetzt eine echte nutzbare Reservegrenze. Die
Powerstations dürfen den Entfeuchter mit ihrer Energie oberhalb dieses SOC
versorgen, auch wenn die heutige PV-Prognose keine Wiederaufladung mehr belegen
kann. Eine laufende Episode arbeitet kleine Restmengen weiter ab, wechselt am
Ziel von B1 zu B2 und beendet den Pfad am Ziel des letzten Speichers. Der
niedrigere Floor bleibt als zusätzliche Sicherheitsgrenze erhalten.

Eine bewusst ausgeschaltete Kaskaden-Automation führt weiterhin einmal
Safe-OFF aus, gibt die Actors danach aber für manuelle Bedienung frei. Auch ein
Shared-Fremdeingriff bleibt nun dauerhaft Hands-off und wird nicht beim
nächsten Coordinator-Lauf verzögert zurückgerollt. Faults bleiben
fail-closed; die automatische Wiederaufnahme verlangt weiterhin eine komplett
ausgeschaltete Kette und frische SOCs.

Core- und Home-Assistant-Regressionsprüfungen decken Zielentladung ohne PV,
den B1→B2-Wechsel, die Restmengen-Fortsetzung, manuelle Freigabe und dauerhaftes
Shared-Hands-off ab.
