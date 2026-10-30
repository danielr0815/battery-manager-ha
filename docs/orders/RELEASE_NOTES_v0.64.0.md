# v0.64.0 – BM-Steuerung OFF übergibt alle Geräteaktoren

OFF am Lastschalter „BM-Steuerung“ übergibt Eingang, Lade-Gate und Ausgang sofort an den Betreiber. BM sendet für dieses Gerät danach weder ON noch OFF, auch bei Schutz, Kalibrierung, verspäteten Bestätigungen und Neustart. Offene Anforderungen, Laufzeit-Timer, Eingangsbesitz und frühere Abschaltpflichten werden ohne Gerätebefehl widerrufen. OFF und die freigegebenen Aktorbindungen bleiben gespeichert; bei geänderter Zuordnung bleiben auch die alten Bindings geschützt.

Bereits an das Gerät gesendete Befehle lassen sich nicht zurückrufen und können nach der Übergabe noch eintreffen. Die Übergabe verspricht deshalb keine atomare Umschaltung am Gerät. ON erlaubt wieder frische normale Planung, übernimmt aber keinen manuell eingeschalteten Eingang allein aufgrund seines ON-Zustands.

Kaskaden verwenden ein freigegebenes Mitglied nicht weiter als Quelle und verwerfen dessen Quellen-, Wake- und Proof-Absichten. Das geordnete Stoppen betrifft nur zuvor beanspruchte Aktoren anderer weiterhin BM-aktiver Geräte. Der manuell übergebene Teil wird weder geschaltet noch als fehlgeschlagene OFF-Bestätigung behandelt; eine vollständige physische Trennung der manuell gehaltenen Kaskade wird nicht zugesichert. Nach ON gelten die normalen Kaskadenfreigaben erneut.

Validiert mit 1.022 Core-Tests bei 100 % Coverage und 3.248 Gesamttests bei 98,0262 % Zeilen-Coverage; jedes HA-Modul erreicht mindestens 95 %. Die ergänzende Branch-Prüfung, Ruff-, Format- und Typprüfungen sowie Frontend-Bundle, Lint, Format, 89 Node-Tests und 45 Browsertests bestehen. Das unabhängige Astra-/High-Review einschließlich aller Korrekturprüfungen ist abgeschlossen; alle Befunde sind geschlossen.
