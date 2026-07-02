"""
================================================================================
 change_detection_EXPLAINED.py
================================================================================
 This is an ANNOTATED CLONE of `change_detection.py`. The original file is NOT
 modified. Every block below is explained so you can understand the whole flow.

 BIG PICTURE
 -----------
 This module runs on Google Earth Engine (GEE) through a Celery background task.
 Given a location (state / district / block — "block" = tehsil/sub-district) and
 a year range, it compares the LAND USE / LAND COVER (LULC) of an EARLIER period
 ("then") against a LATER period ("now") and produces 5 RASTER layers showing
 how the land changed:

     1. Urbanization  -> things turning INTO built-up area
     2. Degradation   -> forest losing quality / turning into worse classes
     3. Deforestation -> forest -> non-forest
     4. Afforestation -> non-forest -> forest
     5. CropIntensity -> change in cropping (single/double/triple crop)

 A "raster" here is an image where each pixel holds a small integer CODE that
 says which transition happened at that pixel (e.g. 2 = "water became built-up").

 The companion file `change_detection_vector.py` later converts these rasters
 into VECTOR polygons + area numbers per micro-watershed.

 PIPELINE PER PARAM:
   build "then"/"now" mode-composite images  ->  compute transition masks  ->
   add masks into one coded image  ->  export raster asset to GEE  ->
   save layer metadata to DB  ->  push to GCS then GeoServer for serving.
================================================================================
"""

import ee                       # The Earth Engine Python client library (server-side geospatial compute).
import copy                     # Used for copy.deepcopy() so we can edit a list of images without touching the originals.
from utilities.gee_utils import (   # Project helper functions that wrap common GEE / infra operations:
    ee_initialize,              #   - authenticates & initializes the EE session for a given service account.
    check_task_status,          #   - blocks/polls until a list of EE export tasks finish.
    valid_gee_text,             #   - sanitizes strings (lowercase, replace spaces, etc.) so they are valid GEE asset names.
    get_gee_asset_path,         #   - builds the base GEE asset folder path for this state/district/block.
    sync_raster_to_gcs,         #   - exports an EE raster image to Google Cloud Storage (GCS).
    sync_raster_gcs_to_geoserver,  # - publishes a raster sitting in GCS into GeoServer (the map server the frontend uses).
    export_raster_asset_to_gee, #   - kicks off an EE export-to-asset task (saves the computed image back into GEE).
    is_gee_asset_exists,        #   - returns True if a GEE asset already exists (so we skip recomputing). 
    make_asset_public,          #   - sets the asset's sharing to public so it can be read by services.
)
from nrm_app.celery import app  # The Celery app instance; @app.task turns a function into an async background job.
from computing.utils import save_layer_info_to_db, update_layer_sync_status  # DB helpers to record layer metadata & sync flags.


@app.task(bind=True)            # Registers this function as a Celery task. bind=True => first arg `self` is the task instance.
def get_change_detection(
    self, state, district, block, start_year, end_year, gee_account_id
):
    """
    Top-level ENTRY POINT (the Celery task).
    Generates change-detection RASTERS for all 5 params for one tehsil/block.

    Parameters:
      state, district, block : location identifiers (block = tehsil).
      start_year, end_year   : the year window to compare (e.g. 2017 .. 2022).
      gee_account_id         : which GEE service account to authenticate with.
    """
    # Initialize the Earth Engine
    ee_initialize(gee_account_id)   # Authenticate this worker with GEE before any ee.* call works.

    # Maps each change-type NAME to the FUNCTION that computes that change image.
    # This lets us loop over them generically below instead of writing 5 copies.
    param_dict = {
        "Urbanization": built_up,
        "Degradation": change_degradation,
        "Deforestation": change_deforestation,
        "Afforestation": change_afforestation,
        "CropIntensity": change_cropping_intensity,
    }

    # Common filename PREFIX used for every output asset, e.g. "change_pune_haveli".
    description = (
        f"change_{valid_gee_text(district.lower())}_{valid_gee_text(block.lower())}"
    )

    # ------------------------------------------------------------------
    # STEP 1: Load the yearly LULC input images (one per agricultural year).
    # ------------------------------------------------------------------
    l1_asset = []               # Will hold one ee.Image per year in [start_year, end_year].
    s_year = start_year         # Loop cursor starting at the first year.
    while s_year <= end_year:   # Inclusive loop over every year in the window.
        l1_asset.append(
            ee.Image(           # Reference an existing yearly LULC asset already produced upstream.
                get_gee_asset_path(state, district, block)   # base folder for this location
                + valid_gee_text(district.lower())
                + "_"
                + valid_gee_text(block.lower())
                + "_"
                + str(s_year)
                + "-07-01_"     # agricultural year runs July 1 (s_year) ...
                + str(s_year + 1)
                + "-06-30_LULCmap_10m"   # ... to June 30 (s_year+1); 10m resolution LULC map.
            )
        )
        s_year += 1             # Move to next year.
    # Result: l1_asset is a time-ordered list of yearly LULC images.

    # ------------------------------------------------------------------
    # STEP 2: Load the Region Of Interest (ROI) = the micro-watershed boundaries.
    # ------------------------------------------------------------------
    # Filter for the region of interest
    roi_boundary = ee.FeatureCollection(    # A FeatureCollection = set of polygons (the MWS units) with a "uid".
        get_gee_asset_path(state, district, block)
        + "filtered_mws_"
        + valid_gee_text(district.lower())
        + "_"
        + valid_gee_text(block.lower())
        + "_uid"
    )

    task_list = []              # Will collect EE export task IDs so we can wait on them.

    # ------------------------------------------------------------------
    # STEP 3: For each change type, compute its image and export it (if missing).
    # ------------------------------------------------------------------
    for change_detection_key, change_detection_values in param_dict.items():
        # change_detection_key   = e.g. "Urbanization"
        # change_detection_values = the corresponding function, e.g. built_up
        ch_description = f"{description}_{change_detection_key}_{start_year}_{end_year}"   # full asset name for THIS param
        asset_id = get_gee_asset_path(state, district, block) + ch_description             # full asset path

        if not is_gee_asset_exists(asset_id):   # Skip if it was already computed in a previous run (idempotent).
            print(f"{asset_id} doesn't exist")

            # eval() runs the mapped function: built_up(roi_boundary, l1_asset), etc.
            # (A plain `change_detection_values(roi_boundary, l1_asset)` would do the same;
            #  eval here is just how the original author called it.)
            result = eval("change_detection_values(roi_boundary, l1_asset)")

            task_id = export_raster_asset_to_gee(   # Start an async export of the computed image into GEE.
                image=result,
                description=ch_description,
                asset_id=asset_id,
                scale=10,                       # 10 meters per pixel (match the LULC resolution).
                region=roi_boundary.geometry(), # only export within the ROI bounds.
            )
            task_list.append(task_id)           # Remember this task so we can wait for it.

    task_id_list = check_task_status(task_list)  # BLOCK until all export tasks complete (success/fail).
    print("Change detection task_id_list", task_id_list)

    # ------------------------------------------------------------------
    # STEP 4: Record each finished raster in the DB and make it public.
    # ------------------------------------------------------------------
    layer_ids = {}              # Maps param name -> DB layer id (used later for sync status updates).
    for param in param_dict.keys():
        ch_description = f"{description}_{param}_{start_year}_{end_year}"
        asset_id = get_gee_asset_path(state, district, block) + ch_description
        if is_gee_asset_exists(asset_id):   # Only register layers that actually exist now.
            layer_id = save_layer_info_to_db(   # Insert/update a row describing this layer in the app DB.
                state,
                district,
                block,
                layer_name=f"change_{valid_gee_text(district.lower())}_{valid_gee_text(block.lower())}_{param}",
                asset_id=asset_id,
                dataset_name="Change Detection Raster",
                misc={                          # Extra metadata stored alongside the layer.
                    "start_year": start_year,
                    "end_year": end_year,
                },
            )
            layer_ids[param] = layer_id         # Save the DB id for the sync step.
            make_asset_public(asset_id)         # Allow GeoServer / public services to read the asset.

    # ------------------------------------------------------------------
    # STEP 5: Publish all rasters to GCS -> GeoServer for the frontend to display.
    # ------------------------------------------------------------------
    layer_at_geoserver = sync_to_gcs_geoserver(
        state,
        district,
        block,
        description,
        param_dict.keys(),
        layer_ids,
        start_year,
        end_year,
    )
    return layer_at_geoserver   # True only if ALL params were successfully synced to GeoServer.


# ==========================================================================
# THE 5 CHANGE-COMPUTATION FUNCTIONS
# ==========================================================================
# Shared idea in all of them:
#   1. REMAP the raw LULC class codes into a smaller set of "semantic" codes.
#   2. Build a "then" image = MODE (most frequent class) of the first 3 years.
#      Build a "now"  image = MODE of the remaining years.
#   3. For each transition of interest, make a boolean mask (then==X AND now==Y),
#      multiply by a unique integer so each transition gets its own code.
#   4. Add all masks onto a zero image -> a single coded "change" raster.
#
# NOTE on raw LULC codes used in remaps (the [1,2,3,4,6,7,8,9,10,11,12] lists):
#   these are the original LULC class ids; the second list is what each becomes.
# ==========================================================================


def built_up(roi_boundary, l1_asset):
    """URBANIZATION: detect pixels that BECAME built-up (class 1) by 'now'."""
    print("built_up function is runing")

    lulc_projection = l1_asset[0].projection()  # Keep the original map projection/CRS for consistency.

    # Remap raw LULC codes -> simplified codes for this analysis:
    #   1 -> 1 (built-up), 2/3/4 -> 2 (water-ish group), 6/8/9/10 -> 3 (tree/green),
    #   7/11 -> ... , 12 -> 4 (barren). 0 = default for anything unlisted.
    def remap_values(image):
        return image.remap(
            [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12],   # FROM these raw classes
            [1, 2, 2, 2, 3, 4, 3, 3, 3, 3, 4],       # TO these simplified classes
            0,                                        # default value if not in the FROM list
            "predicted_label",                       # band name to remap on
        ).setDefaultProjection(lulc_projection)

    l1_asset_remapped = [remap_values(asset) for asset in l1_asset]  # Remap every yearly image.

    # "then" = typical land cover in the EARLY period (mode of first 3 years).
    then = ee.ImageCollection(l1_asset_remapped[:3]).mode().reproject(lulc_projection)
    # "now"  = typical land cover in the LATER period (mode of the rest).
    now = ee.ImageCollection(l1_asset_remapped[3:]).mode().reproject(lulc_projection)

    then = then.clip(roi_boundary.geometry())   # Restrict to the ROI polygons only.
    now = now.clip(roi_boundary.geometry())

    # Transition masks -> "became built-up (1)" from various earlier classes.
    trans_bu_bu = then.eq(1).And(now.eq(1))                  # was built-up, still built-up        -> code 1
    trans_w_bu = then.eq(2).And(now.eq(1)).multiply(2)      # water group  -> built-up            -> code 2
    trans_tr_bu = then.eq(3).And(now.eq(1)).multiply(3)     # tree/green   -> built-up            -> code 3
    trans_b_bu = then.eq(4).And(now.eq(1)).multiply(4)      # barren       -> built-up            -> code 4

    # Start from an all-zero image, then stack the coded transitions onto it.
    change_bu = (
        ee.Image.constant(0)
        .setDefaultProjection(lulc_projection)
        .clip(roi_boundary.geometry())
    )
    change_bu = (
        change_bu.add(trans_bu_bu).add(trans_w_bu).add(trans_tr_bu).add(trans_b_bu)
    )
    return change_bu            # Each pixel now holds 0 (no change) or 1..4 (which transition).


def change_degradation(roi_boundary, l1_asset):
    """DEGRADATION: forest (here remapped to code 3) turning into worse classes."""
    lulc_projection = l1_asset[0].projection()

    # Different remap target list than built_up — tuned for degradation semantics.
    def remap_values(image):
        return image.remap(
            [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12],
            [1, 2, 2, 2, 4, 5, 3, 3, 3, 3, 6],   # note: forest-ish -> 3, plus extra classes 4,5,6
            0,
            "predicted_label",
        ).setDefaultProjection(lulc_projection)

    l1_asset_remapped = [remap_values(asset) for asset in l1_asset]

    then = ee.ImageCollection(l1_asset_remapped[:3]).mode().reproject(lulc_projection)
    now = ee.ImageCollection(l1_asset_remapped[3:]).mode().reproject(lulc_projection)

    then = then.clip(roi_boundary.geometry())
    now = now.clip(roi_boundary.geometry())

    trans_f_f = then.eq(3).And(now.eq(3))                   # forest stayed forest                -> code 1
    trans_f_bu = then.eq(3).And(now.eq(1)).multiply(2)     # forest -> built-up                  -> code 2
    trans_f_ba = then.eq(3).And(now.eq(5)).multiply(3)     # forest -> barren (5)                -> code 3
    trans_f_sc = then.eq(3).And(now.eq(6)).multiply(4)     # forest -> scrub (6)                 -> code 4

    change_deg = (
        ee.Image.constant(0)
        .setDefaultProjection(lulc_projection)
        .clip(roi_boundary.geometry())
    )
    change_deg = (
        change_deg.add(trans_f_f).add(trans_f_bu).add(trans_f_ba).add(trans_f_sc)
    )
    return change_deg


def change_deforestation_afforestation(roi_boundary, l1_asset, lulc_projection):
    """
    SHARED HELPER for both deforestation and afforestation.

    Forest detection is noisy year-to-year, so this function first does TEMPORAL
    SMOOTHING / NOISE-CLEANUP on the yearly series before computing then/now.
    It returns (now, then) which the two callers then turn into directional
    transitions (forest->X for deforestation, X->forest for afforestation).
    """
    print("change_deforestation is running")

    # A zero image used to ACCUMULATE evidence flags across the time series.
    zero_image2 = (
        ee.Image.constant(0)
        .setDefaultProjection(lulc_projection)
        .clip(l1_asset[0].geometry())
    )

    # -------- PASS 1: scan every 3-year window (before, middle, after) ----------
    # Each cond* tests a specific "this looks like noise / a real flip" pattern
    # using RAW class codes (6,8,9,10,11 = tree-ish variants; 12 = forest; etc).
    for i in range(1, len(l1_asset) - 1):
        before = l1_asset[i - 1]   # previous year
        middle = l1_asset[i]       # current year
        after = l1_asset[i + 1]    # next year

        # cond1: forest (12) on both sides but a tree-variant in the middle -> likely still forest.
        cond1 = (
            before.eq(12)
            .And(after.eq(12))
            .And(
                middle.eq(6)
                .Or(middle.eq(8))
                .Or(middle.eq(9))
                .Or(middle.eq(10))
                .Or(middle.eq(11))
            )
        )
        # cond2: tree group (2,3,4) before & after with a tree-variant middle.
        cond2 = (
            before.eq(2)
            .Or(before.eq(3))
            .Or(before.eq(4))
            .And(after.eq(2).Or(after.eq(3)).Or(after.eq(4)))
            .And(
                middle.eq(6)
                .Or(middle.eq(8))
                .Or(middle.eq(9))
                .Or(middle.eq(10))
                .Or(middle.eq(11))
            )
        )
        # cond3..cond11: more before/middle/after pattern tests (each catches a
        # particular temporary flip that should be treated as stable forest/non-forest).
        cond3 = before.eq(6).And(after.eq(6)).And(middle.eq(12))
        cond4 = (
            before.eq(8)
            .Or(before.eq(9))
            .Or(before.eq(10))
            .Or(before.eq(11))
            .And(after.eq(8).Or(after.eq(9)).Or(after.eq(10)).Or(after.eq(11)))
            .And(middle.eq(12))
        )
        cond5 = (
            before.eq(8)
            .Or(before.eq(9))
            .Or(before.eq(10))
            .Or(before.eq(11))
            .And(after.eq(8).Or(after.eq(9)).Or(after.eq(10)).Or(after.eq(11)))
            .And(middle.eq(7))
        )
        cond6 = (
            before.eq(6)
            .And(after.eq(6))
            .And(middle.eq(8).Or(middle.eq(9)).Or(middle.eq(10)).Or(middle.eq(11)))
        )
        cond7 = (
            before.eq(8)
            .Or(before.eq(9))
            .Or(before.eq(10))
            .Or(before.eq(11))
            .And(after.eq(8).Or(after.eq(9)).Or(after.eq(10)).Or(after.eq(11)))
            .And(middle.eq(6))
        )
        cond8 = before.eq(1).And(after.eq(1)).And(middle.eq(6))
        cond9 = before.eq(6).And(after.eq(6)).And(middle.eq(1))
        cond10 = (
            before.eq(1)
            .And(after.eq(1))
            .And(middle.eq(8).Or(middle.eq(9)).Or(middle.eq(10)).Or(middle.eq(11)))
        )
        cond11 = (
            before.eq(7)
            .And(after.eq(7))
            .And(
                middle.eq(6)
                .Or(middle.eq(8))
                .Or(middle.eq(9))
                .Or(middle.eq(10))
                .Or(middle.eq(11))
            )
        )

        # Sum all the cond flags into the accumulator (builds a per-pixel score image).
        zero_image2 = (
            zero_image2.add(cond1)
            .add(cond2)
            .add(cond3)
            .add(cond4)
            .add(cond5)
            .add(cond6)
            .add(cond7)
            .add(cond8)
            .add(cond9)
            .add(cond10)
            .add(cond11)
        )

    # -------- PASS 2: rewrite noisy "middle" years using the accumulated info --------
    l1_asset_copy = copy.deepcopy(l1_asset)   # Work on a COPY so original images stay intact.
    for i in range(1, len(l1_asset) - 1):
        before = l1_asset[i - 1]
        middle = l1_asset[i]
        after = l1_asset[i + 1]

        # cond1: forest both sides, non-forest dip in middle, and accumulator says 3/4 -> fill middle as forest (3).
        cond1 = (
            before.eq(3)
            .And(middle.neq(3))
            .And(after.eq(3))
            .And((zero_image2.eq(3).Or(zero_image2.eq(4))))
        )
        # cond2: lone forest spike in the middle -> revert middle to the "before" value.
        cond2 = (
            before.neq(3)
            .And(middle.eq(3))
            .And(after.neq(3))
            .And((zero_image2.eq(3).Or(zero_image2.eq(4))))
        )

        middle = middle.where(cond1, 3)        # where cond1 holds, set pixel to 3 (forest).
        middle = middle.where(cond2, before)   # where cond2 holds, set pixel back to before's value.

        l1_asset_copy[i] = middle              # Store the cleaned middle year back into the copy.

    # Remap the CLEANED series into simplified classes (forest -> 3 here).
    def remap_values(image):
        remapped = image.remap(
            [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12],
            [1, 2, 2, 2, 3, 5, 4, 4, 4, 4, 6],
            0,
            "predicted_label",
        ).setDefaultProjection(lulc_projection)
        return remapped

    l1_asset_remapped = [remap_values(asset) for asset in l1_asset_copy]

    then = ee.ImageCollection(l1_asset_remapped[:3]).mode().reproject(lulc_projection)
    now = ee.ImageCollection(l1_asset_remapped[3:]).mode().reproject(lulc_projection)

    then = then.clip(roi_boundary.geometry())
    now = now.clip(roi_boundary.geometry())
    return now, then            # Callers decide the direction of the transition.


def change_deforestation(roi_boundary, l1_asset):
    """DEFORESTATION: forest (3) in 'then' -> something else in 'now'."""
    lulc_projection = l1_asset[0].projection()
    now, then = change_deforestation_afforestation(   # reuse the cleaned then/now images
        roi_boundary, l1_asset, lulc_projection
    )
    trans_fo_fo = then.eq(3).And(now.eq(3))                 # forest stayed forest        -> code 1
    trans_fo_bu = then.eq(3).And(now.eq(1)).multiply(2)    # forest -> built-up          -> code 2
    trans_fo_fa = then.eq(3).And(now.eq(4)).multiply(3)    # forest -> farm/other (4)    -> code 3
    trans_fo_ba = then.eq(3).And(now.eq(5)).multiply(4)    # forest -> barren (5)        -> code 4
    trans_sc = then.eq(3).And(now.eq(6)).multiply(5)       # forest -> scrub (6)         -> code 5
    change_def = (
        ee.Image.constant(0)
        .setDefaultProjection(lulc_projection)
        .clip(roi_boundary.geometry())
    )
    change_def = (
        change_def.add(trans_fo_fo)
        .add(trans_fo_bu)
        .add(trans_fo_fa)
        .add(trans_fo_ba)
        .add(trans_sc)
    )
    return change_def


def change_afforestation(roi_boundary, l1_asset):
    """AFFORESTATION: the REVERSE — something in 'then' -> forest (3) in 'now'."""
    lulc_projection = l1_asset[0].projection()
    now, then = change_deforestation_afforestation(
        roi_boundary, l1_asset, lulc_projection
    )
    trans_fo_fo = then.eq(3).And(now.eq(3))                 # forest stayed forest        -> code 1
    trans_bu_fo = then.eq(1).And(now.eq(3)).multiply(2)    # built-up -> forest          -> code 2
    trans_fa_fo = then.eq(4).And(now.eq(3)).multiply(3)    # farm/other -> forest        -> code 3
    trans_ba_fo = then.eq(5).And(now.eq(3)).multiply(4)    # barren -> forest            -> code 4
    trans_sc_fo = then.eq(6).And(now.eq(3)).multiply(5)    # scrub -> forest             -> code 5

    change_af = (
        ee.Image.constant(0)
        .setDefaultProjection(lulc_projection)
        .clip(roi_boundary.geometry())
    )
    change_af = (
        change_af.add(trans_fo_fo)
        .add(trans_bu_fo)
        .add(trans_fa_fo)
        .add(trans_ba_fo)
        .add(trans_sc_fo)
    )
    return change_af


def change_cropping_intensity(roi_boundary, l1_asset):
    """
    CROP INTENSITY: changes between single (5), double (6), triple (7) cropping.
    'si' = single-crop, 'do' = double-crop, 'tr' = triple-crop.
    """
    lulc_projection = l1_asset[0].projection()

    # Remap so cropping classes become 5 (single), 6 (double), 7 (triple), etc.
    def remap_values(image):
        return image.remap(
            [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12],
            [1, 2, 2, 2, 3, 4, 5, 5, 6, 7, 8],
            0,
            "predicted_label",
        ).setDefaultProjection(lulc_projection)

    l1_asset_remapped = [remap_values(asset) for asset in l1_asset]

    then = ee.ImageCollection(l1_asset_remapped[:3]).mode().reproject(lulc_projection)
    now = ee.ImageCollection(l1_asset_remapped[3:]).mode().reproject(lulc_projection)

    then = then.clip(roi_boundary.geometry())
    now = now.clip(roi_boundary.geometry())

    # Every crop-to-crop transition gets its own unique code (1..9):
    trans_do_si = then.eq(6).And(now.eq(5))                 # double -> single   -> code 1
    trans_tr_si = then.eq(7).And(now.eq(5)).multiply(2)    # triple -> single   -> code 2
    trans_tr_do = then.eq(7).And(now.eq(6)).multiply(3)    # triple -> double   -> code 3
    trans_si_do = then.eq(5).And(now.eq(6)).multiply(4)    # single -> double   -> code 4
    trans_si_tr = then.eq(5).And(now.eq(7)).multiply(5)    # single -> triple   -> code 5
    trans_do_tr = then.eq(6).And(now.eq(7)).multiply(6)    # double -> triple   -> code 6
    si_si = then.eq(5).And(now.eq(5)).multiply(7)          # single stayed single -> code 7
    do_do = then.eq(6).And(now.eq(6)).multiply(8)          # double stayed double -> code 8
    tr_tr = then.eq(7).And(now.eq(7)).multiply(9)          # triple stayed triple -> code 9
    # (The commented-out trans_same block was an earlier combined version, now replaced
    #  by the three separate si_si / do_do / tr_tr "no change" codes above.)

    change_far = (
        ee.Image.constant(0)
        .setDefaultProjection(lulc_projection)
        .clip(roi_boundary.geometry())
    )
    change_far = (
        change_far.add(trans_do_si)
        .add(trans_tr_si)
        .add(trans_tr_do)
        .add(trans_si_do)
        .add(trans_si_tr)
        .add(trans_do_tr)
        .add(si_si)
        .add(do_do)
        .add(tr_tr)
    )
    return change_far


def sync_to_gcs_geoserver(
    state, district, block, description, param_list, layer_ids, start_year, end_year
):
    """
    PUBLISHING STEP: take each exported GEE raster and make it visible to the app.
      GEE asset -> GCS (cloud storage) -> GeoServer (serves map tiles to frontend).
    Returns True only if every param made it all the way to GeoServer.
    """
    task_list = []

    # First, export each raster from GEE to GCS (async tasks).
    for change in param_list:
        image = ee.Image(   # Reference the already-exported change raster.
            get_gee_asset_path(state, district, block)
            + f"{description}_{change}_{start_year}_{end_year}"
        )
        task_id = sync_raster_to_gcs(   # Start GEE -> GCS export at 10m scale.
            image, 10, f"{description}_{change}_{start_year}_{end_year}"
        )
        task_list.append(task_id)
    task_id_list = check_task_status(task_list)   # Wait for all GCS exports to finish.
    print("task_id sync to gcs ", task_id_list)

    layer_at_geoserver = []   # Tracks which params successfully reached GeoServer.
    # Then, publish each GCS raster into GeoServer and update the DB sync flag.
    for change in param_list:
        res = sync_raster_gcs_to_geoserver(
            "change_detection",                                  # GeoServer workspace/store name
            f"{description}_{change}_{start_year}_{end_year}",   # source name in GCS
            description + "_" + change,                          # published layer name
            change.lower(),                                      # style name (lowercase param)
        )
        if res and layer_ids[change]:   # If publish succeeded and we have a DB id for it...
            sync_status = update_layer_sync_status(   # mark the layer as synced in the DB.
                layer_id=layer_ids[change], sync_to_geoserver=True
            )
            print("sync to geoserver flag updated")
            if sync_status:
                layer_at_geoserver.append(sync_status)

    # True only if the number of successfully-synced layers equals the number of params.
    return len(layer_at_geoserver) == len(param_list)
