import ee
import argparse
import calendar
import sys
import time


# ---------------------------------------------------------------------------
# CONSTANTS - AET pipeline
# ---------------------------------------------------------------------------
FEATURE_BANDS = [
    "MSAVI",
    "NDMI",
    "NDVI",
    "NDWI",
    "SAVI",
    "NDBI",
    "NDIIB7",
    "Albedo",
    "LST",
    "Rainf_tavg",
    "RootMoist_inst",
    "SoilMoi0_10cm_inst",
    "CanopInt_inst",
    "AvgSurfT_inst",
    "Qair_f_inst",
    "Wind_f_inst",
    "Psurf_f_inst",
    "SoilTMP0_10cm_inst",
    "Qsb_acc",
    "Swnet_tavg",
    "Lwnet_tavg",
    "Qg_tavg",
    "Qh_tavg",
    "Qle_tavg",
    "SWdown_f_tavg",
    "Tair_f_inst",
]
MONTH_ABBR = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

MODIS_COL = "MODIS/061/MOD16A2GF"
MCD12Q1_COL = "MODIS/061/MCD12Q1"

# NoData sentinel used in GEE images and written to masked pixels in assets.
NODATA = -9999.0
EXPORT_BAND_NAMES = [f"b{i}" for i in range(1, 14)]


def _asset_token(value: str) -> str:
    cleaned = []
    for char in str(value).strip().lower():
        cleaned.append(char if char.isalnum() else "_")
    token = "".join(cleaned).strip("_")
    while "__" in token:
        token = token.replace("__", "_")
    return token or "unknown"


def _build_asset_id(cfg: dict, label: str) -> str:
    root = str(cfg.get("asset_root", "")).rstrip("/")
    if not root:
        raise ValueError("asset_root is required")
    tehsil = _asset_token(cfg.get("tehsil_name", "tehsil"))
    year = int(cfg["year"])
    return f"{root}/{label}_{tehsil}_{year}"


def _asset_exists(asset_id: str) -> bool:
    try:
        ee.data.getAsset(asset_id)
        return True
    except Exception:
        return False


def _prepare_asset_target(asset_id: str, overwrite: bool) -> None:
    if not _asset_exists(asset_id):
        return
    if not overwrite:
        raise RuntimeError(
            f"GEE asset already exists: {asset_id}\n"
            "Pass overwrite_assets=True to replace it."
        )
    print(f"  Overwriting existing asset -> {asset_id}")
    ee.data.deleteAsset(asset_id)


def build_common_pixel_mask(
    region: ee.Geometry, default_proj: ee.Projection
) -> ee.Image:
    """Rasterize the tehsil once on the chosen 30 m grid for all outputs."""
    return (
        ee.Image.constant(1)
        .rename("common_mask")
        .setDefaultProjection(default_proj)
        .clip(region)
        .unmask(0)
        .gt(0)
        .selfMask()
    )


def ee_annual_total_band(
    monthly_stack: ee.Image, prefix: str, year: int, band_name: str = "annual"
) -> ee.Image:
    annual = ee.Image.constant(0).float()
    valid_count = ee.Image.constant(0).float()
    for month in range(1, 13):
        month_band = monthly_stack.select(f"{prefix}_{month:02d}")
        days = calendar.monthrange(year, month)[1]
        annual = annual.add(month_band.unmask(0).multiply(days))
        valid_count = valid_count.add(month_band.mask().gt(0).unmask(0))
    return annual.updateMask(valid_count.gt(0)).rename(band_name).float()


def ee_annual_mean_band(
    monthly_stack: ee.Image, prefix: str, band_name: str = "annual"
) -> ee.Image:
    images = [
        monthly_stack.select(f"{prefix}_{month:02d}").rename("annual_src").float()
        for month in range(1, 13)
    ]
    return ee.ImageCollection.fromImages(images).mean().rename(band_name).float()


def build_wue_image(aet_stack: ee.Image, gpp_stack: ee.Image) -> ee.Image:
    """WUE_01...12 = GPP / AET_mm (g C / kg H2O)."""
    bands = []
    for month in range(1, 13):
        aet_mm = aet_stack.select(f"ET_{month:02d}").multiply(0.1)
        wue = (
            gpp_stack.select(f"GPP_{month:02d}").divide(aet_mm).updateMask(aet_mm.gt(0))
        )
        wue = wue.updateMask(wue.gte(0)).updateMask(wue.lte(50))
        bands.append(wue.rename(f"WUE_{month:02d}").float())
    stack = bands[0]
    for band in bands[1:]:
        stack = stack.addBands(band)
    return stack


def _apply_image_properties(img: ee.Image, props: dict) -> ee.Image:
    out = img
    for key, value in props.items():
        out = out.set(key, value)
    return out


def finalize_export_image(
    monthly_stack: ee.Image,
    annual_band: ee.Image,
    region: ee.Geometry,
    metadata: dict,
    band_descriptions: list,
    default_proj: ee.Projection = None,
    common_mask: ee.Image = None,
) -> ee.Image:
    image = monthly_stack.addBands(annual_band).rename(EXPORT_BAND_NAMES)
    if default_proj is not None:
        image = image.setDefaultProjection(default_proj)
    if common_mask is not None:
        image = image.updateMask(common_mask)
    image = image.clip(region)
    image = image.unmask(NODATA).float()
    props = {"nodata": NODATA}
    props.update(metadata)
    for idx, desc in enumerate(band_descriptions, start=1):
        props[f"band_{idx}_description"] = desc
    return _apply_image_properties(image, props)


def _start_asset_export(image: ee.Image, asset_id: str, description: str):
    export_kwargs = {
        "image": image,
        "description": description,
        "assetId": asset_id,
        "scale": 30,
        "maxPixels": 1e13,
    }
    task = ee.batch.Export.image.toAsset(**export_kwargs)
    task.start()
    print(f"  Export task started -> {asset_id}")
    return task


def wait_for_tasks(
    task_specs: list, poll_seconds: int = 30, fail_on_error: bool = False
) -> dict:
    if not task_specs:
        return {}
    poll_seconds = max(5, int(poll_seconds))
    pending = {spec["asset_id"]: spec for spec in task_specs}
    final_statuses = {}
    print(f"\n[exports] Waiting for {len(task_specs)} Earth Engine task(s) ...")
    while pending:
        finished_now = []
        for asset_id, spec in pending.items():
            status = spec["task"].status()
            state = status.get("state", "UNKNOWN")
            if state in {"COMPLETED", "FAILED", "CANCELLED", "CANCEL_REQUESTED"}:
                finished_now.append(asset_id)
                final_statuses[asset_id] = status
                print(f"  [{state}] {spec['label']} -> {asset_id}")
                if status.get("error_message"):
                    print(f"    Error: {status['error_message']}")
        for asset_id in finished_now:
            pending.pop(asset_id, None)
        if pending:
            print(
                f"  Still running: {len(pending)} task(s). Checking again in {poll_seconds}s ..."
            )
            time.sleep(poll_seconds)
    if fail_on_error:
        failed = []
        for spec in task_specs:
            asset_id = spec["asset_id"]
            status = final_statuses.get(asset_id, {})
            state = status.get("state", "UNKNOWN")
            if state != "COMPLETED":
                message = status.get(
                    "error_message", "No error message from Earth Engine."
                )
                failed.append(f"{spec['label']} ({asset_id}) -> {state}: {message}")
        if failed:
            raise RuntimeError(
                "One or more Earth Engine export tasks did not complete successfully:\n"
                + "\n".join(failed)
            )
    return final_statuses


def export_product_asset(
    label: str, display_name: str, image: ee.Image, cfg: dict
) -> dict:
    asset_id = _build_asset_id(cfg, label)
    _prepare_asset_target(asset_id, bool(cfg.get("overwrite_assets", False)))
    print(f"  {display_name} asset -> {asset_id}")
    task = _start_asset_export(
        image,
        asset_id,
        description=f"export_{label}_{_asset_token(cfg['tehsil_name'])}_{cfg['year']}",
    )
    return {"asset_id": asset_id, "task": task, "label": label}


def load_tehsil(asset: str):
    fc = ee.FeatureCollection(asset)
    region = fc.geometry()
    return fc, region


def build_classifier(model_path: str) -> ee.Classifier:
    trees = (
        ee.FeatureCollection(model_path)
        .aggregate_array("tree")
        .map(lambda s: ee.String(s).replace("#.*", "", "g").trim())
    )
    return ee.Classifier.decisionTreeEnsemble(trees)


def fill_monthly_collection(
    raw_monthly: ee.ImageCollection, value_band: str, fallback_value=None
) -> ee.ImageCollection:
    """Fill monthly gaps from neighbouring months within a +/-60 day window."""

    def interpolate(img):
        time_start = img.get("system:time_start")
        neighbours = raw_monthly.select(value_band).filterDate(
            ee.Date(time_start).advance(-60, "day"),
            ee.Date(time_start).advance(60, "day"),
        )
        filled = neighbours.mean()
        out = img.select(value_band).unmask(filled)
        if fallback_value is not None:
            out = out.unmask(fallback_value)
        return (
            out.rename(value_band)
            .float()
            .set("month", img.get("month"))
            .set("system:time_start", time_start)
        )

    return raw_monthly.map(interpolate)


def monthly_collection_to_stack(
    monthly_col: ee.ImageCollection,
    value_band: str,
    output_prefix: str,
    region: ee.Geometry,
) -> ee.Image:
    """Convert a monthly image collection into a named 12-band stack."""

    def rename_month(img):
        month_str = ee.String(ee.Number(img.get("month")).format("%02d"))
        return (
            img.select(value_band)
            .rename(ee.String(output_prefix).cat(month_str))
            .float()
        )

    named = monthly_col.map(rename_month)
    stack = named.toBands().clip(region)
    current_names = stack.bandNames()
    new_names = current_names.map(lambda n: ee.String(n).split("_").slice(1).join("_"))
    return stack.rename(new_names)


def make_raw_monthly_ndvi(month, ls_col, year):
    start = ee.Date.fromYMD(year, month, 1)
    end = start.advance(1, "month")
    mid = start.advance(15, "day").millis()
    monthly_collection = ls_col.filterDate(start, end)
    ndvi = (
        monthly_collection.map(
            lambda img: img.normalizedDifference(["SR_B5", "SR_B4"]).rename("NDVI")
        )
        .mean()
        .rename("NDVI")
    )
    return ndvi.set("month", month).set("system:time_start", mid)


def calc_landsat_indices(img: ee.Image) -> ee.Image:
    ndvi = img.normalizedDifference(["SR_B5", "SR_B4"]).rename("NDVI")
    savi = img.expression(
        "((NIR-R)/(NIR+R+0.5))*1.5",
        {"NIR": img.select("SR_B5"), "R": img.select("SR_B4")},
    ).rename("SAVI")
    msavi = img.expression(
        "(2*NIR+1-sqrt(pow((2*NIR+1),2)-8*(NIR-R)))/2",
        {"NIR": img.select("SR_B5"), "R": img.select("SR_B4")},
    ).rename("MSAVI")
    ndbi = img.normalizedDifference(["SR_B6", "SR_B5"]).rename("NDBI")
    ndwi = img.normalizedDifference(["SR_B3", "SR_B5"]).rename("NDWI")
    ndmi = img.normalizedDifference(["SR_B5", "SR_B6"]).rename("NDMI")
    ndiib7 = img.normalizedDifference(["SR_B5", "SR_B7"]).rename("NDIIB7")
    albedo = img.expression(
        "((0.356*B1)+(0.130*B2)+(0.373*B3)+(0.085*B4)+(0.072*B5)-0.018)/1.016",
        {
            "B1": img.select("SR_B1"),
            "B2": img.select("SR_B2"),
            "B3": img.select("SR_B3"),
            "B4": img.select("SR_B4"),
            "B5": img.select("SR_B5"),
        },
    ).rename("Albedo")
    lst = img.select("ST_B10").multiply(0.00341802).add(149.0).rename("LST")
    return img.addBands([ndvi, savi, msavi, ndbi, ndwi, ndmi, ndiib7, albedo, lst])


def predict_daily_et(
    ls_img: ee.Image, region: ee.Geometry, classifier: ee.Classifier
) -> ee.Image:
    idx = calc_landsat_indices(ls_img)
    clim = (
        ee.ImageCollection("NASA/GLDAS/V021/NOAH/G025/T3H")
        .filterBounds(region)
        .filterDate(
            ls_img.date().advance(-12, "hour"), ls_img.date().advance(12, "hour")
        )
        .mean()
        .resample("bilinear")
        .reproject(crs=ls_img.select("SR_B5").projection(), scale=30)
    )
    return (
        idx.addBands(clim)
        .select(FEATURE_BANDS)
        .classify(classifier)
        .rename("ET_daily")
        .set("system:time_start", ls_img.date().millis())
    )


def make_raw_monthly(month, ls_col, region, classifier, year):
    start = ee.Date.fromYMD(year, month, 1)
    end = start.advance(1, "month")
    mid = start.advance(15, "day").millis()
    monthly_collection = ls_col.filterDate(start, end)
    et = (
        monthly_collection.map(lambda img: predict_daily_et(img, region, classifier))
        .mean()
        .rename("ET_daily")
    )
    return et.set("month", month).set("system:time_start", mid)


# =============================================================================
# IMAGE BUILDERS  (AET / PET / RWDI / WS / KC / combined)
# =============================================================================


def get_proj_30m(region: ee.Geometry, year: int) -> ee.Projection:
    """Return the 30 m Landsat projection for this region/year."""
    ls_ref = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(region)
        .filterDate(ee.Date.fromYMD(year, 1, 1), ee.Date.fromYMD(year, 12, 31))
        .first()
    )
    return ls_ref.select("SR_B5").projection()


def build_rwdi_image(aet_stack: ee.Image, pet_stack: ee.Image) -> ee.Image:
    """RWDI_01...12 = (1 - AET/PET) x 100 (%)"""
    bands = []
    for month in range(1, 13):
        rwdi = (
            ee.Image(1)
            .subtract(
                aet_stack.select(f"ET_{month:02d}").divide(
                    pet_stack.select(f"PET_{month:02d}")
                )
            )
            .multiply(100)
            .rename(f"RWDI_{month:02d}")
            .float()
        )
        bands.append(rwdi)
    stack = bands[0]
    for band in bands[1:]:
        stack = stack.addBands(band)
    return stack
