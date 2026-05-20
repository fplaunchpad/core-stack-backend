import ee

from computing.et_downscale.aet import build_aet_stack
from computing.et_downscale.gpp import build_gpp_stack
from computing.et_downscale.helper import (
    build_classifier,
    get_proj_30m,
    build_common_pixel_mask,
    build_wue_image,
    ee_annual_mean_band,
    finalize_export_image,
    MONTH_ABBR,
    export_product_asset,
    wait_for_tasks,
)


# =============================================================================
# DERIVED APPLICATION 3 - WUE
# =============================================================================


def run_wue(cfg: dict, region: ee.Geometry, aet_stack=None, gpp_stack=None) -> str:
    """
    Water Use Efficiency = GPP / AET (g C / kg H2O)
    Output  : wue_<tehsil>_<year> GEE asset (13 bands)
    """
    tehsil = cfg["tehsil_name"]
    year = cfg["year"]

    print(f"\n{'=' * 60}")
    print(f"  [wue]  {tehsil}  |  {year}  |  WUE = GPP / AET")
    print(f"{'=' * 60}")

    if aet_stack is None:
        print("  Building AET stack (Landsat 8 + GLDAS -> RF model) ...")
        classifier = build_classifier(cfg["model_aez"])
        aet_stack = build_aet_stack(region, classifier, year)

    if gpp_stack is None:
        print("  Building GPP stack (LUE: GLDAS + Landsat NDVI + MCD12Q1) ...")
        proj = get_proj_30m(region, year)
        gpp_stack = build_gpp_stack(region, year, proj)

    grid_proj = aet_stack.select("ET_01").projection()
    common_mask = build_common_pixel_mask(region, grid_proj)
    footprint = aet_stack.select("ET_01").mask()
    wue_monthly = build_wue_image(aet_stack, gpp_stack).updateMask(footprint)
    wue_annual = ee_annual_mean_band(
        wue_monthly, "WUE", band_name="WUE_annual"
    ).updateMask(footprint)
    image = finalize_export_image(
        wue_monthly,
        wue_annual,
        region,
        metadata={
            "application": "wue",
            "units": "g C / kg H2O",
            "formula": "GPP (LUE) / AET (RF downscaled)",
            "gpp_method": "PAR x fAPAR x eps_max x TMIN_scalar x VPD_scalar",
            "aet_method": "Landsat8 + GLDAS features -> Random Forest",
            "bplut_source": "MOD17 C6 BPLUT / MCD12Q1 IGBP LC_Type1",
            "year": str(year),
            "tehsil": tehsil,
            "roi_path": cfg["roi_path"],
            "description": (
                "WUE = GPP/AET per month + annual mean at 30 m. "
                "Units: g C fixed per kg of water transpired."
            ),
        },
        band_descriptions=[f"WUE_{abbr}_gC_per_kgH2O" for abbr in MONTH_ABBR]
        + ["WUE_annual_mean"],
        default_proj=grid_proj,
        common_mask=common_mask,
    )
    task_spec = export_product_asset("wue", "WUE", image, cfg)
    if cfg.get("wait_exports", True):
        wait_for_tasks([task_spec], cfg.get("poll_seconds", 30))
    return task_spec["asset_id"]
