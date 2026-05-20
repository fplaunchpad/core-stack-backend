import ee

from computing.et_downscale.aet import build_aet_stack
from computing.et_downscale.helper import (
    build_classifier,
    get_proj_30m,
    MODIS_COL,
    build_rwdi_image,
    build_common_pixel_mask,
    ee_annual_mean_band,
    finalize_export_image,
    MONTH_ABBR,
    export_product_asset,
    wait_for_tasks,
)
from computing.et_downscale.pet import build_pet_stack


# =============================================================================
# DERIVED APPLICATION 1 - RWDI
# =============================================================================


def run_rwdi(cfg: dict, region: ee.Geometry, aet_stack=None, pet_stack=None) -> str:
    """
    RWDI = (1 - AET/PET) x 100 (%)
    Output  : rwdi_<tehsil>_<year> GEE asset (13 bands)
    """
    tehsil = cfg["tehsil_name"]
    year = cfg["year"]

    print(f"\n{'=' * 60}")
    print(f"  [rwdi]  {tehsil}  |  {year}")
    print(f"{'=' * 60}")

    if aet_stack is None:
        print("  Building AET stack ...")
        classifier = build_classifier(cfg["model_aez"])
        aet_stack = build_aet_stack(region, classifier, year)

    if pet_stack is None:
        print("  Building PET stack (MODIS MOD16A2) ...")
        proj = get_proj_30m(region, year)
        pet_stack = build_pet_stack(region, year, MODIS_COL, proj)

    rwdi_img = build_rwdi_image(aet_stack, pet_stack)
    grid_proj = aet_stack.select("ET_01").projection()
    common_mask = build_common_pixel_mask(region, grid_proj)
    footprint = aet_stack.select("ET_01").mask()
    rwdi_monthly = rwdi_img.updateMask(footprint)
    rwdi_annual = ee_annual_mean_band(
        rwdi_monthly, "RWDI", band_name="RWDI_annual"
    ).updateMask(footprint)
    image = finalize_export_image(
        rwdi_monthly,
        rwdi_annual,
        region,
        metadata={
            "application": "rwdi",
            "units": "percent",
            "formula": "(1 - AET/PET) * 100",
            "modis_collection": MODIS_COL,
            "year": str(year),
            "tehsil": tehsil,
            "roi_path": cfg["roi_path"],
            "description": "Relative Water Deficit Index per month + annual mean",
        },
        band_descriptions=[f"RWDI_{abbr}" for abbr in MONTH_ABBR] + ["RWDI_annual"],
        default_proj=grid_proj,
        common_mask=common_mask,
    )
    task_spec = export_product_asset("rwdi", "RWDI", image, cfg)
    if cfg.get("wait_exports", True):
        wait_for_tasks([task_spec], cfg.get("poll_seconds", 30))
    return task_spec["asset_id"]
