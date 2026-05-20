import ee

from computing.et_downscale.aet import build_aet_stack
from computing.et_downscale.helper import (
    build_classifier,
    get_proj_30m,
    MODIS_COL,
    build_common_pixel_mask,
    ee_annual_total_band,
    finalize_export_image,
    export_product_asset,
    wait_for_tasks,
    MONTH_ABBR,
    fill_monthly_collection,
    monthly_collection_to_stack,
)


def run_pet(cfg: dict, region: ee.Geometry, aet_stack=None, pet_stack=None) -> str:
    """
    Monthly mean daily PET (MODIS MOD16A2) for every 30 m pixel.
    Output  : pet_<tehsil>_<year> GEE asset (13 bands)
    """
    tehsil = cfg["tehsil_name"]
    year = cfg["year"]

    print(f"\n{'=' * 60}")
    print(f"  [pet]  {tehsil}  |  {year}")
    print(f"{'=' * 60}")

    if aet_stack is None:
        print("  Building AET stack (pixel-grid carrier) ...")
        classifier = build_classifier(cfg["model_aez"])
        aet_stack = build_aet_stack(region, classifier, year)

    if pet_stack is None:
        print("  Building PET stack (MODIS MOD16A2) ...")
        proj = get_proj_30m(region, year)
        pet_stack = build_pet_stack(region, year, MODIS_COL, proj)

    grid_proj = aet_stack.select("ET_01").projection()
    common_mask = build_common_pixel_mask(region, grid_proj)
    footprint = aet_stack.select("ET_01").mask()
    pet_monthly = pet_stack.multiply(0.1).updateMask(footprint)
    pet_annual = ee_annual_total_band(
        pet_monthly, "PET", year, band_name="PET_annual"
    ).updateMask(footprint)
    image = finalize_export_image(
        pet_monthly,
        pet_annual,
        region,
        metadata={
            "application": "pet",
            "units": "bands 1-12: mm/day; band 13: mm/yr",
            "source": "MODIS MOD16A2",
            "modis_collection": MODIS_COL,
            "year": str(year),
            "tehsil": tehsil,
            "roi_path": cfg["roi_path"],
            "description": "Bands 1-12: mean daily PET per month at 30 m; band 13: annual total PET",
        },
        band_descriptions=[f"PET_{abbr}_daily_mm" for abbr in MONTH_ABBR]
        + ["PET_annual_mm"],
        default_proj=grid_proj,
        common_mask=common_mask,
    )
    task_spec = export_product_asset("pet", "PET", image, cfg)
    if cfg.get("wait_exports", True):
        wait_for_tasks([task_spec], cfg.get("poll_seconds", 30))
    return task_spec["asset_id"]


def _make_raw_monthly_pet(month, modis_col, year, proj):
    start = ee.Date.fromYMD(year, month, 1)
    end = start.advance(1, "month")
    mid = start.advance(15, "day").millis()
    pet = (
        modis_col.filterDate(start, end)
        .select("PET")
        .mean()
        .divide(8)
        .resample("bilinear")
        .reproject(crs=proj, scale=30)
        .rename("PET_daily")
        .float()
    )
    return pet.set("month", month).set("system:time_start", mid)


def build_pet_stack(
    region: ee.Geometry, year: int, modis_col_id: str, proj: ee.Projection
) -> ee.Image:
    """
    12-band PET stack PET_01...PET_12 (0.1 mm/day) at 30 m.
    MOD16A2 is 8-day composite; divide by 8 for daily rate.
    500 m MODIS pixel bilinearly resampled to the 30 m Landsat grid.
    Months with no MODIS composites are filled using a +/-60 day window.
    """
    modis_col = ee.ImageCollection(modis_col_id).filterBounds(region)
    months = ee.List.sequence(1, 12)
    raw_monthly = ee.ImageCollection.fromImages(
        months.map(lambda m: _make_raw_monthly_pet(ee.Number(m), modis_col, year, proj))
    )
    interp_col = fill_monthly_collection(raw_monthly, "PET_daily", fallback_value=0)
    stack = monthly_collection_to_stack(interp_col, "PET_daily", "PET_", region)
    return stack
