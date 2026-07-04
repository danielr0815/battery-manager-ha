# Brand-Icons für home-assistant/brands

Damit HA/HACS das Icon anzeigen, müssen diese Dateien per Pull Request in das
zentrale [home-assistant/brands](https://github.com/home-assistant/brands)-Repo:

1. Fork von `home-assistant/brands` erstellen.
2. Ordner `custom_integrations/battery_manager/` anlegen und
   `icon.png` (256×256) + `icon@2x.png` (512×512) aus diesem Verzeichnis
   hineinkopieren.
3. PR eröffnen (Titel z. B. „Add battery_manager custom integration").
   Die CI prüft Größe/Format automatisch.

Nach dem Merge erscheint das Icon in HA und HACS automatisch (CDN-Cache kann
einige Stunden brauchen).
