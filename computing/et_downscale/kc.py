import ee

from computing.et_downscale.aet import build_aet_stack
from computing.et_downscale.helper import (
    MONTH_ABBR,
    MODIS_COL,
    build_classifier,
    get_proj_30m,
    build_common_pixel_mask,
    ee_annual_mean_band,
    finalize_export_image,
    export_product_asset,
    wait_for_tasks,
)
from computing.et_downscale.pet import build_pet_stack


def _run_kc_application(
    cfg: dict, region: ee.Geometry, aet_stack=None, pet_stack=None
) -> str:
    """Shared runner for the monthly Kc proxy (AET/PET)."""
    tehsil = cfg["tehsil_name"]
    year = cfg["year"]
    label = "kc"
    title = "Crop Coefficient (Kc)"
    stack_builder = build_kc_image
    band_names = [f"KC_{abbr}" for abbr in MONTH_ABBR] + ["KC_annual"]
    metadata = {
        "application": "kc",
        "units": "ratio (AET/PET)",
        "formula": "AET / PET",
        "modis_collection": MODIS_COL,
        "year": str(year),
        "tehsil": tehsil,
        "roi_path": cfg["roi_path"],
        "description": "Monthly Kc proxy from AET/PET + annual mean",
    }

    print(f"\n{'=' * 60}")
    print(f"  [{label}]  {tehsil}  |  {year}")
    print(f"{'=' * 60}")

    if aet_stack is None:
        print("  Building AET stack ...")
        classifier = build_classifier(cfg["model_aez"])
        aet_stack = build_aet_stack(region, classifier, year)

    if pet_stack is None:
        print("  Building PET stack (MODIS MOD16A2) ...")
        proj = get_proj_30m(region, year)
        pet_stack = build_pet_stack(region, year, MODIS_COL, proj)

    ratio_img = stack_builder(aet_stack, pet_stack)
    grid_proj = aet_stack.select("ET_01").projection()
    common_mask = build_common_pixel_mask(region, grid_proj)
    footprint = aet_stack.select("ET_01").mask()
    ratio_monthly = ratio_img.updateMask(footprint)
    ratio_annual = ee_annual_mean_band(
        ratio_monthly, "KC", band_name="KC_annual"
    ).updateMask(footprint)
    image = finalize_export_image(
        ratio_monthly,
        ratio_annual,
        region,
        metadata=metadata,
        band_descriptions=band_names,
        default_proj=grid_proj,
        common_mask=common_mask,
    )
    task_spec = export_product_asset(label, title, image, cfg)
    if cfg.get("wait_exports", True):
        wait_for_tasks([task_spec], cfg.get("poll_seconds", 30))
    return task_spec["asset_id"]


def build_kc_image(aet_stack: ee.Image, pet_stack: ee.Image) -> ee.Image:
    """KC_01...12 = AET / PET (0-1)"""
    bands = []
    for month in range(1, 13):
        kc = (
            aet_stack.select(f"ET_{month:02d}")
            .divide(pet_stack.select(f"PET_{month:02d}"))
            .rename(f"KC_{month:02d}")
            .float()
        )
        bands.append(kc)
    stack = bands[0]
    for band in bands[1:]:
        stack = stack.addBands(band)
    return stack


# =============================================================================
# DERIVED APPLICATION 2 - KC
# =============================================================================


def run_kc(cfg: dict, region: ee.Geometry, aet_stack=None, pet_stack=None) -> str:
    """
    Kc proxy = AET / PET
    Output  : kc_<tehsil>_<year> GEE asset (13 bands)
    """
    return _run_kc_application(cfg, region, aet_stack=aet_stack, pet_stack=pet_stack)
