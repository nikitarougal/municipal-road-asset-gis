"""
Municipal Road Asset QA/QC Automation

GitHub-safe portfolio version.

Configuration is supplied through environment variables:
    ARCGIS_PROFILE  - name of an ArcGIS API for Python login profile
    ARCGIS_ITEM_ID  - item ID of the hosted feature service

The script reads hosted Road Assets and Inspections data, runs QA/QC checks,
writes a timestamped CSV report, and appends an execution log.

Sample data and business rules in this portfolio project are fictional/demo only.
"""

import os
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
from arcgis.gis import GIS


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ARCGIS_PROFILE = os.getenv("ARCGIS_PROFILE")
ITEM_ID = os.getenv("ARCGIS_ITEM_ID")

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
NEXT_INSPECTION_FIELD = "NextInspection"

# Inspections fields
INSPECTION_ID_FIELD = "InspectionID"
INSPECTION_ASSET_ID_FIELD = "AssetID"
INSPECTION_DATE_FIELD = "InspectionDate"
INSPECTION_CONDITION_FIELD = "Condition"

# Repository-relative folders. Assumes this file is stored in /python.
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "outputs"
LOG_DIR = BASE_DIR / "logs"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_PATH = LOG_DIR / "qaqc_scheduler.log"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(message):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"

    print(line)

    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(line + "\n")


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def main():
    log("QA/QC job started")

    gis = GIS(profile=ARCGIS_PROFILE)
    username = gis.users.me.username if gis.users.me else "<unknown>"
    log(f"Connected to ArcGIS Online as {username}")

    item = gis.content.get(ITEM_ID)
    if item is None:
        raise RuntimeError(f"ArcGIS item {ITEM_ID} could not be found.")

    if not item.layers:
        raise RuntimeError("No feature layers were found in the item.")

    if not item.tables:
        raise RuntimeError("No related tables were found in the item.")

    log(f"Opened item: {item.title}")

    road_assets = item.layers[0]
    inspections = item.tables[0]

    log(f"Road Assets layer: {road_assets.properties.name}")
    log(f"Inspections table: {inspections.properties.name}")

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

    log(f"Assets loaded: {len(assets_df)}")
    log(f"Inspections loaded: {len(inspections_df)}")

    assets_df[NEXT_INSPECTION_FIELD] = pd.to_datetime(
        assets_df[NEXT_INSPECTION_FIELD],
        errors="coerce",
    )

    inspections_df[INSPECTION_DATE_FIELD] = pd.to_datetime(
        inspections_df[INSPECTION_DATE_FIELD],
        errors="coerce",
    )

    # QA Check 1: Duplicate Asset IDs
    duplicate_assets = assets_df[
        assets_df.duplicated(
            subset=[ASSET_ID_FIELD],
            keep=False,
        )
    ]

    # QA Check 2: Duplicate Inspection IDs
    duplicate_inspections = inspections_df[
        inspections_df.duplicated(
            subset=[INSPECTION_ID_FIELD],
            keep=False,
        )
    ]

    # QA Check 3: Assets without inspection history
    assets_with_inspections = set(
        inspections_df[INSPECTION_ASSET_ID_FIELD].dropna()
    )

    assets_without_inspections = assets_df[
        ~assets_df[ASSET_ID_FIELD].isin(assets_with_inspections)
    ]

    # QA Check 4: Orphan inspections
    valid_assets = set(
        assets_df[ASSET_ID_FIELD].dropna()
    )

    orphan_inspections = inspections_df[
        ~inspections_df[INSPECTION_ASSET_ID_FIELD].isin(valid_assets)
    ]

    # QA Check 5: Missing inspection dates
    missing_inspection_dates = inspections_df[
        inspections_df[INSPECTION_DATE_FIELD].isna()
    ]

    # QA Check 6: Overdue inspections
    today = pd.Timestamp.now()

    overdue_assets = assets_df[
        assets_df[NEXT_INSPECTION_FIELD].notna()
        & (assets_df[NEXT_INSPECTION_FIELD] < today)
    ]

    # QA Check 7: Condition/status consistency
    condition_status_issues = assets_df[
        (
            (assets_df[ASSET_CONDITION_FIELD] == "CRITICAL")
            & ~assets_df[ASSET_STATUS_FIELD].isin(
                ["REPLACE", "MAINT", "RETIRED"]
            )
        )
        |
        (
            (assets_df[ASSET_CONDITION_FIELD] == "POOR")
            & (assets_df[ASSET_STATUS_FIELD] == "ACTIVE")
        )
    ]

    # QA Check 8: Parent condition vs latest inspection
    valid_inspections = inspections_df[
        inspections_df[INSPECTION_DATE_FIELD].notna()
    ].copy()

    latest_inspections = (
        valid_inspections
        .sort_values(INSPECTION_DATE_FIELD)
        .groupby(INSPECTION_ASSET_ID_FIELD)
        .tail(1)
    )

    assets_for_compare = assets_df[
        [ASSET_ID_FIELD, ASSET_CONDITION_FIELD]
    ].rename(
        columns={
            ASSET_ID_FIELD: "AssetID",
            ASSET_CONDITION_FIELD: "Condition_Asset",
        }
    )

    latest_for_compare = latest_inspections[
        [
            INSPECTION_ASSET_ID_FIELD,
            INSPECTION_DATE_FIELD,
            INSPECTION_CONDITION_FIELD,
        ]
    ].rename(
        columns={
            INSPECTION_ASSET_ID_FIELD: "AssetID",
            INSPECTION_CONDITION_FIELD: "Condition_LatestInspection",
        }
    )

    comparison = assets_for_compare.merge(
        latest_for_compare,
        on="AssetID",
        how="left",
    )

    condition_mismatches = comparison[
        comparison["Condition_LatestInspection"].notna()
        & (
            comparison["Condition_Asset"]
            != comparison["Condition_LatestInspection"]
        )
    ]

    # Consolidated QA report
    issues = []

    for _, row in assets_without_inspections.iterrows():
        issues.append({
            "IssueType": "No Inspection History",
            "AssetID": row[ASSET_ID_FIELD],
            "Details": "Asset has no related inspection records",
        })

    for _, row in orphan_inspections.iterrows():
        issues.append({
            "IssueType": "Orphan Inspection",
            "AssetID": row[INSPECTION_ASSET_ID_FIELD],
            "Details": (
                f"Inspection {row[INSPECTION_ID_FIELD]} "
                "references an asset that does not exist"
            ),
        })

    for _, row in missing_inspection_dates.iterrows():
        issues.append({
            "IssueType": "Missing Inspection Date",
            "AssetID": row[INSPECTION_ASSET_ID_FIELD],
            "Details": (
                f"Inspection {row[INSPECTION_ID_FIELD]} "
                "has no inspection date"
            ),
        })

    for _, row in overdue_assets.iterrows():
        issues.append({
            "IssueType": "Overdue Inspection",
            "AssetID": row[ASSET_ID_FIELD],
            "Details": (
                f"Next inspection was due "
                f"{row[NEXT_INSPECTION_FIELD]}"
            ),
        })

    for _, row in condition_status_issues.iterrows():
        issues.append({
            "IssueType": "Condition / Status Inconsistency",
            "AssetID": row[ASSET_ID_FIELD],
            "Details": (
                f"Condition={row[ASSET_CONDITION_FIELD]}, "
                f"Status={row[ASSET_STATUS_FIELD]}"
            ),
        })

    for _, row in condition_mismatches.iterrows():
        issues.append({
            "IssueType": "Condition Mismatch",
            "AssetID": row["AssetID"],
            "Details": (
                f"Parent condition={row['Condition_Asset']}; "
                f"latest inspection condition="
                f"{row['Condition_LatestInspection']}"
            ),
        })

    for _, row in duplicate_assets.iterrows():
        issues.append({
            "IssueType": "Duplicate Asset ID",
            "AssetID": row[ASSET_ID_FIELD],
            "Details": "Asset ID occurs more than once",
        })

    for _, row in duplicate_inspections.iterrows():
        issues.append({
            "IssueType": "Duplicate Inspection ID",
            "AssetID": row[INSPECTION_ASSET_ID_FIELD],
            "Details": (
                f"Inspection ID {row[INSPECTION_ID_FIELD]} "
                "occurs more than once"
            ),
        })

    qa_report = pd.DataFrame(
        issues,
        columns=["IssueType", "AssetID", "Details"],
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = (
        OUTPUT_DIR
        / f"GIS_Data_Quality_Report_{timestamp}.csv"
    )

    qa_report.to_csv(output_path, index=False)

    log(
        f"QA/QC completed: "
        f"{len(assets_df)} assets, "
        f"{len(inspections_df)} inspections, "
        f"{len(qa_report)} issues"
    )

    if not qa_report.empty:
        log("Issues by type:")
        for issue_type, count in qa_report["IssueType"].value_counts().items():
            log(f"  {issue_type}: {count}")
    else:
        log("No data quality issues detected.")

    log(f"QA report saved to: {output_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("QA/QC job FAILED")
        with LOG_PATH.open("a", encoding="utf-8") as file:
            file.write(traceback.format_exc())
            file.write("\n")
        raise
