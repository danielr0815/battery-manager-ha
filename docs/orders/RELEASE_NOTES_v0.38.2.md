# v0.38.2

Die Kaskadenkarte zeigt SOC, Leistung und kumulierte Energie jetzt in derselben
zeitlichen Auflösung wie die Aktivitätsbalken. Der SOC bleibt während geplanter
Pausen konstant und sinkt erst mit Beginn der Entladung. Auch Teilstunden,
Ladeenden und Quellenwechsel werden berücksichtigt. Die geplanten Energiemengen
und die Gerätesteuerung bleiben unverändert.

Der gemeinsame Zeitcursor funktioniert jetzt auch direkt auf den Balken für
Laden, Entladen und AC-Ausgang. Uhrzeit und geplanter Zustand sind per Maus,
Touch oder Pfeiltasten ablesbar. Ältere Pläne ohne vollständige Zeitangaben
werden weiterhin als Zeitfenstermittelwerte gekennzeichnet.

1713 Tests und 34 Frontend-Tests bestanden; Gesamt-Coverage 97,18 %, Kern 100 %
und alle HA-Module mindestens 95 %. Lint, Format und Typprüfung sind grün.
Die temporäre Vorschau wurde zusätzlich mit dem lokalen Playwright-MCP in
Home Assistant bei Desktop- und Mobilbreiten geprüft.
