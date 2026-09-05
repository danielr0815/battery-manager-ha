Die Kaskadenkarte nutzt breite Desktop-Dashboards kompakter: Übersichtsdiagramme sind auf 600 px, Detaildiagramme auf 900 px Breite begrenzt. Dadurch bleiben mehr Kennzahlen und Ablaufdetails gleichzeitig sichtbar. Die gemeinsame Auswahl „Heute / Morgen / Gesamter Plan“ steht jetzt oberhalb aller Diagramme. Schmale Ansichten bleiben responsiv.

Der Status „lädt Speicher“ wurde durch „Wiederaufladung ausstehend“ ersetzt: Der interne Zustand `recovering` bezeichnet eine noch offene Wiederaufladung und belegt keine laufende Ladung.

Deutsch und Englisch werden für Karten, Karteneditor, Kaskadenphasen, Auswahllisten, Aktionsfehler, Reparaturmeldungen, Benachrichtigungen und lesbare Exporttabellen konsistent unterstützt. Karten reagieren sofort auf einen Wechsel der HA-Benutzersprache. Push-Nachrichten und Texttabellen verwenden die HA-Systemsprache. Eigene Namen und technische Schnittstellen bleiben erhalten.

Geprüft mit 896 Python-Tests und 19 Frontendtests, 97,03 % Gesamt-Coverage, erfüllten Modul-Coverage-Grenzen sowie erfolgreichem Lint, Format- und Typcheck. Browserprüfung mit aktuellen HA-Anlagendaten in Deutsch und Englisch; Desktop- und Mobilbreiten geprüft.
