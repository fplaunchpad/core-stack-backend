# Presentation: Scientific Innovation in CoRE Stack NRM Planning

---

## 🛝 Slide 1: Introduction & Mission

### Title: Scientific Decision-Making in Natural Resource Management (NRM)

* **Subtitle**: Transforming Hydrological & Land Restoration Planning with CoRE Stack
* **Core Concept**:
  * CoRE Stack integrates **satellite remote sensing, cloud computing (Google Earth Engine), and participatory field mapping** to scientifically design watershed interventions (check dams, farm ponds, trenches).
* **The Technology Shift**:

| Attribute | Old Conventional NRM | New Scientific NRM (CoRE Stack) |
|---|---|---|
| **Data Source** | Coarse static paper maps, decadal reports | High-resolution multi-temporal satellite imagery (10m resolution) |
| **Analysis Speed**| Weeks of manual calculations by specialists | Minutes of automated cloud-based processing |
| **Planning Scale** | Aggregated regional approximations | Granular Microwatershed-level (MWS) spatial precision |

---

## 🛝 Slide 2: Hydrological Water Budgeting

### Title: Spatial Groundwater Recharge & Water Balance Modeling
* **Implementation File**: [computing/mws/generate_hydrology.py](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/generate_hydrology.py)

* **The Science**:
  * Calculates the exact water balance equation per Microwatershed:
    $$\Delta G = Precipitation (P) - Runoff (Q) - Evapotranspiration (ET)$$
    *   *P script*: [computing/mws/precipitation.py](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/precipitation.py)
    *   *ET script*: [computing/mws/evapotranspiration.py](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/evapotranspiration.py)
    *   *Runoff script*: [computing/mws/run_off.py](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/run_off.py)
  * Predicts localized **Well Depth Fluctuation** ($wd$) by dividing Recharge ($\Delta G$) by specific Aquifer Yield ($S_y$):
    $$wd = \frac{\Delta G}{S_y \times 1000}$$
    *   *Well Depth script*: [computing/mws/well_depth.py](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/well_depth.py)
* **The Technology Shift**:

| Attribute | Old Conventional Methods | New CoRE Stack Engine |
|---|---|---|
| **Rainfall Data** | Sparse local rain gauges (poor spatial coverage) | Daily GPM GSMaP (JAXA) satellite precipitation |
| **Runoff Estimation**| Static empirical tables (ignoring terrain slopes) | Slope-Adjusted SCS Curve Number (SCS-CN) algorithm using SRTM DEM |
| **Evapotranspiration**| Approximated via temp averages | Daily NASA FLDAS/GLDAS Land Data Assimilation model |
| **Groundwater Link**| Disconnected from soil/rock properties | Multi-layered aquifer yield modeling ($S_y$) for water table projection |

---

## 🛝 Slide 3: Composite Drought Monitoring

### Title: Combining Meteorological & Agricultural Stress Indicators
* **Implementation File**: [computing/drought/generate_layers.py](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/generate_layers.py)

* **The Science**:
  * Instead of measuring rainfall deficits alone, CoRE Stack builds a composite index mapping:
    * **Meteorological Drought**: Short-term dry spells & Standardized Precipitation Index (SPI-1).
    * **Agricultural Drought**: Vegetation Condition Index (VCI) from MODIS NDVI/NDWI and Moisture Adequacy Index (MAI) from MODIS ET/PET.
    * **Sowing Progress**: Sowing index (PAS) extracted via dynamic LULC land cover tracking.
* **The Technology Shift**:

| Attribute | Old Conventional Methods | New CoRE Stack Engine |
|---|---|---|
| **Primary Metric** | Cumulative rainfall deficit (ignores soil & crop health) | Composite index combining meteorological, soil, and vegetation stress |
| **Sowing Tracking**| Delayed manual report aggregations | Dynamic Land Use Land Cover (LULC) cropping classification |
| **Vegetation Health**| Visual crop damage surveys | MODIS NDVI baseline standardization (VCI) in crop-zone masks |

---

## 🛝 Slide 4: CLART (Composite Land Assessment & Restoration Tool)

### Title: Automated Land Treatment & Recharge Recommendation Matrix
* **Implementation File**: [computing/clart/clart.py](file:///home/snaveen/Desktop/core-stack-backend/computing/clart/clart.py)

* **The Science**:
  * Intersects geological rock permeability (**Lithology**), structural fractures (**Lineaments**), stream density (**Drainage Density**), and terrain gradients (**Slope %**) to calculate Recharge Potential ($rp$).
    *   *Lithology script*: [computing/clart/lithology.py](file:///home/snaveen/Desktop/core-stack-backend/computing/clart/lithology.py)
  * Classes 1–5 assign precise treatments: Class 1 (recharge structures), Class 2 (farm ponds), Class 3 (vegetative guards), Class 4/5 (trenches and gully plugs based on steepness).
* **The Technology Shift**:

| Attribute | Old Conventional Methods | New CoRE Stack Engine |
|---|---|---|
| **Site Selection** | Subjective field observations, political defaults | Automated GIS decision matrix selecting optimal engineering coordinates |
| **Topography** | Approximate contours from topographic maps | Exact slope gradients extracted from FABDEM/SRTM DEM rasters |
| **Geological Check** | Expensive field-bore tests or offline maps | Layer overlay of lithology, drainage density, and lineament structures |

---

## 🛝 Slide 5: Participatory NRM Planning (The Closed Loop)

### Title: Bridging Satellite Science with Community Needs

* **The Science**:
  * Bridges high-level science with ground reality by creating an interactive, closed-loop planning system.
  * Scientific recommendations from GEE are loaded into mobile tools (**Open Data Kit - ODK**) used by local communities. Local inputs feed back into the server to generate final **Detailed Project Reports (DPR)**.
    *   *ODK Parsing / Ingestion*: [plans/](file:///home/snaveen/Desktop/core-stack-backend/plans/)
    *   *Yuktdhara DPR Generation*: [dpr/](file:///home/snaveen/Desktop/core-stack-backend/dpr/)
* **The Technology Shift**:

| Attribute | Old Conventional Methods | New CoRE Stack Engine |
|---|---|---|
| **Field Data Collection**| Paper forms, manual data entry errors | Offline mobile surveys (ODK) with automatic GPS validation |
| **Report Generation** | Months of writing, drafting, and manual editing | Immediate PDF/Excel DPR reports and Yuktdhara uploads |
| **Verification Loop** | No feedback loop; plan is fixed in office | Collaborative validation: Ground surveys verify satellite recommendations |
