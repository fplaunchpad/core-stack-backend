# CoRE Stack Mathematical Formulae Reference

This document compiles **every** mathematical equation, environmental index, decision rule, and classification threshold implemented in the CoRE Stack backend. Each entry gives: the formula, a table expanding **every variable to its full form** (name, meaning, units, data source), the NRM/planning **use case**, and the exact **file:line location**.

> Verified against source on 2026-06-12. Formulae marked **⚠ as-coded** reproduce exactly what the code does even where it deviates from the textbook form — important if you are porting these (see `convert.md`).

**Datasets referenced throughout** (full forms used once here):
- **JAXA GSMaP** — Japan Aerospace Exploration Agency, Global Satellite Mapping of Precipitation (hourly rain rate).
- **NASA FLDAS** — Famine Early Warning Systems Network Land Data Assimilation System (monthly ET).
- **MODIS** — Moderate Resolution Imaging Spectroradiometer (NDVI/NDWI/ET/PET products, 2000→).
- **CHIRPS** — Climate Hazards Group InfraRed Precipitation with Station data (1981→).
- **SRTM** — Shuttle Radar Topography Mission digital elevation model (30 m).
- **Dynamic World** — Google/WRI 10 m near-real-time LULC classification.
- **CGWB** — Central Ground Water Board (India) principal-aquifer polygons.
- **IndiaSAT** — India-specific LULC classification used in tree-health change.

---

## 1. Hydrological Water Budgeting Pipeline

Computes the fortnightly (14-day) or annual (365-day, hydro-year July 1 → June 30) water balance per **MWS (Microwatershed)** — the core unit of analysis. Chain: $P \rightarrow Q \rightarrow ET \rightarrow \Delta G \rightarrow G \rightarrow$ well-depth.

### 1A. Precipitation ($P$)

$$P = \sum_{t=1}^{T} \text{hourlyPrecipRate}_t$$

| Variable | Full form | Meaning | Units | Source |
|---|---|---|---|---|
| $P$ | Precipitation | Total rainfall depth over the period | mm | computed |
| $\text{hourlyPrecipRate}_t$ | hourly precipitation rate at hour $t$ | satellite-observed rain rate | mm/hr | JAXA GSMaP (`JAXA_PPT`), band `hourlyPrecipRate`; summed band is `hourlyPrecipRate_sum` |
| $T$ | total hours | hours in the 14-day fortnight or 365-day hydro-year | — | — |

- **Use case**: total water influx from rainfall; first term of the water balance.
- **Location**: [computing/mws/precipitation.py](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/precipitation.py#L98-L121)

### 1B. Evapotranspiration ($ET$) — FLDAS source (water budget)

**⚠ as-coded**: only the $86400$ conversion applies here; the $0.1$ scale factor belongs to the **MODIS** ET used for MAI (§2B), *not* FLDAS.

$$ET_{\text{daily}} = \begin{cases} \text{Evap\_tavg} \times 86400 & \text{if } \text{Evap\_tavg} > 0 \\ 0 & \text{otherwise} \end{cases}
\qquad ET_{\text{MWS}} = \frac{\sum_{\text{pixels}} \sum_d ET_{\text{daily},d}}{N_{\text{pixels}}}$$

| Variable | Full form | Meaning | Units | Source |
|---|---|---|---|---|
| $ET$ | Evapotranspiration | water returned to atmosphere (soil evaporation + plant transpiration) | mm | computed |
| $\text{Evap\_tavg}$ | average evapotranspiration rate (band name) | mass flux of evaporated water | kg · m⁻² · s⁻¹ | NASA FLDAS |
| $86400$ | seconds per day | converts kg/m²/s → kg/m²/day; 1 kg/m² of water ≡ 1 mm depth | s/day | constant |
| $N_{\text{pixels}}$ | pixel count | valid pixels in the MWS (the per-region value is sum ÷ count, i.e. spatial mean) | — | reducer |

- **Use case**: the loss term of the water balance.
- **Location**: [computing/mws/evapotranspiration.py](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/evapotranspiration.py#L262-L282) (expression `"ET>0?86400*ET:0"` at L262; pixel-count division at L279)

### 1C. Hydrologic Soil Group remap

Soil raster values are remapped to the four **HSG (Hydrologic Soil Group)** classes:

$$\text{soil} = \begin{cases} 1 & b_1 = 11 \;(\text{HSG A — high infiltration}) \\ 2 & b_1 = 12 \;(\text{HSG B}) \\ 3 & b_1 = 13 \;(\text{HSG C}) \\ 4 & b_1 = 14 \;(\text{HSG D — very low infiltration}) \end{cases}$$

- **Source**: `GLOBAL_HYDROLOGIC_SOIL_GROUPS` raster, band `b1`.
- **Location**: [computing/mws/run_off.py:197-205](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/run_off.py#L197-L205)

### 1D. Base Curve Number lookup ($CN_2$)

$CN_2$ (**Curve Number** for average antecedent-moisture conditions, SCS-CN method of the USDA Soil Conservation Service) is a lookup over (HSG × Dynamic World LULC class). LULC composite = per-pixel **mode** of Dynamic World `label` over the period.

| Dynamic World class (`lulc`) | HSG A (1) | HSG B (2) | HSG C (3) | HSG D (4) |
|---|---|---|---|---|
| 0 water | 0 | 0 | 0 | 0 |
| 1 trees | 30 | 55 | 70 | 77 |
| 2 grass | 39 | 61 | 74 | 80 |
| 3 flooded vegetation | 0 | 0 | 0 | 0 |
| 4 crops | 64 | 75 | 82 | 85 |
| 5 shrub & scrub | 39 | 61 | 74 | 80 |
| 6 built-up | 82 | 88 | 91 | 93 |
| 7 bare ground | 49 | 69 | 79 | 84 |

Higher $CN$ ⇒ more runoff, less infiltration ($CN \in [0,100]$).

- **Use case**: encodes the combined land-cover + soil response to rainfall.
- **Location**: [computing/mws/run_off.py:233-278](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/run_off.py#L233-L278)

### 1E. Wet-condition Curve Number ($CN_3$) and slope-adjusted $CN_{2a}$

$$CN_3 = CN_2 \cdot 2.718^{\,0.00673\,(100 - CN_2)}$$
$$CN_{2a} = \underbrace{\frac{CN_3 - CN_2}{3}}_{p_1} \cdot \underbrace{\left(1 - 2 \cdot 2.718^{-13.86\,\alpha}\right)}_{p_2} + CN_2$$

| Variable | Full form | Meaning | Units | Source |
|---|---|---|---|---|
| $CN_3$ | wet-AMC Curve Number (unadjusted) | CN under saturated soil, Sharpley–Williams form | — | computed |
| $CN_{2a}$ | slope-adjusted average-AMC Curve Number | corrects $CN_2$ upward on steep terrain | — | computed |
| $\alpha$ | slope (code: `slope`) | terrain slope from `ee.Terrain.slope(DEM)` — **⚠ as-coded in degrees**, not m/m fraction as in the original Sharpley–Williams equation | degrees | SRTM DEM |
| $2.718$ | Euler's number $e$ (approximated) | — | — | constant |

- **Use case**: prevents underestimating runoff on steep slopes.
- **Location**: [computing/mws/run_off.py:286-320](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/run_off.py#L286-L320)

### 1F. Dry and wet AMC Curve Numbers ($CN_{1a}, CN_{3a}$)

**AMC = Antecedent Moisture Condition** (I = dry, II = average, III = wet).

$$CN_{1a} = \frac{4.2\,CN_{2a}}{10 - 0.058\,CN_{2a}} \qquad CN_{3a} = \frac{23\,CN_{2a}}{10 + 0.13\,CN_{2a}}$$

- **Use case**: shift the retention capacity with how wet the soil already is.
- **Location**: [computing/mws/run_off.py:322-332](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/run_off.py#L322-L332)

### 1G. Potential Maximum Retention ($S$, code `sr1/sr2/sr3`)

$$sr_k = \frac{25400}{CN_{ka}} - 254 \qquad k \in \{1,2,3\}$$

| Variable | Full form | Meaning | Units |
|---|---|---|---|
| $sr_k$ | potential maximum soil-moisture retention for AMC class $k$ | depth of water the soil can still absorb before runoff | mm |
| $25400, 254$ | metric constants of the SCS-CN equation ($1000/CN - 10$ inches → mm) | — | mm |

- **Use case**: per-pixel infiltration ceiling for each moisture condition.
- **Location**: [computing/mws/run_off.py:334-350](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/run_off.py#L334-L350)

### 1H. Daily rainfall windows ($P$, $P_5$)

The runoff loop iterates day index $i$ = 0…364 (annual) or 0…14 (fortnight), counting back from the period end (`base`):

- $P$ — **current-day precipitation**: GSMaP sum over $(\text{base}-i-1,\; \text{base}-i]$ → 1 day.
- $P_5$ — **antecedent precipitation** (code `antecedent`): GSMaP sum over $(\text{base}-i-4,\; \text{base}-i]$ — **⚠ as-coded a 4-day window**, conventionally referred to as the 5-day antecedent rainfall in SCS-CN literature.

- **Location**: [computing/mws/run_off.py:352-369](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/run_off.py#L352-L369) (window construction), [L401-408](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/run_off.py#L401-L408) ($P$)

### 1I. Antecedent moisture amount ($M$, code `m1/m2/m3`)

$$M_k = 0.5\left(-sr_k + \sqrt{sr_k^2 + 4\,P_5\,sr_k}\right) \qquad k \in \{1,2,3\}$$

| Variable | Full form | Meaning | Units |
|---|---|---|---|
| $M_k$ | antecedent soil-moisture amount for AMC class $k$ (Mishra–Singh modified SCS-CN) | water already stored in soil from the previous days' rain | mm |
| $P_5$ | antecedent rainfall (§1H) | — | mm |

- **Use case**: real-time soil saturation before computing the day's runoff.
- **Location**: [computing/mws/run_off.py:371-399](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/run_off.py#L371-L399)

### 1J. Daily Surface Runoff ($Q$)

**⚠ as-coded** — one chained ternary expression, evaluated top-to-bottom (first true branch wins):

$$Q = \begin{cases}
\dfrac{(P - 0.2\,sr_1)(P - 0.2\,sr_1 + M_1)}{P + 1.2\,sr_1 + M_1} & \text{if } P \ge 0.2\,sr_1,\; 0 \le P_5 \le 35,\; Q \ge 0 \quad (\text{AMC I, dry})\\[2ex]
\dfrac{(P - 0.2\,sr_2)(P - 0.2\,sr_2 + M_2)}{P + 1.2\,sr_2 + M_2} & \text{if } P \ge 0.2\,sr_2,\; P_5 > 35,\; Q \ge 0 \quad (\text{AMC II})\\[2ex]
\dfrac{(P - 0.2\,sr_3)(P - 0.2\,sr_3 + M_3)}{P + 1.2\,sr_3 + M_3} & \text{if } P \ge 0.2\,sr_3,\; P_5 > 52.5,\; Q \ge 0 \quad (\text{AMC III, wet})\\[1ex]
0 & \text{otherwise}
\end{cases}$$

Two faithful-to-code notes (relevant for the OCaml port):
1. **Denominator** is literally `P + 0.2*sr + sr + m` = $P + 1.2\,sr + M$ — the textbook Mishra–Singh form is $P + 0.8\,S + M$. Document/decide before porting.
2. **Branch order**: because the AMC II branch condition is `P5 > 35`, it also captures $P_5 > 52.5$; the AMC III branch is reached only when the AMC II guard fails (e.g. $P < 0.2\,sr_2$ or its quotient is negative).

Daily $Q$ images are summed over the period (`runoff_sum`) and aggregated per MWS.

| Variable | Full form | Meaning | Units |
|---|---|---|---|
| $Q$ | surface runoff depth (per pixel, per day) | rainfall that flows off rather than infiltrating | mm |
| $0.2\,sr$ | initial abstraction $I_a = \lambda S$, $\lambda = 0.2$ | interception + surface storage before runoff begins | mm |
| $35, 52.5$ | AMC thresholds on $P_5$ | dry ≤ 35 mm < average ≤ 52.5 mm < wet | mm |

- **Use case**: runoff estimation for designing watershed storage capacities (check dams, ponds).
- **Location**: [computing/mws/run_off.py:410-433](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/run_off.py#L410-L433)

### 1K. Net Groundwater Recharge ($\Delta G$)

$$\Delta G = P - Q - ET$$

| Variable | Full form | Meaning | Units | Code name |
|---|---|---|---|---|
| $\Delta G$ | change (delta) in groundwater storage for the period | net water that percolates to the aquifer | mm | `g` (key `"DeltaG"`) |
| $P$ | precipitation (§1A) | — | mm | `p` (key `"Precipitation"`) |
| $Q$ | runoff (§1J) | — | mm | `q` (key `"RunOff"`) |
| $ET$ | evapotranspiration (§1B) | — | mm | `e` (key `"ET"`) |

- **Use case**: net groundwater replenishment per MWS per period — the headline hydrology output.
- **Location**: [computing/mws/delta_g.py:140-150](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/delta_g.py#L140-L150) (line 149: `g = p.subtract(q).subtract(e)`)

### 1L. Cumulative Groundwater Storage ($G$)

$$G_t = G_{t-1} + \Delta G_t, \qquad G_0 = 0$$

Sequential accumulation of $\Delta G$ across periods (fortnights/years), per MWS.

- **Use case**: running groundwater-storage trajectory; reveals long-term depletion or recovery.
- **Location**: [computing/mws/calculateG.py:77-88](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/calculateG.py#L77-L88) (`curr_prop["G"] = curr_prop["DeltaG"] + prev_g`)

### 1M. Weighted Average Aquifer Specific Yield ($S_y$)

$$S_y = \sum_{j=1}^{J} \frac{\text{Area}_{\text{intersection},j}}{\text{Area}_{\text{MWS}}} \times y_j$$

| Variable | Full form | Meaning | Units |
|---|---|---|---|
| $S_y$ | specific yield (area-weighted) | fraction of aquifer volume that drains/refills as the water table moves | fraction (0–1) |
| $\text{Area}_{\text{intersection},j}$ | area of overlap between the MWS polygon and CGWB aquifer polygon $j$ | — | m² |
| $y_j$ | yield fraction of aquifer $j$ (code `y_value`) | mapped from CGWB descriptive strings | fraction |
| $J$ | number of intersecting aquifer polygons | — | — |

CGWB string → fraction mapping (≈50 entries; representative set):
`Upto 1%`→0.01 · `Upto 1.5%`/`1-1.5%`→0.015 · `Upto 2%`/`1-2%`/`1.5-2%`→0.02 · `Upto 2.5%`/`1-2.5`→0.025 · `Upto 3%`/`2-3%`→0.03 · `Upto 3.5%`→0.035 · `Upto 4%`→0.04 · `Upto 5%`→0.05 · `6 - 8%`/`Upto 8%`→0.08 · `6 - 10%`/`8 - 10%`→0.10 · `6 - 12%`/`8 - 12%`→0.12 · `6 - 15%`/`8 - 15%`/`Upto 15%`→0.15 · `6 - 16%`/`8 - 16%`→0.16 · `8 - 18%`→0.18 · `8 - 20%`→0.20
(full table: [well_depth.py:71-110](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/well_depth.py#L71-L110))

- **Use case**: geological storage characteristics of the aquifer beneath each MWS.
- **Location**: [computing/mws/well_depth.py:121-139](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/well_depth.py#L121-L139)

### 1N. Predicted Well-Depth Fluctuation ($wd$)

$$wd = \frac{\Delta G}{S_y \times 1000}$$

| Variable | Full form | Meaning | Units |
|---|---|---|---|
| $wd$ | well-depth (water-table) fluctuation | vertical movement of the water table implied by the recharge | m |
| $1000$ | mm → m conversion | — | mm/m |

- **Use case**: translates recharge depth (mm of water column) into observable well-level change (m), comparable with field well readings.
- **Location**: [computing/mws/well_depth.py:158](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/well_depth.py#L158)

---

## 2. Composite Drought Assessment

Standardizes vegetation, moisture, and rainfall against historical baselines, then combines them into weekly drought classes and multi-year frequency/intensity.

### 2A. Vegetation Condition Index ($VCI$)

$$VCI_{\text{pixel}} = \min\left(\frac{NDVI - NDVI_{\min}}{NDVI_{\max} - NDVI_{\min}},\; \frac{NDWI - NDWI_{\min}}{NDWI_{\max} - NDWI_{\min}}\right) \times 100$$
$$VCI_{\text{MWS}} = \frac{\sum_{ROI} VCI_{\text{pixel}} \cdot \text{crop\_mask}}{\sum_{ROI} \text{crop\_mask}}$$

| Variable | Full form | Meaning | Units | Source |
|---|---|---|---|---|
| $VCI$ | Vegetation Condition Index | current greenness/wetness relative to historical extremes; 0 = worst ever, 100 = best ever | % | computed |
| $NDVI$ | Normalized Difference Vegetation Index | greenness $(NIR-Red)/(NIR+Red)$ | unitless | MODIS |
| $NDWI$ | Normalized Difference Water Index | canopy/soil wetness | unitless | MODIS |
| $NDVI_{\min/\max}$ | historical extremes per pixel, same 28-day calendar window, years 2000 → current | — | — | MODIS archive |
| $\text{crop\_mask}$ | cropping mask | 1 on cropped pixels, 0 elsewhere — VCI averaged over cropland only | binary | LULC |
| $ROI$ | Region Of Interest | the MWS polygon | — | — |

- **Use case**: crop-health monitoring relative to historical seasons.
- **Location**: [computing/drought/generate_layers.py:848-894](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/generate_layers.py#L848-L894) (min at L874, ×100 at L877; 28-day window L805-814)

### 2B. Moisture Adequacy Index ($MAI$) — uses MODIS ET/PET with the 0.1 scale

$$MAI = \frac{\sum_{ROI}\left(\sum_i ET_i \cdot w_i \cdot 0.1\right)\cdot \text{crop\_mask}}{\sum_{ROI}\left(\sum_i PET_i \cdot w_i \cdot 0.1\right)\cdot \text{crop\_mask}} \times 100$$

| Variable | Full form | Meaning | Units | Source |
|---|---|---|---|---|
| $MAI$ | Moisture Adequacy Index | how much of the crop's potential water demand is actually met | % | computed |
| $ET_i$ | actual evapotranspiration, MODIS 8-day composite $i$ | — | (scaled) kg/m² | MODIS ET product |
| $PET_i$ | Potential Evapotranspiration, 8-day composite $i$ | atmospheric water demand if water were unlimited | (scaled) kg/m² | MODIS |
| $w_i$ | weight | fraction of composite $i$'s 8-day span overlapping the 28-day window | 0–1 | computed (L896-928) |
| $0.1$ | MODIS scale factor | converts stored integers to physical units — **applies here, not in §1B FLDAS** | — | product spec |

- **Use case**: tracking crop water-supply adequacy inside sown areas.
- **Location**: [computing/drought/generate_layers.py:973-1007](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/generate_layers.py#L973-L1007) (`mai_ = et_/pet_*100` at L999-1002)

### 2C. Standardized Precipitation Index ($SPI$, 28-day ≈ SPI-1)

$$SPI = \frac{P_{28d} - \mu_{28d}}{\sigma_{28d}}$$

| Variable | Full form | Meaning | Units | Source |
|---|---|---|---|---|
| $SPI$ | Standardized Precipitation Index | rainfall anomaly in standard deviations | σ | computed |
| $P_{28d}$ | current 28-day precipitation total | — | mm | CHIRPS |
| $\mu_{28d}, \sigma_{28d}$ | long-term mean / standard deviation of the same 28-day calendar window, 1981 → previous year | — | mm | CHIRPS archive |

- **Use case**: meteorological rainfall-anomaly characterization.
- **Location**: [computing/drought/generate_layers.py:692-698](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/generate_layers.py#L692-L698)

### 2D. Weekly severity classes (drought causality input) — **⚠ as-coded**

From `getWeekVector` ([drought_causality.py:152-186](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/drought_causality.py#L152-L186)):

| Index | severe | moderate | mild | Note |
|---|---|---|---|---|
| $VCI$ | $60 < VCI \le 100$ | $40 < VCI \le 60$ | otherwise | **⚠** direction is inverted versus the usual "high VCI = healthy" convention — documented exactly as the code reads |
| $MAI$ | $MAI \le 25$ | $25 < MAI \le 50$ | $MAI > 50$ | low adequacy = severe (intuitive) |
| $CAS$ | $\le 33.3$ | $\le 50$ | $> 50$ | weekly check uses `kharif_cropped_sqkm`; the yearly mode (below) uses *percent* area with the same thresholds |

**CAS = Cropped Area Sown** (kharif-season sown area). Yearly mode ([L51-59](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/drought_causality.py#L51-L59)): `percent_of_area_cropped_kharif` ≤ 33.3 → class 3 (severe), ≤ 50 → 2 (moderate), else 1 (mild).

Yearly binning of weekly index values (mode over weeks): VCI bins $[-1, 40, 60, 100]$ → poor/fair/good; MAI bins $[-1, 25, 50, 100]$ → poor/fair/good; SPI bins $[-1000, -2, -1.5, -1, 0, 1, 1.5, 2, 1000]$ → extremelyDry / severelyDry / moderatelyDry / mildlyDry / mildlyWet / moderatelyWet / severelyWet / extremelyWet.

### 2E. Meteorological Drought trigger ($mD$)

$$mD = 1 \iff (\text{dryspell} = 1) \;\lor\; (\text{rainfall deviation} = \text{“scanty”}) \;\lor\; (SPI < -1.5)$$

| Variable | Full form | Meaning |
|---|---|---|
| $mD$ | meteorological drought occurrence (week) | rainfall-side precondition for any drought severity |
| dryspell | consecutive-dry-days flag for the week | binary |
| rainfall deviation | monthly departure category from normal rainfall ("scanty" = extreme deficit) | categorical |

- **Location**: [drought_causality.py:186-189](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/drought_causality.py#L186-L189)

### 2F. Drought causality paths

Given $mD = 1$ for a week, severity is attributed by which trigger fired and how the three impact indices align:

- **Severe drought** (all of VCI, MAI, CAS in class "severe"): path 1 if dryspell triggered, path 2 if rainfall-deviation, path 3 if SPI. Weekly path counters accumulate per year.
- **Moderate drought**: ~18 analogous paths over mixed (mild/moderate) VCI × severe/moderate MAI × CAS combinations.
- **Mild drought scores**: per-factor frequency × weight, normalized by the 6 kharif weeks:
  $$\text{score}_f = \frac{\text{count}_f}{6} \quad f \in \{vci, mai, cas, dryspell, \text{rf-deviation}, spi\}$$

- **Use case**: attributing *why* a drought week occurred (rainfall deficit vs crop stress vs soil-moisture), for program targeting.
- **Location**: [computing/drought/drought_causality.py:152-405](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/drought_causality.py#L152-L405)

### 2G. Drought frequency & intensity (multi-year)

Per MWS and severity threshold $k \in \{0,1,2,3\}$ (none/mild/moderate/severe): frequency = number of drought weeks at that severity per year; intensity = corresponding magnitude, both rounded to 2 decimals into `Frequency_*` / `Intensity_*` columns.

- **Location**: [computing/drought/drought_causality.py:337-360](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/drought_causality.py#L337-L360)

---

## 3. CLART Decision Matrix

**CLART = Composite Land Assessment and Restoration Tool** — assigns water-structure treatments from recharge potential + slope.

### 3A. Slope Percentage ($sp$)

$$sp = \tan\left(\frac{\theta \,\pi}{180}\right) \times 100$$

| Variable | Full form | Meaning | Units |
|---|---|---|---|
| $sp$ | slope percentage | rise over run × 100 | % |
| $\theta$ | slope angle from `ee.Terrain.slope(DEM)` | — | degrees |

- **Use case**: terrain steepness for treatment recommendations. (Note: §1E's runoff adjustment uses the *degree* value directly, not this percentage — this formula lives only in CLART.)
- **Location**: [computing/clart/clart.py:86-89](file:///home/snaveen/Desktop/core-stack-backend/computing/clart/clart.py#L86-L89)

### 3B. Recharge Potential ($rp$)

$$rp_{\text{raw}} = dd_{\text{score}} \times lin_{\text{score}} \times lith_{\text{score}}$$

| Component | Full form | Rule |
|---|---|---|
| $lin_{\text{score}}$ | lineament score (geological fracture lines that conduct water) | present → 10, absent → 1 ([clart.py:91-92](file:///home/snaveen/Desktop/core-stack-backend/computing/clart/clart.py#L91-L92)) |
| $dd_{\text{score}}$ | drainage-density score (normalized DD, §4) | $dd_{\text{norm}} \le 0.334 \to 1$ (low); $\le 0.667 \to 2$ (medium); $> 0.667 \to 3$ (high) ([clart.py:115-126](file:///home/snaveen/Desktop/core-stack-backend/computing/clart/clart.py#L115-L126)) |
| $lith_{\text{score}}$ | lithology score from **RIF (Recharge Infiltration Factor)** of the aquifer | $RIF < 10 \to 3$ (low permeability); $10 \le RIF \le 15 \to 2$; $RIF > 15 \to 1$ (high permeability) ([lithology.py:126-131](file:///home/snaveen/Desktop/core-stack-backend/computing/clart/lithology.py#L126-L131)) |

Raw product → potential class:
$$rp = \begin{cases} 1\ (\text{High}) & rp_{\text{raw}} \in \{1, 2, 10, 20, 30, 40, 60, 90\} \\ 2\ (\text{Medium}) & rp_{\text{raw}} \in \{3, 4\} \\ 3\ (\text{Low}) & rp_{\text{raw}} \in \{6, 9\} \\ 0 & \text{otherwise} \end{cases}$$

- **Use case**: sub-surface water-absorption potential map.
- **Location**: [computing/clart/clart.py:128-164](file:///home/snaveen/Desktop/core-stack-backend/computing/clart/clart.py#L128-L164)

### 3C. CLART Recommendation classes (treatment code $tc$ 1–5)

With $max\_sp$ = maximum slope percentage in the region:

| Class | Condition | Typical treatment |
|---|---|---|
| 1 | $rp = 1$ and $sp \in [0,\ 0.20 \cdot max\_sp]$ | high-recharge flat land — recharge structures |
| 2 | $rp = 2$ and $sp \in [0,\ 0.25 \cdot max\_sp]$ | medium recharge — farm ponds etc. |
| 3 | $rp = 3$ and $sp \in [0,\ 0.20 \cdot max\_sp]$ | low recharge, flat — surface storage |
| 4 | $rp \in \{1,2,3\}$ and $sp \in (0.25 \cdot max\_sp,\ 0.30 \cdot max\_sp]$ | moderate slopes — contour trenches |
| 5 | $rp \in \{1,2,3\}$ and $sp > 0.30 \cdot max\_sp$ | steep — gully plugs / area treatment |

- **Use case**: automated placement guidance for check dams, farm ponds, contour trenches, gully plugs.
- **Location**: [computing/clart/clart.py:174-215](file:///home/snaveen/Desktop/core-stack-backend/computing/clart/clart.py#L174-L215)

---

## 4. Drainage Density ($DD$) — pure-Python (geopandas) calculation

For each watershed, drainage lines are clipped to the polygon (in **EPSG:7755**, a projected CRS for India, so lengths are in meters), then:

$$DD = \sum_{o=1}^{11} \frac{L_o}{1000} \cdot f_o \cdot \frac{100}{A_{ha}/100}$$

| Variable | Full form | Meaning | Units |
|---|---|---|---|
| $DD$ | drainage density (weighted) | stream length per unit area, weighted by stream order | km / km² (scaled) |
| $L_o$ | total length of drainage lines of stream order $o$ inside the watershed | — | m (÷1000 → km) |
| $f_o$ | influence factor for order $o$ | $\frac{60}{385}, \frac{55}{385}, \frac{50}{385}, \frac{45}{385}, \frac{40}{385}, \frac{35}{385}, \frac{30}{385}, \frac{25}{385}, \frac{20}{385}, \frac{15}{385}, \frac{10}{385}$ for $o = 1..11$ (weights decline with order; 385 = 60+55+…+10... normalizing constant) | fraction |
| $A_{ha}$ | watershed area (attribute `area_in_ha`) | code divides by 100 → km² | ha |
| stream order | Strahler stream order (attribute `ORDER`) | 1 = headwater rivulet … 11 = main channel | — |

Outputs per watershed: `DD` (sum over orders), `DD_stream` (per-order dict), `str_len_km` (per-order lengths).

- **Use case**: input to CLART's $dd_{\text{score}}$ (§3B); proxy for how quickly the landscape sheds water.
- **Location**: [computing/clart/drainage_density.py:120-197](file:///home/snaveen/Desktop/core-stack-backend/computing/clart/drainage_density.py#L120-L197); rasterization of the `DD` attribute at 30 m: [computing/clart/rasterize_vector.py:9-53](file:///home/snaveen/Desktop/core-stack-backend/computing/clart/rasterize_vector.py#L9-L53) (resolution constant `0.000278°` ≈ 30 m)

---

## 5. Terrain Description (TPI & Landform Classification)

### 5A. Topographic Position Index ($TPI$), two scales

$$TPI = z - \bar{z}_{\text{annulus}} \qquad TPI_{\text{std}} = \frac{TPI - \mu_{TPI}}{\sigma_{TPI}} \times 100 + 0.5$$

| Variable | Full form | Meaning | Units |
|---|---|---|---|
| $TPI$ | Topographic Position Index | how much higher/lower a pixel is than its neighborhood; >0 ridge-like, <0 valley-like | m |
| $z$ | pixel elevation (DEM, 30 m) | — | m |
| $\bar{z}_{\text{annulus}}$ | focal mean over a ring (annulus) kernel | small scale: inner 5 px / outer 10 px; large scale: inner 62 px / outer 67 px | m |
| $TPI_{\text{std}}$ | standardized TPI | comparable across terrains | — |

### 5B. Dynamic classification limits

$$\text{factor} = \max\left(3 - \log_{10}(\sigma_{dem} + 1),\; 0.3\right), \qquad \text{limits} = \pm 100 \times \text{factor}$$

where $\sigma_{dem}$ = standard deviation of elevation in the region (flatter region → wider limits → less over-classification).

### 5C. 11-class landform rules ($TPI_s$ = small-scale std TPI, $TPI_l$ = large-scale, $L/R$ = left/right limit)

| Class | Condition | Landform |
|---|---|---|
| 1 | $TPI_s \le L$, $TPI_l \le L$ | Valley |
| 2 | $TPI_s \le L$, $L < TPI_l < R$ | Valley |
| 3 | $TPI_s \le L$, $TPI_l \ge R$ | Ridge |
| 4 | $L < TPI_s < R$, $TPI_l \le L$ | Valley |
| 5 | $L < TPI_s < R$, $L < TPI_l < R$, slope < 5° | Plain |
| 6 | $L < TPI_s < R$, $L < TPI_l < R$, 5° ≤ slope < 20° | Slopy |
| 7 | $L < TPI_s < R$, $TPI_l \ge R$, slope < 6° | Flat ridge top |
| 8 | $L < TPI_s < R$, ($L < TPI_l < R$, slope ≥ 20°) or ($TPI_l \ge R$, slope ≥ 6°) | Upper slope |
| 9 | $TPI_s \ge R$, $TPI_l \le L$ | Hilltop |
| 10 | $TPI_s \ge R$, $L < TPI_l < R$ | Hilltop |
| 11 | $TPI_s \ge R$, $TPI_l \ge R$ | Peak |

5-group rollup: Plains = {5}; Slopy = {6}; Steep slopes = {8}; Ridge = {3, 7, 10, 11}; Valleys = {1, 2, 4, 9}.

- **Use case**: terrain layer for planning + input to terrain clustering and LULC×terrain analysis.
- **Location**: [computing/terrain_descriptor/terrain_utils.py:5-161](file:///home/snaveen/Desktop/core-stack-backend/computing/terrain_descriptor/terrain_utils.py#L5-L161) (TPI L5-68, limits L69-78, classes L80-159); rollup: [terrain_clusters.py:223-231](file:///home/snaveen/Desktop/core-stack-backend/computing/terrain_descriptor/terrain_clusters.py#L223-L231)

### 5D. Terrain cluster assignment (pre-trained K-means, $k = 4$)

Feature vector per MWS: $[\text{slopy\%}, \text{plain\%}, \text{ridge\%}, \text{valley\%}, \text{hill-slopes\%}]$ (area fractions of the 5 groups). Assignment = nearest centroid by squared Euclidean distance:

$$\text{cluster} = \arg\min_c \sum_i (x_i - \mu_{c,i})^2$$

Fixed centroids (trained offline):
```
c0: [0.3626, 0.2104, 0.1216, 0.1739, 0.1315]   (mixed/undulating)
c1: [0.0917, 0.8430, 0.0352, 0.0217, 0.0083]   (plain-dominated)
c2: [0.0850, 0.0105, 0.2376, 0.3799, 0.2869]   (hilly/valley)
c3: [0.2230, 0.5612, 0.0851, 0.0731, 0.0575]   (mostly plain + slope)
```

- **Use case**: groups MWS by terrain regime so plans/benchmarks compare like-with-like.
- **Location**: [computing/terrain_descriptor/terrain_clusters.py:331-363](file:///home/snaveen/Desktop/core-stack-backend/computing/terrain_descriptor/terrain_clusters.py#L331-L363)

---

## 6. Cropping Intensity ($CI$)

Using LULC cropping classes (8 = single kharif, 9 = single non-kharif, 10 = double, 11 = triple):

$$\text{single} = A_8 + A_9, \qquad A_{\text{croppable}} = \bigcup_{\text{years}} (A_8 \cup A_9 \cup A_{10} \cup A_{11})$$
$$CI = 1 \cdot \frac{\text{single}}{A_{\text{croppable}}} + 2 \cdot \frac{A_{10}}{A_{\text{croppable}}} + 3 \cdot \frac{A_{11}}{A_{\text{croppable}}}$$

| Variable | Full form | Meaning |
|---|---|---|
| $CI$ | Cropping Intensity | average number of crop seasons per year on croppable land; 1.0 = everything single-cropped, 3.0 = everything triple-cropped |
| $A_k$ | area of LULC class $k$ (pixel count × pixel area) | code: `sngl_frac`, `dbl_frac`, `trpl_frac` for the three fractions |
| kharif / rabi / zaid | the three Indian agricultural seasons: monsoon (Jun–Oct) / winter (Nov–Mar) / summer (Apr–Jun) | — |

- **Use case**: agricultural-productivity indicator; drives CI-change detection (§10D).
- **Location**: [computing/cropping_intensity/cropping_intensity.py:150-331](file:///home/snaveen/Desktop/core-stack-backend/computing/cropping_intensity/cropping_intensity.py#L150-L331) (intensity at L313-319)

---

## 7. LULC Classes & Area Accounting

**LULC = Land Use / Land Cover** (CoRE Stack v3 classification, 10 m):

| Code | Class | Code | Class |
|---|---|---|---|
| 0 | Background | 7 | Barren lands |
| 1 | Built-up | 8 | Single-cropping cropland (kharif) |
| 2 | Water (kharif only) | 9 | Single non-kharif cropping |
| 3 | Water (kharif + rabi) | 10 | Double-cropping cropland |
| 4 | Water (kharif + rabi + zaid) | 11 | Triple-cropping cropland |
| 6 | Trees / Forest | 12 | Shrubs & scrub |

Area per class: $A_k = N_k \times a_{\text{pixel}}$ ($N_k$ = pixel count of class $k$; $a_{\text{pixel}}$ = 10 m × 10 m). Vector outputs carry per-class area attributes (`built-up_area_`, `k_water_area_`, `kr_water_area_`, `krz_water_area_`, `tree_forest_area_`, `barrenlands_area_`, `single_kharif_cropped_area_`, …).

- **Use case**: every theme that aggregates "how much of X" per MWS/village.
- **Location**: [computing/lulc/lulc_vector.py:106-150+](file:///home/snaveen/Desktop/core-stack-backend/computing/lulc/lulc_vector.py#L106-L150); class definitions implicit in [lulc_v3.py](file:///home/snaveen/Desktop/core-stack-backend/computing/lulc/lulc_v3.py)

---

## 8. Surface Water Bodies (SWB)

### 8A. Water presence & seasonality

$$\text{water} \iff 2 \le \text{LULC} \le 4$$

Seasonal persistence comes directly from the class (2 = kharif only, 3 = kharif+rabi, 4 = all three seasons). Percentages per water body:
$$\%_{\text{season}} = \frac{N_{\text{season pixels}}}{N_{\text{total water pixels}}} \times 100$$

- **Location**: [computing/surface_water_bodies/swb1.py:87-143](file:///home/snaveen/Desktop/core-stack-backend/computing/surface_water_bodies/swb1.py#L87-L143)

### 8B. Waterbody type classification

With a configurable buffer (default **500 m**) around each waterbody:

```
river  — if a river feature lies within the buffer
canal  — else if a canal feature lies within the buffer
individual — otherwise (standalone tank/pond)
```

- **Use case**: First census of water bodies; distinguishes riverine/canal-fed/standalone storage for rejuvenation planning.
- **Location**: [computing/surface_water_bodies/swb3.py:41-148](file:///home/snaveen/Desktop/core-stack-backend/computing/surface_water_bodies/swb3.py#L41-L148)

---

## 9. Tree Health

### 9A. Canopy masks

$$\text{tree\_mask} = (\text{LULC} = 6), \qquad \text{CCD}_{\text{masked}} = \text{CCD} \times \text{tree\_mask}, \qquad CH_{\text{masked}} = CH \times \text{tree\_mask}$$

| Variable | Full form | Meaning | Resolution |
|---|---|---|---|
| $CCD$ | Canopy Cover Density | fraction of ground covered by tree canopy | 25 m |
| $CH$ | Canopy Height | tree height raster | 25 m |

- **Location**: [computing/tree_health/ccd.py:78-106](file:///home/snaveen/Desktop/core-stack-backend/computing/tree_health/ccd.py#L78-L106), [canopy_height.py:78-106](file:///home/snaveen/Desktop/core-stack-backend/computing/tree_health/canopy_height.py#L78-L106)

### 9B. Overall tree-cover change (fusion with IndiaSAT)

Output codes (background −9999): `0` = no change (IndiaSAT agrees), `−2` = deforestation (IndiaSAT classes 2–5), `+2` = afforestation (IndiaSAT classes 2–5); inside agreed no-change areas the tree-change classes $\{-1, 1, 3, 4, 5\}$ pass through.

$$\text{no\_change} = (\text{aff} = 1), \quad \text{defo} = (2 \le \text{def} \le 5), \quad \text{affo} = (2 \le \text{aff} \le 5)$$

- **Use case**: reconciles CoRE Stack change detection with IndiaSAT before reporting tree-cover change.
- **Location**: [computing/tree_health/overall_change.py:62-177](file:///home/snaveen/Desktop/core-stack-backend/computing/tree_health/overall_change.py#L62-L177)

---

## 10. Change Detection (LULC transition matrices)

All four products follow the same pattern: **remap** the 13 LULC classes to a coarse scheme, take the per-pixel **mode** over the "then" years and the "now" years, then code specific **transitions**.

### 10A. Built-up change ([change_detection.py:119-158](file:///home/snaveen/Desktop/core-stack-backend/computing/change_detection/change_detection.py#L119-L158))

Remap: `1→1 (built-up), 2,3,4→2 (water), 6,8,9,10,11→3 (trees/crops), 7,12→4 (barren/shrub)`.
Transitions: BU→BU = 1, Water→BU = 2, Tree/Crop→BU = 3, Barren→BU = 4.

### 10B. Degradation ([L161-197](file:///home/snaveen/Desktop/core-stack-backend/computing/change_detection/change_detection.py#L161-L197))

Remap: `1→1, 2,3,4→2, 8,9,10,11→3 (cropland), 6→4 (forest), 7→5 (barren), 12→6 (shrub)`.
Transitions (forest fate): F→F = 1, F→Built-up = 2, F→Barren = 3, F→Shrub = 4.

### 10C. Deforestation / Afforestation ([L200-399](file:///home/snaveen/Desktop/core-stack-backend/computing/change_detection/change_detection.py#L200-L399))

Remap: `1→1, 2,3,4→2, 6→3 (forest), 8,9,10,11→4 (farm), 7→5 (barren), 12→6 (shrub)`.
Deforestation: forest → {built-up, farm, barren, …} (11 coded conditions); Afforestation: the reverse transitions. Key codes: F→F = 1, F→BU = 2, F→Farm = 3, F→Barren = 4.

### 10D. Cropping-intensity change ([L402-457](file:///home/snaveen/Desktop/core-stack-backend/computing/change_detection/change_detection.py#L402-L457))

Remap: `1→1, 2,3,4→2, 6→3, 7→4, 8,9→5 (single), 10→6 (double), 11→7 (triple), 12→8`.
Transition codes: Double→Single = 1, Triple→Single = 2, Triple→Double = 3, Single→Double = 4, Single→Triple = 5, Double→Triple = 6, Single→Single = 7, Double→Double = 8, Triple→Triple = 9. (1–3 = intensification loss; 4–6 = gain; 7–9 = stable.)

- **Use case**: where land is urbanizing, degrading, deforesting, or changing agricultural intensity — drives the change-detection layers served to planners.

---

## 11. LULC × Terrain Clustering (pre-trained K-means, AEZ-specific)

**AEZ = Agro-Ecological Zone**. Each MWS gets a land-use cluster computed separately for plains and slopes, by nearest-centroid (squared Euclidean distance, same metric as §5D) against centroids trained per AEZ (stored in `computing/lulc_X_terrain/utils.py`).

- **Plains** feature vector (7-dim): `[barren%, double_crop%, shrubs_scrubs%, single_crop%, single_non_kharif%, forest%, triple_crop%]` — [lulc_on_plain_cluster.py:179-305](file:///home/snaveen/Desktop/core-stack-backend/computing/lulc_X_terrain/lulc_on_plain_cluster.py#L179-L305)
- **Slopes** feature vector (3-dim): `[barren%, shrub_scrub%, forests%]` — [lulc_on_slope_cluster.py:130-251](file:///home/snaveen/Desktop/core-stack-backend/computing/lulc_X_terrain/lulc_on_slope_cluster.py#L130-L251)

- **Use case**: characterizes how land is used *given* its terrain, per agro-ecological context.

---

## 12. Geometric pipelines (no closed-form math)

| Pipeline | Operation | Location |
|---|---|---|
| MWS delineation | spatial intersection filter of the pan-India microwatershed dataset against the admin boundary | [computing/mws/mws.py:33-90](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/mws.py#L33-L90) |
| Drainage lines | `filterBounds` clip of the pan-India drainage dataset to the ROI | [computing/misc/drainage_lines.py:28-111](file:///home/snaveen/Desktop/core-stack-backend/computing/misc/drainage_lines.py#L28-L111) |
| Stream order stats | per-order area %: $(A_o / A_{total}) \times 100$ from masked `ee.Image.pixelArea()` | [computing/misc/stream_order.py:285-331](file:///home/snaveen/Desktop/core-stack-backend/computing/misc/stream_order.py#L285-L331) |

---

## 13. Resolution & convention summary

| Item | Value |
|---|---|
| LULC / SWB pixel | 10 m |
| Runoff/hydrology compute scale | 30 m (EPSG:4326) |
| CCD / canopy height | 25 m |
| DEM (SRTM / FABDEM) | 30 m |
| Drainage-density raster | 0.000278° ≈ 30 m |
| Length/area CRS (local calc) | EPSG:7755 (meters); results restored to EPSG:4326 |
| Hydro-year | July 1 → June 30 |
| Fortnight | 14 days |
| Drought window | 28 days |
| MODIS archive start | 2000 (VCI baselines) |
| CHIRPS archive start | 1981 (SPI baselines) |

## 14. Master variable glossary

| Symbol / code | Full form | First defined |
|---|---|---|
| $P$ | Precipitation (mm) | §1A |
| $ET$ / `Evap_tavg` | Evapotranspiration / FLDAS average evaporation rate | §1B |
| $PET$ | Potential Evapotranspiration | §2B |
| HSG / `soil` | Hydrologic Soil Group (A–D → 1–4) | §1C |
| $CN_2, CN_3, CN_{1a}, CN_{2a}, CN_{3a}$ | (SCS) Curve Numbers: base, wet, dry-adjusted, slope-adjusted, wet-adjusted | §1D–1F |
| AMC | Antecedent Moisture Condition (I dry / II average / III wet) | §1F |
| $sr_k$ / $S$ | potential maximum soil retention (mm) | §1G |
| $P_5$ / `antecedent` | antecedent (preceding-days) rainfall (mm) | §1H |
| $M_k$ | antecedent moisture amount (mm) | §1I |
| $Q$ / `runoff` | surface runoff depth (mm) | §1J |
| $\Delta G$ / `DeltaG` | net groundwater recharge for a period (mm) | §1K |
| $G$ | cumulative groundwater storage (mm) | §1L |
| $S_y$ / `y_value` | aquifer specific yield (fraction) | §1M |
| $wd$ | well-depth fluctuation (m) | §1N |
| $VCI$ | Vegetation Condition Index (%) | §2A |
| $NDVI$ / $NDWI$ | Normalized Difference Vegetation / Water Index | §2A |
| $MAI$ | Moisture Adequacy Index (%) | §2B |
| $SPI$ | Standardized Precipitation Index (σ) | §2C |
| CAS | Cropped Area Sown (kharif) | §2D |
| $mD$ | meteorological drought flag | §2E |
| $sp$, $max\_sp$ | slope percentage; regional max slope % | §3A |
| $rp$ | recharge potential class | §3B |
| RIF | Recharge Infiltration Factor (lithology permeability) | §3B |
| $tc$ | CLART treatment class (1–5) | §3C |
| $DD$, $f_o$, $L_o$ | drainage density; per-order influence factor; per-order stream length | §4 |
| TPI ($TPI_s$, $TPI_l$) | Topographic Position Index (small / large scale) | §5A |
| $CI$ | Cropping Intensity (1–3) | §6 |
| LULC | Land Use / Land Cover | §7 |
| kharif / rabi / zaid | Indian crop seasons: monsoon / winter / summer | §6 |
| CCD / CH | Canopy Cover Density / Canopy Height | §9A |
| AEZ | Agro-Ecological Zone | §11 |
| MWS | Microwatershed (core spatial unit) | §1 |
| ROI | Region Of Interest (the MWS/block polygon) | §2A |
