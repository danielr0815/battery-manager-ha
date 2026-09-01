# Battery Manager v0.33.1

Die Bad-Kaskade bevorzugte bei knappem PV-Überschuss bisher das weitere Laden
der Fossibots und ließ dafür den Entfeuchter pausieren. Ursache war, dass die
allgemeine Lastpriorität unverändert auf die internen Kaskadenteilnehmer
angewendet wurde. Die Kaskade belegt nun weiterhin ihre konfigurierte globale
Prioritätsposition, plant dort aber zuerst die direkte Endlast und lädt die
Speicher nur mit dem verbleibenden Überschuss. Das vermeidet unnötige
AC→Speicher→AC-Verluste; die nachgewiesene Rückkehr auf mindestens 50 Prozent
nach einer exportgestützten Tiefentladung bleibt verpflichtend.

Die Energieanzeige der Kaskade zeigte außerdem nur die Summe des gesamten
Prognosehorizonts. Root-Energie wird jetzt wie bei normalen Lasten nach lokalem
Kalendertag aufgeteilt. Forecast- und Kaskadenkarte zeigen damit verständliche
Werte für heute und morgen, während interne Lade- und Aux-Energie weiterhin
nicht doppelt zur Root-Bilanz addiert werden.

778 Tests sind grün. Der reine Planner-Core bleibt vollständig abgedeckt; die
Gesamt-Coverage beträgt 95,29 Prozent.
