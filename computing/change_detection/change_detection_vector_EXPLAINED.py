"""
================================================================================
 change_detection_vector_EXPLAINED.py
================================================================================
 ANNOTATED CLONE of `change_detection_vector.py`. The original is NOT modified.

 WHERE THIS FITS
 ---------------
 `change_detection.py` produced 5 RASTER layers (pixel images of coded land-use
 transitions). THIS file is the next stage: it turns those rasters into VECTOR
 outputs. For every micro-watershed (MWS) polygon it computes the AREA (in
 hectares) of each transition class, attaches those numbers as polygon
 attributes, and publishes the result to GeoServer so the dashboard can show
 "X hectares deforested in this watershed", etc.

 PIPELINE:
   load MWS polygons (roi)  ->  for each of the 5 change types:
     for each transition class: mask the raster, sum pixel area per polygon,
     store as a labeled attribute  ->  export vector asset to GEE  ->
     save metadata to DB  ->  publish to GeoServer.

 KEY GEE CONCEPT USED HERE:
   reduceRegions(collection=polygons, reducer=sum, ...) overlays a raster on a
   set of polygons and returns, per polygon, the SUM of pixel values inside it.
   Here the raster is "pixel area, but only where this transition occurred",
   so the sum = total area of that transition inside the polygon.
================================================================================
"""

import ee                       # Earth Engine Python client.
from computing.utils import (   # DB + GeoServer helpers shared across the app:
    sync_layer_to_geoserver,    #   - pushes a GeoJSON FeatureCollection to GeoServer.
    save_layer_info_to_db,      #   - records the layer in the app DB, returns a layer_id.
    update_layer_sync_status,   #   - flips the "synced to GeoServer" flag in the DB.
)
from utilities.gee_utils import (   # GEE helpers (same family as in change_detection.py):
    ee_initialize,              #   - authenticate the EE session.
    check_task_status,          #   - wait for EE export tasks to finish.
    valid_gee_text,             #   - sanitize names for GEE assets.
    get_gee_asset_path,         #   - base asset folder for this location.
    is_gee_asset_exists,        #   - check whether an asset already exists.
    export_vector_asset_to_gee, #   - start an EE export of a FeatureCollection (vector) to a GEE asset.
    make_asset_public,          #   - make an asset publicly readable.
)
from nrm_app.celery import app  # Celery app for the @app.task decorator.


@app.task(bind=True)            # Register as a Celery background task.
def vectorise_change_detection(
    self, state, district, block, start_year, end_year, gee_account_id
):
    """
    ENTRY POINT (Celery task).
    Generates change-detection VECTORS for all 5 params at tehsil (block) level.
    Assumes the RASTERS from change_detection.py already exist.
    """
    ee_initialize(gee_account_id)   # Authenticate with GEE.

    # Load the micro-watershed polygons (the units we compute areas for).
    roi = ee.FeatureCollection(
        get_gee_asset_path(state, district, block)
        + "filtered_mws_"
        + valid_gee_text(district.lower())
        + "_"
        + valid_gee_text(block.lower())
        + "_uid"
    )

    # Build all 5 vector layers; each call returns an EE export task id.
    # (These run the generate_vector pipeline once per change type.)
    task_list = [
        afforestation_vector(roi, state, district, block, start_year, end_year),
        deforestation_vector(roi, state, district, block, start_year, end_year),
        degradation_vector(roi, state, district, block, start_year, end_year),
        urbanization_vector(roi, state, district, block, start_year, end_year),
        crop_intensity_vector(roi, state, district, block, start_year, end_year),
    ]

    print(task_list)
    task_id_list = check_task_status(task_list)   # Wait for all 5 vector exports to complete.
    print("Change vector task completed - task_id_list:", task_id_list)

    # The 5 param names, used to locate each exported asset and register it.
    param_list = [
        "Urbanization",
        "Degradation",
        "Deforestation",
        "Afforestation",
        "CropIntensity",
    ]
    layer_at_geoserver = False   # Tracks the result of the LAST GeoServer sync (return value).
    for param in param_list:
        # Reconstruct the exact asset name produced by generate_vector() below.
        description = f"change_vector_{valid_gee_text(district)}_{valid_gee_text(block)}_{param}_{start_year}_{end_year}"
        asset_id = get_gee_asset_path(state, district, block) + description
        if is_gee_asset_exists(asset_id):   # Only process params whose export actually succeeded.
            layer_id = save_layer_info_to_db(   # Record this vector layer in the DB.
                state,
                district,
                block,
                layer_name=f"change_vector_{valid_gee_text(district.lower())}_{valid_gee_text(block.lower())}_{param}",
                asset_id=asset_id,
                dataset_name="Change Detection Vector",
            )
            make_asset_public(asset_id)         # Allow services to read the asset.
            layer_at_geoserver = sync_change_to_geoserver(   # Publish this vector to GeoServer.
                block, district, state, asset_id, param, layer_id
            )

    return layer_at_geoserver   # Reflects whether the final param synced successfully.


# ==========================================================================
# THE 5 "ARGS" DEFINITIONS
# ==========================================================================
# Each function below just defines the LIST of transition classes for one change
# type, then delegates to generate_vector(). The args mirror the integer codes
# the raster used in change_detection.py:
#   - "value" = the pixel code(s) in the raster for that transition.
#   - "label" = the attribute name to store that transition's AREA under.
# A list "value" (e.g. [2,3,4,5]) means "any of these codes" -> a TOTAL column.
# ==========================================================================


# Afforestation  (codes match change_afforestation in change_detection.py)
def afforestation_vector(roi, state, district, block, start_year, end_year):
    args = [
        {"value": 1, "label": "fo_fo"},   # 1 = forest stayed forest
        {"value": 2, "label": "bu_fo"},   # 2 = built-up -> forest
        {"value": 3, "label": "fa_fo"},   # 3 = farm -> forest
        {"value": 4, "label": "ba_fo"},   # 4 = barren -> forest
        {"value": 5, "label": "sc_fo"},   # 5 = scrub -> forest
        {"value": [2, 3, 4, 5], "label": "total_aff"},   # any real gain -> total afforestation
    ]  # Classes in afforestation raster layer

    return generate_vector(
        roi, args, state, district, block, "Afforestation", start_year, end_year
    )


# Deforestation  (codes match change_deforestation)
def deforestation_vector(roi, state, district, block, start_year, end_year):
    args = [
        {"value": 1, "label": "fo_fo"},   # forest stayed forest
        {"value": 2, "label": "fo_bu"},   # forest -> built-up
        {"value": 3, "label": "fo_fa"},   # forest -> farm
        {"value": 4, "label": "fo_ba"},   # forest -> barren
        {"value": 5, "label": "fo_sc"},   # forest -> scrub
        {"value": [2, 3, 4, 5], "label": "total_def"},   # any real loss -> total deforestation
    ]  # Classes in deforestation raster layer

    return generate_vector(
        roi, args, state, district, block, "Deforestation", start_year, end_year
    )


# Degradation  (codes match change_degradation)
def degradation_vector(roi, state, district, block, start_year, end_year):

    args = [
        {"value": 1, "label": "f_f"},     # forest stayed forest
        {"value": 2, "label": "f_bu"},    # forest -> built-up
        {"value": 3, "label": "f_ba"},    # forest -> barren
        {"value": 4, "label": "f_sc"},    # forest -> scrub
        {"value": [2, 3, 4], "label": "total_deg"},   # any degradation -> total
    ]  # Classes in degradation raster layer

    return generate_vector(
        roi, args, state, district, block, "Degradation", start_year, end_year
    )


# Urbanization  (codes match built_up)
def urbanization_vector(roi, state, district, block, start_year, end_year):
    args = [
        {"value": 1, "label": "bu_bu"},   # built-up stayed built-up
        {"value": 2, "label": "w_bu"},    # water -> built-up
        {"value": 3, "label": "tr_bu"},   # tree/green -> built-up
        {"value": 4, "label": "b_bu"},    # barren -> built-up
        {"value": [2, 3, 4], "label": "total_urb"},   # any new built-up -> total urbanization
    ]  # Classes in urbanization raster layer

    return generate_vector(
        roi, args, state, district, block, "Urbanization", start_year, end_year
    )


# CropIntensity  (codes match change_cropping_intensity)
def crop_intensity_vector(roi, state, district, block, start_year, end_year):

    args = [
        {"value": 1, "label": "do_si"},   # double -> single
        {"value": 2, "label": "tr_si"},   # triple -> single
        {"value": 3, "label": "tr_do"},   # triple -> double
        {"value": 4, "label": "si_do"},   # single -> double
        {"value": 5, "label": "si_tr"},   # single -> triple
        {"value": 6, "label": "do_tr"},   # double -> triple
        {"value": 7, "label": "si_si"},   # single stayed single
        {"value": 8, "label": "do_do"},   # double stayed double
        {"value": 9, "label": "tr_tr"},   # triple stayed triple
        {"value": [1, 2, 3, 4, 5, 6], "label": "total_change"},   # any intensity change -> total
    ]  # Classes in crop_intensity raster layer

    return generate_vector(
        roi, args, state, district, block, "CropIntensity", start_year, end_year
    )


def generate_vector(
    roi, args, state, district, block, layer_name, start_year, end_year
):
    """
    THE CORE WORKER.
    For one change type, computes per-polygon AREA of each transition class and
    attaches them as attributes on the MWS polygons, then exports as a vector.
    """
    # Load the corresponding RASTER produced by change_detection.py.
    raster = ee.Image(
        get_gee_asset_path(state, district, block)
        + f"change_{valid_gee_text(district.lower())}_{valid_gee_text(block.lower())}_{layer_name}_{start_year}_{end_year}"
    )  # Change detection raster layer

    fc = roi                     # Start from the plain polygons; we add columns one class at a time.
    for arg in args:             # Loop over every transition class for this change type.
        raster = raster.select(["constant"])   # Select the band holding the coded values (named "constant").

        # Build a boolean MASK that is 1 where the pixel == this class' code(s).
        if isinstance(arg["value"], list) and len(arg["value"]) > 1:
            # Multi-code ("total") case: OR together raster.eq(code) for each code.
            # The code builds the expression as a STRING then eval()s it.
            ored_str = "raster.eq(ee.Number(" + str(arg["value"][0]) + "))"
            for i in range(1, len(arg["value"])):
                ored_str = (
                    ored_str + ".Or(raster.eq(ee.Number(" + str(arg["value"][i]) + ")))"
                )
            print(ored_str)      # (debug) prints the constructed expression.
            mask = eval(ored_str)   # mask = raster.eq(c0).Or(raster.eq(c1))...  -> 1 where any code matches.
        else:
            # Single-code case: simple equality mask.
            mask = raster.eq(ee.Number(arg["value"]))

        pixel_area = ee.Image.pixelArea()        # Image where each pixel value = its ground area in m^2.
        forest_area = pixel_area.updateMask(mask)   # Keep area only where this transition occurred; else masked out.

        # For each polygon in fc, SUM the (masked) pixel areas inside it.
        # Result: each feature gains a "sum" property = m^2 of this transition.
        fc = forest_area.reduceRegions(
            collection=fc,
            reducer=ee.Reducer.sum(),
            scale=10,                            # 10m sampling, matching the raster.
            crs=raster.projection(),             # use the raster's CRS for correct areas.
        )

        # Helper: drop a named property from a feature (used to remove the raw "sum").
        def remove_property(feat, prop):
            properties = feat.propertyNames()
            select_properties = properties.filter(ee.Filter.neq("item", prop))
            return feat.select(select_properties)

        # Helper: convert the raw "sum" (m^2) into hectares, store under this class' label.
        def process_feature(feature):
            value = feature.get("sum")           # area in square meters from reduceRegions
            value = ee.Number(value).multiply(0.0001)   # m^2 -> hectares (1 ha = 10,000 m^2)
            feature = feature.set(arg["label"], value)  # store as e.g. "fo_bu" = <hectares>
            feature = remove_property(feature, "sum")   # remove the temporary "sum" so next loop is clean
            return feature

        fc = fc.map(process_feature)   # Apply to every polygon, adding this class' hectare column.

    fc = ee.FeatureCollection(fc)      # Ensure final result is typed as a FeatureCollection.

    # Export the finished vector (polygons + all the per-class hectare columns) to GEE.
    description = f"change_vector_{valid_gee_text(district)}_{valid_gee_text(block)}_{layer_name}_{start_year}_{end_year}"
    task = export_vector_asset_to_gee(
        fc, description, get_gee_asset_path(state, district, block) + description
    )
    return task                  # Return the export task id so the caller can wait on it.


def sync_change_to_geoserver(block, district, state, asset_id, param, layer_id):
    """
    Publish one finished VECTOR asset to GeoServer and update the DB sync flag.
    """
    # (The commented dict below was a planned mapping of params to STAC spec layer
    #  names; kept for reference but not currently used.)
    # stac_spec_layer_name_dict = {
    #     "Urbanization": "change_urbanization_vector",
    #     "Degradation": "change_cropping_reduction_vector",
    #     "Deforestation": "change_tree_cover_loss_vector",
    #     "Afforestation": "change_tree_cover_gain_vector",
    #     "CropIntensity": "change_cropping_intensity_vector",
    # }
    fc = ee.FeatureCollection(asset_id).getInfo()   # Pull the FeatureCollection from GEE to the client as GeoJSON.
    fc = {"features": fc["features"], "type": fc["type"]}   # Keep only the fields GeoServer needs.
    res = sync_layer_to_geoserver(   # Upload the GeoJSON to GeoServer under a descriptive layer name.
        state,
        fc,
        "change_vector_"
        + valid_gee_text(district.lower())
        + "_"
        + valid_gee_text(block.lower())
        + "_"
        + param,
        "change_detection",          # GeoServer workspace.
    )
    print(res)

    # On success (HTTP 201 Created) and if we have a DB id, mark it synced.
    if res["status_code"] == 201 and layer_id:
        update_layer_sync_status(layer_id=layer_id, sync_to_geoserver=True)
        print("sync to geoserver flag updated")
        return True
    return False
