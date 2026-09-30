"""
Municipal Road Asset Parent Synchronization

GitHub-safe portfolio version.

Configuration is supplied through environment variables:
    ARCGIS_PROFILE   - name of an ArcGIS API for Python login profile
    ARCGIS_ITEM_ID   - item ID of the hosted feature service
    APPLY_CHANGES    - set to "true" to write updates; defaults to false

IMPORTANT:
    The script defaults to DRY-RUN mode. It will not modify hosted features
    unless APPLY_CHANGES=true is explicitly supplied.

Sample data and business rules in this portfolio project are fictional/demo only.
"""

import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from arcgis.gis import GIS


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ARCGIS_PROFILE = os.getenv("ARCGIS_PROFILE")
ITEM_ID = os.getenv("ARCGIS_ITEM_ID")
APPLY_CHANGES = os.getenv("APPLY_CHANGES", "false").strip().lower() == "true"

if not ARCGIS_PROFILE:
    raise RuntimeError(
        "ARCGIS_PROFILE is not set. Set it to the name of your saved ArcGIS login profile."
    )

if not ITEM_ID:
    raise RuntimeError(
        "ARCGIS_ITEM_ID is not set. Set it to the hosted feature service item ID."
    )

# Road Assets fields
# Note: AsssetID is the legacy internal field name used by this demo schema.
ASSET_ID_FIELD = "AsssetID"
ASSET_CONDITION_FIELD = "Condition"
ASSET_STATUS_FIELD = "Status"
LAST_INSPECTION_FIELD = "LastInspection"
NEXT_INSPECTION_FIELD = "NextInspection"

# Inspections fields
INSPECTION_ASSET_ID_FIELD = "AssetID"
INSPECTION_DATE_FIELD = "InspectionDate"
INSPECTION_CONDITION_FIELD = "Condition"
RECOMMENDED_ACTION_FIELD = "RecommendedAction"

INSPECTION_INTERVAL_DAYS = {
    "EXCELLENT": 365,
    "GOOD": 365,
    "FAIR": 180,
    "POOR": 30,
    "CRITICAL": 7,
}

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "outputs"
LOG_DIR = BASE_DIR / "logs"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

DRY_RUN_PATH = OUTPUT_DIR / "Parent_Asset_Sync_DryRun.csv"
AUDIT_LOG_PATH = LOG_DIR / "Parent_Asset_Sync_Log.csv"


# ---------------------------------------------------------------------------
# Business rules
# ---------------------------------------------------------------------------

def determine_status(current_status, condition, recommended_action):
    """Return the desired parent status for the latest inspection."""

    # Never automatically reactivate a retired asset.
    if current_status == "RETIRED":
        return "RETIRED"

    # Explicit recommended actions take priority.
    if recommended_action == "REPLACE":
        return "REPLACE"

    if recommended_action in ("REPAIR", "CLEAN"):
        return "MAINT"

    # Otherwise derive status from condition.
    if condition in ("CRITICAL", "POOR"):
        return "MAINT"

    if condition in ("EXCELLENT", "GOOD", "FAIR"):
        return "ACTIVE"

    # Preserve the current value if an unexpected code is encountered.
    return current_status


def same_date(a, b):
    """Compare ArcGIS date values at calendar-date precision."""

    if pd.isna(a) and pd.isna(b):
        return True

    if pd.isna(a) or pd.isna(b):
        return False

    return pd.Timestamp(a).date() == pd.Timestamp(b).date()


def to_python_datetime(value):
    if pd.isna(value):
        return None

    return pd.Timestamp(value).to_pydatetime()


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------

def refresh_data(road_assets, inspections):
    assets_fs = road_assets.query(
        where="1=1",
        out_fields="*",
        return_geometry=False,
    )

    inspections_fs = inspections.query(
        where="1=1",
        out_fields="*",
        return_geometry=False,
    )

    assets_df = assets_fs.sdf.copy()
    inspections_df = inspections_fs.sdf.copy()

    assets_df[LAST_INSPECTION_FIELD] = pd.to_datetime(
        assets_df[LAST_INSPECTION_FIELD],
        errors="coerce",
    )

    assets_df[NEXT_INSPECTION_FIELD] = pd.to_datetime(
        assets_df[NEXT_INSPECTION_FIELD],
        errors="coerce",
    )

    inspections_df[INSPECTION_DATE_FIELD] = pd.to_datetime(
        inspections_df[INSPECTION_DATE_FIELD],
        errors="coerce",
    )

    return assets_df, inspections_df


def build_sync_plan(assets_df, inspections_df, oid_field):
    valid_inspections = inspections_df[
        inspections_df[INSPECTION_DATE_FIELD].notna()
    ].copy()

    latest_inspections = (
        valid_inspections
        .sort_values(INSPECTION_DATE_FIELD)
        .groupby(INSPECTION_ASSET_ID_FIELD)
        .tail(1)
    )

    assets_lookup = assets_df.set_index(ASSET_ID_FIELD)
    sync_plan = []

    for _, inspection in latest_inspections.iterrows():
        asset_id = inspection[INSPECTION_ASSET_ID_FIELD]

        # Skip orphan inspection records.
        if asset_id not in assets_lookup.index:
            continue

        asset = assets_lookup.loc[asset_id]

        latest_date = inspection[INSPECTION_DATE_FIELD]
        latest_condition = inspection[INSPECTION_CONDITION_FIELD]
        recommended_action = inspection[RECOMMENDED_ACTION_FIELD]

        current_condition = asset[ASSET_CONDITION_FIELD]
        current_status = asset[ASSET_STATUS_FIELD]
        current_last_date = asset[LAST_INSPECTION_FIELD]
        current_next_date = asset[NEXT_INSPECTION_FIELD]

        desired_condition = latest_condition

        desired_status = determine_status(
            current_status,
            latest_condition,
            recommended_action,
        )

        interval_days = INSPECTION_INTERVAL_DAYS.get(latest_condition)

        if interval_days is not None:
            desired_next_date = (
                pd.Timestamp(latest_date)
                + pd.Timedelta(days=interval_days)
            )
        else:
            desired_next_date = current_next_date

        changes = []

        if current_condition != desired_condition:
            changes.append(
                f"Condition: {current_condition} -> {desired_condition}"
            )

        if current_status != desired_status:
            changes.append(
                f"Status: {current_status} -> {desired_status}"
            )

        if not same_date(current_last_date, latest_date):
            changes.append(
                f"LastInspection: {current_last_date} -> {latest_date}"
            )

        if not same_date(current_next_date, desired_next_date):
            changes.append(
                f"NextInspection: {current_next_date} -> {desired_next_date}"
            )

        if changes:
            sync_plan.append({
                "ObjectID": asset[oid_field],
                "AssetID": asset_id,
                "OldCondition": current_condition,
                "NewCondition": desired_condition,
                "OldStatus": current_status,
                "NewStatus": desired_status,
                "OldLastInspection": current_last_date,
                "NewLastInspection": latest_date,
                "OldNextInspection": current_next_date,
                "NewNextInspection": desired_next_date,
                "RecommendedAction": recommended_action,
                "Changes": " | ".join(changes),
            })

    columns = [
        "ObjectID",
        "AssetID",
        "OldCondition",
        "NewCondition",
        "OldStatus",
        "NewStatus",
        "OldLastInspection",
        "NewLastInspection",
        "OldNextInspection",
        "NewNextInspection",
        "RecommendedAction",
        "Changes",
    ]

    return pd.DataFrame(sync_plan, columns=columns)


def build_update_payload(sync_df, oid_field):
    updates = []

    for _, row in sync_df.iterrows():
        attributes = {
            oid_field: int(row["ObjectID"]),
        }

        if row["OldCondition"] != row["NewCondition"]:
            attributes[ASSET_CONDITION_FIELD] = row["NewCondition"]

        if row["OldStatus"] != row["NewStatus"]:
            attributes[ASSET_STATUS_FIELD] = row["NewStatus"]

        if not same_date(
            row["OldLastInspection"],
            row["NewLastInspection"],
        ):
            attributes[LAST_INSPECTION_FIELD] = to_python_datetime(
                row["NewLastInspection"]
            )

        if not same_date(
            row["OldNextInspection"],
            row["NewNextInspection"],
        ):
            attributes[NEXT_INSPECTION_FIELD] = to_python_datetime(
                row["NewNextInspection"]
            )

        # ObjectID alone is not an update.
        if len(attributes) > 1:
            updates.append({"attributes": attributes})

    return updates


def append_audit_log(sync_df):
    rows = []

    for _, row in sync_df.iterrows():
        rows.append({
            "RunTimestamp": datetime.now(),
            "AssetID": row["AssetID"],
            "Changes": row["Changes"],
        })

    if not rows:
        return

    log_df = pd.DataFrame(rows)

    log_df.to_csv(
        AUDIT_LOG_PATH,
        mode="a",
        header=not AUDIT_LOG_PATH.exists(),
        index=False,
    )


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def main():
    gis = GIS(profile=ARCGIS_PROFILE)

    item = gis.content.get(ITEM_ID)
    if item is None:
        raise RuntimeError(f"ArcGIS item {ITEM_ID} could not be found.")

    if not item.layers or not item.tables:
        raise RuntimeError(
            "The item must contain a Road Assets layer and an Inspections table."
        )

    road_assets = item.layers[0]
    inspections = item.tables[0]
    oid_field = road_assets.properties.objectIdField

    print(f"Item: {item.title}")
    print(f"Road Assets: {road_assets.properties.name}")
    print(f"Inspections: {inspections.properties.name}")
    print(f"Mode: {'APPLY CHANGES' if APPLY_CHANGES else 'DRY RUN'}")

    assets_df, inspections_df = refresh_data(
        road_assets,
        inspections,
    )

    print(f"Assets loaded: {len(assets_df)}")
    print(f"Inspections loaded: {len(inspections_df)}")

    sync_df = build_sync_plan(
        assets_df,
        inspections_df,
        oid_field,
    )

    print(f"Assets requiring synchronization: {len(sync_df)}")

    sync_df.to_csv(DRY_RUN_PATH, index=False)
    print(f"Dry-run report saved to: {DRY_RUN_PATH}")

    if sync_df.empty:
        print("Synchronization complete — no asset updates required.")
        return

    updates = build_update_payload(sync_df, oid_field)
    print(f"Prepared updates: {len(updates)}")

    if not APPLY_CHANGES:
        print(
            "DRY RUN ONLY — hosted features were not modified. "
            "Set APPLY_CHANGES=true to enable writes."
        )
        return

    result = road_assets.edit_features(
        updates=updates,
        rollback_on_failure=True,
    )

    failed_updates = [
        row
        for row in result.get("updateResults", [])
        if not row.get("success")
    ]

    if failed_updates:
        print("FAILED UPDATES")
        for failure in failed_updates:
            print(failure)
        raise RuntimeError(
            f"{len(failed_updates)} hosted feature update(s) failed."
        )

    print(
        f"Success: "
        f"{len(result.get('updateResults', []))} asset(s) updated."
    )

    append_audit_log(sync_df)
    print(f"Audit log updated: {AUDIT_LOG_PATH}")


if __name__ == "__main__":
    main()
