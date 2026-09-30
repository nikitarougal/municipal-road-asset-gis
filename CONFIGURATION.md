# Running the Python scripts locally

The public scripts do not contain an ArcGIS username, password, item ID, or
absolute local path.

Set these environment variables before running:

- `ARCGIS_PROFILE` — your saved ArcGIS API for Python profile name
- `ARCGIS_ITEM_ID` — your hosted feature service item ID
- `APPLY_CHANGES` — optional; only used by `municipal_asset_sync.py`

`municipal_asset_sync.py` defaults to dry-run mode. To permit hosted feature
updates, explicitly set `APPLY_CHANGES=true`.

Example on Windows Command Prompt:

```bat
set ARCGIS_PROFILE=municipal_gis
set ARCGIS_ITEM_ID=YOUR_ITEM_ID
set APPLY_CHANGES=false
```