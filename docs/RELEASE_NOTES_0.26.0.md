# Battery Manager 0.26.0

Version 0.26.0 führt lineare Speicher-Kaskaden ein, beispielsweise
`Root → Fossibot B1 → Fossibot B2 → Entfeuchter`. Battery Manager plant die
Endlast über Root/PV, die Speicher in physischer Reihenfolge und – unter den
bisherigen Gates – die Hausbatterie. Jeder verwendete Speicher muss sein
Recovery-Ziel noch am selben lokalen PV-Tag erreichen können.

Storage-Load-Subentries erhalten Output-, Floor-, Recovery-, Handover- und
Actor-Felder. Danach wird eine Kaskaden-Subentry mit Root→Leaf-Reihenfolge und
Endlast angelegt. Planung und Vorschau laufen sofort; die Automation bleibt bis
zur bewussten Aktivierung AUS.

Bei exklusiver Actor-Nutzung muss insbesondere
`automation.f2400_b_ac_out_off` deaktiviert werden. Alternativ kann der Actor
bewusst `shared` sein; nach einer Fremdänderung gilt dann vollständiges
Hands-off bis zu einem bewussten AUS→AN.

Neue Entities zeigen Automation, Root-Empfehlung, Modus/Quelle, gewichteten
SOC, Fault und Reset. `custom:battery-manager-cascade-card` liest den
SOC-Prognose-Sensor.
