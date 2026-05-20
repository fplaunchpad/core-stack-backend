#!/usr/bin/env python3
"""
ET-Applications - Pan-India ET Downscaling CLI  (GEE Asset Export Edition)
===========================================================================
Each output mode now builds its 13-band image fully inside Earth Engine and
exports it directly to a GEE asset. The monthly calculations are performed
entirely server-side in Earth Engine.

  PIXEL CONSISTENCY GUARANTEE
  ----------------------------
  All exported assets produced by the same tehsil + year combination share
  the same bounding box, CRS, and 30 m pixel grid. The AET (Landsat 8)
  pixel grid drives the spatial reference; MODIS-derived bands are
  resampled to this grid before export. Pixels outside the tehsil boundary
  are written as NoData (-9999).

  Band descriptions and valid-pixel counts are stored as Earth Engine image
  properties on the exported assets.

  Core Layers + Derived Applications
  ----------------------------------
  aet           ->  aet_<TEHSIL>_<YEAR>                13 bands  (12 monthly + annual total)
  pet           ->  pet_<TEHSIL>_<YEAR>                13 bands  (12 monthly + annual total)
  gpp           ->  gpp_<TEHSIL>_<YEAR>                13 bands  (12 monthly + annual mean)
  rwdi          ->  rwdi_<TEHSIL>_<YEAR>               13 bands  (12 monthly + annual mean)
  kc            ->  kc_<TEHSIL>_<YEAR>                 13 bands  (12 monthly + annual mean)
  wue           ->  wue_<TEHSIL>_<YEAR>                13 bands  (12 monthly + annual mean)
  all           ->  three feature layers + three derived applications

  GPP Method (Light Use Efficiency)
  ----------------------------------
  GPP = PAR x fAPAR x eps
    PAR    = 0.45 x SWdown_f_tavg (GLDAS W/m2 -> MJ/m2/day)
    fAPAR  = max(0, 1.24 x NDVI - 0.168) from Landsat 8
    eps    = eps_max x TMIN_scalar x VPD_scalar
    eps_max, TMIN/VPD thresholds from MOD17 BPLUT keyed on MCD12Q1 land cover

  WUE Formula
  -----------
  WUE = GPP / AET   (g C m-2 day-1) / (mm day-1) = g C / kg H2O
  where AET is the Landsat + GLDAS RF-downscaled actual ET.

  Quick-start
  -----------
  python3 et_fixed.py --tehsil-asset ... --model-aez ... --asset-root ...
  python3 et_fixed.py --application aet ...
  python3 et_fixed.py --application all ...

  Or call main() programmatically with the same parameters.
"""

import ee

from computing.et_downscale.aet import build_aet_stack, run_aet
from computing.et_downscale.gpp import build_gpp_stack, run_gpp
from computing.et_downscale.helper import (
    MCD12Q1_COL,
    build_classifier,
    get_proj_30m,
    MODIS_COL,
    build_common_pixel_mask,
    ee_annual_total_band,
    finalize_export_image,
    MONTH_ABBR,
    export_product_asset,
    ee_annual_mean_band,
    wait_for_tasks,
    build_rwdi_image,
    build_wue_image,
    load_tehsil,
)
from computing.et_downscale.kc import build_kc_image, run_kc
from computing.et_downscale.pet import build_pet_stack, run_pet
from computing.et_downscale.rwdi import run_rwdi
from computing.et_downscale.wue import run_wue
from utilities.constants import GEE_PATHS, AEZ
from utilities.gee_utils import ee_initialize, get_gee_dir_path, valid_gee_text


def _build_cfg(
    *,
    roi_path: str,
    model_aez: str,
    asset_root: str,
    year: int = 2022,
    tehsil_name: str | None = None,
    application: str = "all",
    overwrite_assets: bool = False,
    wait_exports: bool = True,
    poll_seconds: int = 30,
) -> dict:
    return {
        "roi_path": roi_path,
        "model_aez": model_aez,
        "asset_root": asset_root,
        "year": int(year),
        "tehsil_name": tehsil_name or "",
        "application": application,
        "overwrite_assets": overwrite_assets,
        "wait_exports": wait_exports,
        "poll_seconds": poll_seconds,
    }


# =============================================================================
# ALL  (single combined download - guaranteed pixel consistency)
# =============================================================================


def run_all(cfg: dict, region: ee.Geometry) -> dict:
    """
    Run the three core layers first, wait for them to finish exporting,
    then export the three derived applications.

    Export sequencing guarantee:
      1. Start AET, PET, and GPP export tasks.
      2. Wait until all three core exports reach a terminal state.
      3. Only then start RWDI, KC, and WUE export tasks.

    Pixel consistency guarantee:
      All outputs are derived from the same AET/PET/GPP stacks.
      They are therefore spatially identical - same CRS, transform, and
      pixel grid. No post-processing alignment is needed.
    """
    year = cfg["year"]
    tehsil = cfg["tehsil_name"]

    print(f"\n{'=' * 60}")
    print("  [all] Building shared GEE stacks ...")
    print(f"{'=' * 60}")

    print("\n  [1/3] Building AET stack (Landsat 8 + GLDAS -> RF model) ...")
    classifier = build_classifier(cfg["model_aez"])
    aet_stack = build_aet_stack(region, classifier, year)

    print("\n  [2/3] Building PET stack (MODIS MOD16A2) ...")
    proj = get_proj_30m(region, year)
    pet_stack = build_pet_stack(region, year, MODIS_COL, proj)

    print("\n  [3/3] Building GPP stack (LUE: GLDAS + Landsat NDVI + MCD12Q1) ...")
    gpp_stack = build_gpp_stack(region, year, proj)
    grid_proj = aet_stack.select("ET_01").projection()
    common_mask = build_common_pixel_mask(region, grid_proj)
    footprint = aet_stack.select("ET_01").mask()
    results = {}
    core_task_specs = []
    derived_task_specs = []

    aet_monthly = aet_stack.multiply(0.1)
    aet_annual = ee_annual_total_band(
        aet_monthly, "ET", year, band_name="ET_annual"
    ).updateMask(footprint)
    aet_image = finalize_export_image(
        aet_monthly,
        aet_annual,
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
    spec = export_product_asset("aet", "AET", aet_image, cfg)
    core_task_specs.append(spec)
    results["aet"] = spec["asset_id"]

    pet_monthly = pet_stack.multiply(0.1).updateMask(footprint)
    pet_annual = ee_annual_total_band(
        pet_monthly, "PET", year, band_name="PET_annual"
    ).updateMask(footprint)
    pet_image = finalize_export_image(
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
    spec = export_product_asset("pet", "PET", pet_image, cfg)
    core_task_specs.append(spec)
    results["pet"] = spec["asset_id"]

    gpp_monthly = gpp_stack.updateMask(footprint)
    gpp_annual = ee_annual_mean_band(
        gpp_monthly, "GPP", band_name="GPP_annual"
    ).updateMask(footprint)
    gpp_image = finalize_export_image(
        gpp_monthly,
        gpp_annual,
        region,
        metadata={
            "application": "gpp",
            "units": "g C / m2 / day",
            "method": "LUE: PAR x fAPAR x eps_max x TMIN_scalar x VPD_scalar",
            "par_source": "GLDAS SWdown_f_tavg * 0.0864 * 0.45",
            "fapar_source": "Landsat 8 NDVI -> 1.24*NDVI - 0.168",
            "bplut_source": "MOD17 C6 / MCD12Q1 IGBP LC_Type1",
            "tmin_source": "GLDAS Tair_f_inst monthly minimum (K-273.15)",
            "vpd_source": "GLDAS Tair+Qair+Psurf Magnus formula",
            "year": str(year),
            "tehsil": tehsil,
            "roi_path": cfg["roi_path"],
            "description": "Mean daily GPP per month (LUE) + annual mean",
        },
        band_descriptions=[f"GPP_{abbr}_gC_m2_day" for abbr in MONTH_ABBR]
        + ["GPP_annual_mean"],
        default_proj=grid_proj,
        common_mask=common_mask,
    )
    spec = export_product_asset("gpp", "GPP", gpp_image, cfg)
    core_task_specs.append(spec)
    results["gpp"] = spec["asset_id"]

    print(
        "\n  [phase 1/2] Waiting for core exports (AET, PET, GPP) before derived exports ..."
    )
    wait_for_tasks(core_task_specs, cfg.get("poll_seconds", 30), fail_on_error=True)

    print(
        "\n  [phase 2/2] Starting derived exports (RWDI, KC, WUE) from the shared core stacks ..."
    )

    rwdi_monthly = build_rwdi_image(aet_stack, pet_stack).updateMask(footprint)
    rwdi_annual = ee_annual_mean_band(
        rwdi_monthly, "RWDI", band_name="RWDI_annual"
    ).updateMask(footprint)
    rwdi_image = finalize_export_image(
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
            "description": "RWDI per month + annual mean",
        },
        band_descriptions=[f"RWDI_{abbr}" for abbr in MONTH_ABBR] + ["RWDI_annual"],
        default_proj=grid_proj,
        common_mask=common_mask,
    )
    spec = export_product_asset("rwdi", "RWDI", rwdi_image, cfg)
    derived_task_specs.append(spec)
    results["rwdi"] = spec["asset_id"]

    kc_monthly = build_kc_image(aet_stack, pet_stack).updateMask(footprint)
    kc_annual = ee_annual_mean_band(kc_monthly, "KC", band_name="KC_annual").updateMask(
        footprint
    )
    kc_image = finalize_export_image(
        kc_monthly,
        kc_annual,
        region,
        metadata={
            "application": "kc",
            "units": "ratio (AET/PET)",
            "formula": "AET / PET",
            "modis_collection": MODIS_COL,
            "year": str(year),
            "tehsil": tehsil,
            "roi_path": cfg["roi_path"],
            "description": "Monthly Kc proxy from AET/PET + annual mean",
        },
        band_descriptions=[f"KC_{abbr}" for abbr in MONTH_ABBR] + ["KC_annual"],
        default_proj=grid_proj,
        common_mask=common_mask,
    )
    spec = export_product_asset("kc", "Crop Coefficient (Kc)", kc_image, cfg)
    derived_task_specs.append(spec)
    results["kc"] = spec["asset_id"]

    wue_monthly = build_wue_image(aet_stack, gpp_stack).updateMask(footprint)
    wue_annual = ee_annual_mean_band(
        wue_monthly, "WUE", band_name="WUE_annual"
    ).updateMask(footprint)
    wue_image = finalize_export_image(
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
            "description": "WUE per month + annual mean at 30 m",
        },
        band_descriptions=[f"WUE_{abbr}_gC_per_kgH2O" for abbr in MONTH_ABBR]
        + ["WUE_annual_mean"],
        default_proj=grid_proj,
        common_mask=common_mask,
    )
    spec = export_product_asset("wue", "WUE", wue_image, cfg)
    derived_task_specs.append(spec)
    results["wue"] = spec["asset_id"]

    if cfg.get("wait_exports", True):
        wait_for_tasks(
            derived_task_specs, cfg.get("poll_seconds", 30), fail_on_error=True
        )
    else:
        print(
            "\n[exports] Derived export tasks started. Final completion polling skipped (wait_exports=false)."
        )
    return results


def get_model_aez(roi):

    aez = ee.FeatureCollection(AEZ)
    filtered_aez = aez.filterBounds(roi.geometry()).first().get("ae_regcode").getInfo()
    return f"projects/shuvamdownscalinget/assets/rf_aez{filtered_aez}_final"


def main(
    state: str | None = None,
    district: str | None = None,
    tehsil: str | None = None,
    roi_path: str | None = None,
    year: int = 2022,
    gee_account_id: int = 1,
    application: str = "all",
    overwrite_assets: bool = False,
    wait_exports: bool = True,
    poll_seconds: int = 30,
    app_type: str = "MWS",
):
    ee_initialize(account_id=gee_account_id)
    if state and district and tehsil:
        asset_suffix = (
            valid_gee_text(district.lower()) + "_" + valid_gee_text(tehsil.lower())
        )
        asset_folder_list = [state, district, tehsil]

        roi_path = (
            get_gee_dir_path(
                asset_folder_list, asset_path=GEE_PATHS[app_type]["GEE_ASSET_PATH"]
            )
            + "filtered_mws_"
            + valid_gee_text(district.lower())
            + "_"
            + valid_gee_text(tehsil.lower())
            + "_uid"
        )

    roi = ee.FeatureCollection(roi_path)
    model_aez = get_model_aez(roi)

    asset_root = "projects/corestack-datasets-alpha/assets"

    cfg = _build_cfg(
        roi_path=roi_path,
        model_aez=model_aez,
        asset_root=asset_root,
        year=year,
        tehsil_name=tehsil,
        application=application,
        overwrite_assets=overwrite_assets,
        wait_exports=wait_exports,
        poll_seconds=poll_seconds,
    )

    app = cfg["application"]

    print("\n" + "=" * 68)
    print("  ET-Applications  (GEE Asset Export Edition - v3.0 with GPP/WUE/Kc)")
    print("=" * 68)
    for label, key in [
        ("Tehsil", "tehsil_name"),
        ("Year", "year"),
        ("Output mode", "application"),
        ("Asset root", "asset_root"),
        ("Overwrite assets", "overwrite_assets"),
        ("Model (AEZ)", "model_aez"),
        ("ROI path", "roi_path"),
        ("Wait for exports", "wait_exports"),
        ("Poll interval (s)", "poll_seconds"),
        ("MODIS collection", "modis_collection"),
    ]:
        print(f"  {label:<22}: {cfg.get(key, 'N/A')}")
    print("=" * 68 + "\n")

    region = roi.geometry()

    dispatch = {
        "aet": lambda: run_aet(cfg, region),
        "pet": lambda: run_pet(cfg, region),
        "rwdi": lambda: run_rwdi(cfg, region),
        "kc": lambda: run_kc(cfg, region),
        "gpp": lambda: run_gpp(cfg, region),
        "wue": lambda: run_wue(cfg, region),
        "all": lambda: run_all(cfg, region),
    }

    result = dispatch[app]()
    if app == "all":
        print("\nDone. Export assets:")
        for label, asset_id in result.items():
            print(f"  {label:<5} -> {asset_id}")
    else:
        print(f"\nDone. Export asset: {result}")
    return result
