import ee
from computing.et_downscale.helper import (
    build_classifier,
    build_common_pixel_mask,
    ee_annual_total_band,
    finalize_export_image,
    MONTH_ABBR,
    export_product_asset,
    wait_for_tasks,
    fill_monthly_collection,
    monthly_collection_to_stack,
    make_raw_monthly,
)


# =============================================================================
# CORE LAYER 1 - AET
# =============================================================================


def run_aet(cfg: dict, region: ee.Geometry, aet_stack=None) -> str:
    """
    Monthly mean daily AET for every 30 m pixel.
    Output  : aet_<tehsil>_<year> GEE asset (13 bands)
    """
    tehsil = cfg["tehsil_name"]
    year = cfg["year"]

    print(f"\n{'=' * 60}")
    print(f"  [aet]  {tehsil}  |  {year}")
    print(f"{'=' * 60}")

    if aet_stack is None:
        print("  Building AET stack ...")
        classifier = build_classifier(cfg["model_aez"])
        aet_stack = build_aet_stack(region, classifier, year)

    grid_proj = aet_stack.select("ET_01").projection()
    common_mask = build_common_pixel_mask(region, grid_proj)
    aet_monthly = aet_stack.multiply(0.1)
    footprint = aet_monthly.select("ET_01").mask()
    aet_annual_total = ee_annual_total_band(
        aet_monthly, "ET", year, band_name="ET_annual"
    ).updateMask(footprint)
    image = finalize_export_image(
        aet_monthly,
        aet_annual_total,
        region,
        metadata={
            "application": "aet",
            "units": "bands 1-12: mm/day; band 13: mm/yr",
            "year": str(year),
            "tehsil": tehsil,
            "roi_path": cfg["roi_path"],
            "model_aez": cfg["model_aez"],
            "description": "Bands 1-12: mean daily AET per month at 30 m; band 13: annual total AET",
        },
        band_descriptions=[f"ET_{abbr}_daily_mm" for abbr in MONTH_ABBR]
        + ["ET_annual_mm"],
        default_proj=grid_proj,
        common_mask=common_mask,
    )
    task_spec = export_product_asset("aet", "AET", image, cfg)
    if cfg.get("wait_exports", True):
        wait_for_tasks([task_spec], cfg.get("poll_seconds", 30))
    return task_spec["asset_id"]


def build_aet_stack(
    region: ee.Geometry, classifier: ee.Classifier, year: int
) -> ee.Image:
    """12-band ET_01...ET_12 stack (0.1 mm/day, mean daily)."""
    ls_col = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(region)
        .filterDate(ee.Date.fromYMD(year, 1, 1), ee.Date.fromYMD(year, 12, 31))
    )

    months = ee.List.sequence(1, 12)
    raw_monthly = ee.ImageCollection.fromImages(
        months.map(
            lambda m: make_raw_monthly(ee.Number(m), ls_col, region, classifier, year)
        )
    )
    interp_col = fill_monthly_collection(raw_monthly, "ET_daily", fallback_value=0)
    stack = monthly_collection_to_stack(interp_col, "ET_daily", "ET_", region)
    return stack
