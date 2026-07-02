# Change Detection — Pipeline Spec & Known Bugs

> This page documents a **Pipeline correctness** bug class (see the [Bug taxonomy](Home)) for the change detection pipeline — i.e. the pipeline not doing what it should be doing.
>
> **Spec reminder:** change detection between years `i` and `j` should compute the modal LULC of years `(i, i+1, i+2)` and of years `(j-2, j-1, j)`, then compute a change matrix of the area under class `x` that has transitioned to class `y` for all pairs of classes.
>
> **Todo:** Write a specification of the pipeline in natural language. Ask Claude to create a dummy test LULC and a unit test on this LULC to check for correctness of the pipeline. Do this for all pipelines.

---

## How the Pipeline Works

The change detection pipeline takes a chronological stack of annual LULC (Land Use Land Cover) rasters and produces five transition rasters: **Urbanization**, **Degradation**, **Deforestation**, **Afforestation**, and **Crop Intensity**. Each output pixel holds an integer transition code (0 = no change) describing how that location changed between an early baseline period and a later active period.

The pipeline runs in three stages:

**Stage 1 — Build "then" and "now" windows.**
For each pixel, compute the modal (most frequent) LULC class across the first 3 years of the input stack (`then`) and across the last 3 years (`now`). These two values capture the stable land cover before and after the change window, suppressing single-year noise.

**Stage 2 — Remap classes.**
Before computing the mode, raw 12-class LULC values are collapsed into a smaller set of semantic categories specific to each sub-pipeline (e.g., Urbanization uses 4 categories: Built-up, Water, Tree/Crop, Barren/Scrub). Deforestation and Afforestation additionally apply a temporal smoothing pass to the raw stack before remapping (see Bug 2 below).

**Stage 3 — Assign transition codes.**
Each pixel's `(then, now)` class pair is looked up against a transition table. Only pixels that satisfy the sub-pipeline's trigger condition receive a non-zero code (e.g., Urbanization only fires when `now == Built-up`; Deforestation only fires when `then == Forest`). The result is exported as a single-band raster and later reduced to vector area statistics per watershed polygon.

For detailed specifications, worked examples, and output schemas for each sub-pipeline, see the [Pipeline Specifications](#pipeline-specifications) section below.

---

## Known Bugs

### Bug 1 — Wrong "now" window ("then and now" bug)

> 🐞 **Tracked in:** [core-stack-org/core-stack-backend#1039](https://github.com/core-stack-org/core-stack-backend/issues/1039)

**Location:** `computing/change_detection/change_detection.py` — lines 137, 177, 340–341, 417–418
**Affects:** All five sub-pipelines

The `now` window is constructed with `l1_asset_remapped[3:]` instead of `l1_asset_remapped[-3:]`.

Per the spec, `now` must always be the modal LULC of the **last 3 years** `(end_year-2, end_year-1, end_year)`. With the current slice `[3:]`, when the input stack has exactly 6 years the two slices happen to be identical and the bug is silent. For any stack with 7 or more years, `[3:]` includes all years from index 3 to the end — potentially 4, 5, or more years — causing the modal computation to draw from intermediate years that should have no influence on `now`.

**Example:** An 8-year stack where years 3–4 are class 1 (built-up) and years 5–7 are class 6 (forest). The correct `now` modal is 6 (last 3 years). The buggy `now` modal is 1 (mode of the 5-element slice dominated by class 1).

**Fix:** Replace `[3:]` with `[-3:]` in the four affected locations listed above.

For the full hypothesis and a concrete test specification, see [Issue #1039](https://github.com/core-stack-org/core-stack-backend/issues/1039).

---

### Bug 2 — Temporal smoothing targets raw class 3 (Water K+R) unexpectedly

> 🐞 **Tracked in:** [core-stack-org/core-stack-backend#1040](https://github.com/core-stack-org/core-stack-backend/issues/1040)

**Location:** `computing/change_detection/change_detection.py` — lines 302–325
**Affects:** Deforestation and Afforestation sub-pipelines only

Before the modal computation, Deforestation and Afforestation apply a two-pass smoothing step to the **raw** LULC stack. Pass A counts "anomaly" events at each pixel by checking 11 pattern conditions across every interior year (e.g., tree–crop–tree, built-up–tree–built-up). Pass B then applies corrections — but only to pixels whose value is **3**, and only when the Pass A anomaly count is exactly 3 or 4:

```python
cond1 = before.eq(3).And(middle.neq(3)).And(after.eq(3)).And(zero_image.eq(3).Or(zero_image.eq(4)))
cond2 = before.neq(3).And(middle.eq(3)).And(after.neq(3)).And(zero_image.eq(3).Or(zero_image.eq(4)))
middle = middle.where(cond1, 3)
middle = middle.where(cond2, before)
```

- **cond1:** `before == 3 AND middle ≠ 3 AND after == 3` and anomaly count ∈ {3,4} → set `middle = 3`
- **cond2:** `before ≠ 3 AND middle == 3 AND after ≠ 3` and anomaly count ∈ {3,4} → set `middle = before`

The smoothing runs on the raw stack **before** remapping. At that point class `3` is the raw LULC label for **Water (Kharif + Rabi)** — not Forest. Forest is raw class **6**. So a smoothing step that reads as "stabilise a flickering forest pixel" is in fact rewriting **water** pixels: it fills a non-3 dip between two water years back to water (cond1), and erases a lone water spike between two non-water years (cond2). Forest flicker — the thing deforestation/afforestation actually cares about — is never smoothed by these two conditions.

**Why it slips through:** the 11 anomaly conditions in Pass A are written in raw class codes, so a reader can easily assume Pass B's hardcoded `3` is also "the forest class," when raw `3` is Water. The effect is silent — no error is raised, the output just reflects water-based corrections instead of the intended forest-based ones.

For the full hypothesis and a concrete test specification, see [Issue #1040](https://github.com/core-stack-org/core-stack-backend/issues/1040).

---

### Flow diagram (errors marked)

Red text and ⭐ markers in the diagram below indicate the locations of the bugs in `change_detection.py`.

<img width="3890" height="7649" alt="changedetection-bug" src="https://github.com/user-attachments/assets/7aa605a9-31fa-490e-b5d9-94fcfbebc2f8" />

---

# Pipeline Specifications

Natural-language specifications for every pipeline that needs correctness unit tests.
Each section defines: inputs, what computation is performed step-by-step, outputs, and
invariants that any correct implementation must satisfy.

---

## End-to-End Worked Example: Built-up (Urbanization)

This example traces a single MWS polygon containing **6 pixels** through the
full pipeline — from the raw LULC stack to the final vector area attributes.
Each pixel is 10 m × 10 m = **100 m²**.

Year range: **2018 → 2023** (6 years, so then = 2018–2020, now = 2021–2023).

---

### Stage 1 — Raw LULC stack (input)

| Pixel | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | Story |
|---|---|---|---|---|---|---|---|
| A | 6 | 6 | 6 | 1 | 1 | 1 | Forest throughout early years, built-up in late years |
| B | 2 | 3 | 2 | 1 | 1 | 1 | Water (varying types) early, built-up late |
| C | 7 | 7 | 7 | 1 | 1 | 1 | Barrenland early, built-up late |
| D | 1 | 1 | 1 | 1 | 1 | 1 | Built-up throughout all 6 years |
| E | 12 | 12 | 12 | 1 | 1 | 1 | Scrub early, built-up late |
| F | 8 | 8 | 8 | 6 | 6 | 6 | Cropland early, forest late — no urbanization |

---

### Stage 2 — Class remapping (urbanization remap table)

The raw 12-class LULC is collapsed to 4 categories before computing the mode.

| Raw class | Urbanization class | Label |
|---|---|---|
| 1 | 1 | Built-up |
| 2, 3, 4 | 2 | Water |
| 6, 8, 9, 10, 11 | 3 | Tree / Cropland |
| 7, 12 | 4 | Barren / Scrub |

Applying this to every pixel and every year:

| Pixel | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|---|
| A | 3 | 3 | 3 | 1 | 1 | 1 |
| B | 2 | 2 | 2 | 1 | 1 | 1 |
| C | 4 | 4 | 4 | 1 | 1 | 1 |
| D | 1 | 1 | 1 | 1 | 1 | 1 |
| E | 4 | 4 | 4 | 1 | 1 | 1 |
| F | 3 | 3 | 3 | 3 | 3 | 3 |

---

### Stage 3 — Modal aggregation (then / now windows)

For each pixel, compute the most frequent class across the first 3 years (then)
and across the last 3 years (now).

| Pixel | then = mode(2018,2019,2020) | now = mode(2021,2022,2023) |
|---|---|---|
| A | 3 | 1 |
| B | 2 | 1 |
| C | 4 | 1 |
| D | 1 | 1 |
| E | 4 | 1 |
| F | 3 | 3 |

Note on Pixel B: raw years are 2, 3, 2 (three different water sub-classes).
After remapping all three become class 2, so the mode is unambiguously 2.

---

### Stage 4 — Transition code assignment (change detection raster output)

Rule: a non-zero code is only assigned when `now == 1` (pixel became built-up).

| Code | Condition | Label |
|---|---|---|
| 1 | then=1, now=1 | bu_bu (was already built-up) |
| 2 | then=2, now=1 | w_bu (water → built-up) |
| 3 | then=3, now=1 | tr_bu (tree/crop → built-up) |
| 4 | then=4, now=1 | b_bu (barren/scrub → built-up) |
| 0 | now ≠ 1 | no urbanization |

Applying to each pixel:

| Pixel | then | now | Code | Label |
|---|---|---|---|---|
| A | 3 | 1 | **3** | tr_bu |
| B | 2 | 1 | **2** | w_bu |
| C | 4 | 1 | **4** | b_bu |
| D | 1 | 1 | **1** | bu_bu |
| E | 4 | 1 | **4** | b_bu |
| F | 3 | 3 | **0** | (none — crop did not urbanize) |

This is the raster that `built_up()` exports to GEE. Visualised on the 6-pixel
grid (0 = no urbanization):

```
[ 3  2 ]
[ 4  1 ]
[ 4  0 ]
```

---

### Stage 5 — Vector reduction (change detection vector output)

`generate_vector` reads the raster above and, for each MWS polygon, sums the
pixel area (m²) covered by each transition code, then converts to hectares.

Each pixel = 100 m² = 0.01 ha.

**Individual transition areas:**

| Label | Matching pixels | Pixel count | Area (m²) | Area (ha) |
|---|---|---|---|---|
| bu_bu | D | 1 | 100 | **0.01** |
| w_bu | B | 1 | 100 | **0.01** |
| tr_bu | A | 1 | 100 | **0.01** |
| b_bu | C, E | 2 | 200 | **0.02** |

**Total urbanization (codes 2, 3, 4 combined — excludes bu_bu):**

Pixels B, A, C, E all have a code ∈ {2, 3, 4} → 4 pixels → 400 m²

| Label | Area (ha) |
|---|---|
| total_urb | **0.04** |

Note: `total_urb` is computed by masking all pixels with code ∈ {2,3,4} in a
single pass — it is NOT `w_bu + tr_bu + b_bu` added arithmetically. Here the
result is the same (0.01 + 0.01 + 0.02 = 0.04) because no pixel holds more than
one code — which is always true since each pixel has exactly one then/now pair.

**Final vector feature (the MWS polygon record written to GeoServer):**

```json
{
  "type": "Feature",
  "properties": {
    "uid": "MWS_001",
    "bu_bu":      0.01,
    "w_bu":       0.01,
    "tr_bu":      0.01,
    "b_bu":       0.02,
    "total_urb":  0.04
  }
}
```

---

### What Pixel F tells us

Pixel F was cropland (class 8) in 2018–2020 and forest (class 6) in 2021–2023.
After remapping, both classes map to **3 (Tree/Cropland)**, so `then=3, now=3`.
Since `now ≠ 1`, no urbanization code fires and it gets code **0**.

This pixel produces zero area contribution to every urbanization label — it is
correctly excluded. It would show up in the deforestation/afforestation pipeline
instead.

---

## LULC Class Reference

| Class | Label |
|---|---|
| 0 | Background / No data |
| 1 | Built-up |
| 2 | Water (Kharif only) |
| 3 | Water (Kharif + Rabi) |
| 4 | Water (Kharif + Rabi + Zaid) |
| 6 | Tree / Forest |
| 7 | Barrenland |
| 8 | Single-crop (Kharif) |
| 9 | Single-crop (non-Kharif) |
| 10 | Double-crop |
| 11 | Triple-crop |
| 12 | Shrub / Scrub |

Class 5 does not appear as a raw LULC label; it is only produced as an intermediate
value inside remapping steps. Every raw LULC image must contain only values from
{0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12}.

---

## 1. Change Detection Pipeline

**File:** `computing/change_detection/change_detection.py`

### Inputs

| Parameter | Type | Meaning |
|---|---|---|
| `l1_asset` | list of rasters, length N | Annual LULC images ordered chronologically: index 0 = `start_year`, index N-1 = `end_year`. N must be ≥ 6. |
| `start_year` | int | First year of the analysis window |
| `end_year` | int | Last year of the analysis window |
| `roi_boundary` | geometry | Region of interest; all outputs are clipped to this |

### Step 1 — Build "then" and "now" windows

- **then** = modal LULC of years `(start_year, start_year+1, start_year+2)` = `l1_asset[:3]`
- **now** = modal LULC of years `(end_year-2, end_year-1, end_year)` = `l1_asset[-3:]`

The mode is computed pixel-wise: each pixel in the output takes the most frequent value
across the three input images at that pixel location.

> **Known bug:** The current code uses `l1_asset[3:]` for `now`. This is only correct
> when N == 6. For any other N the `now` window contains the wrong set of years.
> Fix: change `l1_asset[3:]` → `l1_asset[-3:]` in all five sub-functions.
> See [Issue #1039](https://github.com/core-stack-org/core-stack-backend/issues/1039).

### Step 2 — Remap classes

Each sub-pipeline remaps the full 12-class LULC into a smaller set of categories before
computing transitions. Unmapped source pixels (class 5, or any unrecognised value) are
set to 0. Remapping is applied to every image before the modal computation.

### Step 3 — Compute transition raster

For every pixel, look at its `(then_class, now_class)` pair and assign an integer output
code. Pixels that do not satisfy any defined transition condition output 0.

### Invariant

For any sub-pipeline, every output pixel must be one of the valid transition codes listed
below, or 0 (no transition / out-of-scope class). Values outside this set indicate a
computation error.

---

### 1a. Urbanization (`built_up`)

**Remapping (applied before modal computation):**

| Source classes | Mapped to | Semantic label |
|---|---|---|
| 1 | 1 | Built-up |
| 2, 3, 4 | 2 | Water |
| 6, 8, 9, 10, 11 | 3 | Tree / Cropland |
| 7, 12 | 4 | Barren / Scrub |

**Output transition codes** (only pixels where `now == 1` produce non-zero output):

| Code | Transition | Meaning |
|---|---|---|
| 1 | built-up → built-up | Stayed built-up |
| 2 | water → built-up | Water converted to built-up |
| 3 | tree/crop → built-up | Vegetation converted to built-up |
| 4 | barren/scrub → built-up | Barren land converted to built-up |

---

### 1b. Degradation (`change_degradation`)

**Remapping:**

| Source classes | Mapped to | Semantic label |
|---|---|---|
| 1 | 1 | Built-up |
| 2, 3, 4 | 2 | Water |
| 8, 9, 10, 11 | 3 | Cropland |
| 6 | 4 | Tree / Forest |
| 7 | 5 | Barren |
| 12 | 6 | Scrub |

**Output transition codes** (only pixels where `then == 4` produce non-zero output):

| Code | Transition | Meaning |
|---|---|---|
| 1 | forest → forest | No degradation |
| 2 | forest → built-up | Forest converted to built-up |
| 3 | forest → barren | Forest degraded to barren |
| 4 | forest → scrub | Forest degraded to scrub |

---

### 1c. Deforestation (`change_deforestation`) and 1d. Afforestation (`change_afforestation`)

Both sub-pipelines share a preparatory smoothing step (`change_deforestation_afforestation`)
before the modal computation.

#### Pre-processing smoothing

**Pass A — Anomaly count:**
Iterate over every interior year `i` (1 to N-2) with the triplet `(before, middle, after)`.
For each of the 11 anomaly conditions below, add 1 to `zero_image` at every pixel that
matches that condition. A pixel's final count reflects how many (year, condition) pairs
fired at that pixel across the whole time series.

| # | Condition name | before | middle | after |
|---|---|---|---|---|
| 1 | shrubs-green-shrubs | 12 | ∈{6,8,9,10,11} | 12 |
| 2 | water-green-water | ∈{2,3,4} | ∈{6,8,9,10,11} | ∈{2,3,4} |
| 3 | tree-shrub-tree | 6 | 12 | 6 |
| 4 | crop-shrub-crop | ∈{8,9,10,11} | 12 | ∈{8,9,10,11} |
| 5 | crop-barren-crop | ∈{8,9,10,11} | 7 | ∈{8,9,10,11} |
| 6 | tree-farm-tree | 6 | ∈{8,9,10,11} | 6 |
| 7 | farm-tree-farm | ∈{8,9,10,11} | 6 | ∈{8,9,10,11} |
| 8 | BU-tree-BU | 1 | 6 | 1 |
| 9 | tree-BU-tree | 6 | 1 | 6 |
| 10 | BU-farm-BU | 1 | ∈{8,9,10,11} | 1 |
| 11 | barren-green-barren | 7 | ∈{6,8,9,10,11} | 7 |

**Pass B — Correction using anomaly count:**
Iterate over every interior year `i` again. Apply two corrections per pixel if the anomaly
count at that pixel is ∈ {3, 4}:

```python
cond1 = before.eq(3).And(middle.neq(3)).And(after.eq(3)).And(zero_image.eq(3).Or(zero_image.eq(4)))
cond2 = before.neq(3).And(middle.eq(3)).And(after.neq(3)).And(zero_image.eq(3).Or(zero_image.eq(4)))
middle = middle.where(cond1, 3)
middle = middle.where(cond2, before)
```

Here class `3` is the **raw** LULC class for "Water (Kharif+Rabi)", not a remapped class.

- **cond1:** `before == 3 (Water-K+R) AND middle ≠ 3 AND after == 3 (Water-K+R)` AND anomaly count ∈ {3,4} → set `middle = 3`
- **cond2:** `before ≠ 3 AND middle == 3 (Water-K+R) AND after ≠ 3` AND anomaly count ∈ {3,4} → set `middle = before`

> **Known bug:** these conditions hardcode raw class `3` (Water K+R) where forest (raw class `6`)
> was intended, so the smoothing corrects water pixels rather than forest flicker.
> See [Issue #1040](https://github.com/core-stack-org/core-stack-backend/issues/1040).

**Remapping (applied after smoothing):**

| Source classes | Mapped to | Semantic label |
|---|---|---|
| 1 | 1 | Built-up |
| 2, 3, 4 | 2 | Water |
| 6 | 3 | Tree / Forest |
| 8, 9, 10, 11 | 4 | Farmland |
| 7 | 5 | Barren |
| 12 | 6 | Scrub |

#### Deforestation output codes (only pixels where `then == 3`):

| Code | Transition | Meaning |
|---|---|---|
| 1 | forest → forest | No deforestation |
| 2 | forest → built-up | Deforested to built-up |
| 3 | forest → farmland | Deforested to farmland |
| 4 | forest → barren | Deforested to barren |
| 5 | forest → scrub | Deforested to scrub |

#### Afforestation output codes (only pixels where `now == 3`):

| Code | Transition | Meaning |
|---|---|---|
| 1 | forest → forest | No change |
| 2 | built-up → forest | Built-up afforested |
| 3 | farmland → forest | Farmland afforested |
| 4 | barren → forest | Barren afforested |
| 5 | scrub → forest | Scrub afforested |

---

### 1e. Crop Intensity (`change_cropping_intensity`)

**Remapping:**

| Source classes | Mapped to | Semantic label |
|---|---|---|
| 1 | 1 | Built-up |
| 2, 3, 4 | 2 | Water |
| 6 | 3 | Tree |
| 7 | 4 | Barren |
| 8, 9 | 5 | Single-crop (SI) |
| 10 | 6 | Double-crop (DO) |
| 11 | 7 | Triple-crop (TR) |
| 12 | 8 | Shrub |

**Output transition codes** (all transitions between crop classes; non-crop pixels output 0):

| Code | Transition | Meaning |
|---|---|---|
| 1 | DO → SI | Intensification decreased |
| 2 | TR → SI | Intensification decreased sharply |
| 3 | TR → DO | Intensification decreased |
| 4 | SI → DO | Intensification increased |
| 5 | SI → TR | Intensification increased sharply |
| 6 | DO → TR | Intensification increased |
| 7 | SI → SI | No change (single) |
| 8 | DO → DO | No change (double) |
| 9 | TR → TR | No change (triple) |

---

## 2. Temporal Correction Pipeline

**File:** `computing/lulc/utils/temporal_correction.py`

### Inputs

| Parameter | Type | Meaning |
|---|---|---|
| `l1_asset_new` | list of rasters, length N | Annual LULC images, ordered chronologically. Background pixels are class 0. |
| `crop_freq_array` | list of rasters, length N | Cropping-frequency classification for each year; used as the replacement value in certain corrections |

### Phase 1 — Background fill

Background pixels (class 0) are filled using valid neighbors. Processing order matters:
intermediate years first, then boundaries.

**Intermediate years (i from 1 to N-2):**
- If `before ≥ 1 AND after ≥ 1 AND middle == 0`:
  - When `i == 1`: fill `middle` with `after`
  - Otherwise: fill `middle` with `before`

**First year (i = 0):**
- If `year[0] == 0 AND year[1] ≥ 1`: fill `year[0]` with `year[1]`

**Last year (i = N-1):**
- If `year[N-1] == 0 AND year[N-2] ≥ 1`: fill `year[N-1]` with `year[N-2]`

### Phase 2 — Anomaly count

For every interior year `i`, count how many of the 11 anomaly conditions (same table as
Section 1c above) fire at each pixel. Sum these counts into `zero_image`.

### Phase 3 — Correction (anomaly count == 1)

For every interior year `i`, apply the 11 correction rules (see `process_conditions`) to
pixels where `zero_image == 1`.

Correction targets per condition:

| Condition | Correction applied to `middle` |
|---|---|
| cond1 (shrubs-green-shrubs) | → 12 (shrub) |
| cond2 (water-green-water) | → 7 (barren) |
| cond3 (tree-shrub-tree) | → 6 (tree) |
| cond4 (crop-shrub-crop) | → `crop_freq_array[i]` |
| cond5 (crop-barren-crop) | → `crop_freq_array[i]` |
| cond6 (tree-farm-tree) | → 6 (tree) |
| cond7 (farm-tree-farm) | → `crop_freq_array[i]` |
| cond8 (BU-tree-BU) | → 1 (built-up), only if `year[i-2] == 1 AND year[i+2] == 1` |
| cond9 (tree-BU-tree) | → 6 (tree) |
| cond10 (BU-farm-BU) | before, middle, after all → `crop_freq_array` |
| cond11 (barren-green-barren) | → 7 (barren) |

Exception: when `i == 2`, cond1-cond7, cond9-cond11 are skipped (only cond8 applies at that position).

### Phase 4 — Correction (anomaly count ≥ 2)

Same 11 rules, applied twice:
- First pass: i from 1 to N-3 (excludes last interior year)
- Second pass: i from 1 to N-2 (full interior range), catches any remaining anomalies

### Phase 5 — First-year corrections

Two conditions applied only to `year[0]`:

| Condition | Rule | Correction |
|---|---|---|
| BU-farm-farm | year[0]==1 AND year[1]∈{8-11} AND year[2]∈{8-11} | year[0] → `crop_freq_array[0]` |
| BU-tree-tree | year[0]==1 AND year[1]==6 AND year[2]==6 | year[0] → 6 |

### Output

The same list `l1_asset_new`, length N, with corrected values written in-place for each
year. Each corrected image is exported to GEE as the final LULC for that year.

### Invariants

- No output pixel should be class 0 unless it was 0 in all N input images for that pixel.
- Background fill must not propagate class 0 into valid pixels.
- A pixel corrected by a crop-type condition must receive a value from {8, 9, 10, 11},
  never any other class.

---

## 3. Cropping Frequency Pipeline

**File:** `computing/lulc/cropping_frequency.py`

### Purpose

Classifies each pixel as single-crop (8 or 9), double-crop (10), or triple-crop (11) by
measuring the number of NDVI peaks (crop cycles) visible in the Landsat/Sentinel-2 time
series over a growing year.

### Inputs

| Parameter | Meaning |
|---|---|
| `roi_boundary` | Region of interest geometry |
| `startDate` | Season start, format "YYYY-MM-DD" (typically July 1) |
| `endDate` | Season end, format "YYYY-MM-DD" (typically June 30 next year) |

### Processing steps

1. **Fetch imagery:** Collect Landsat-7, Landsat-8, and Sentinel-2 TOA imagery within the
   date range and ROI. Apply cloud masks.

2. **Sensor harmonisation:** Calibrate Landsat-8 and Sentinel-2 reflectances to the
   Landsat-7 ETM+ scale using the Chastain regression coefficients.

3. **NDVI time series:** Compute NDVI for each image. Resample to a 16-day composite grid
   spanning `startDate` to `endDate`.

4. **Gap-fill:** Pair Landsat/Sentinel NDVI with MODIS NDVI using a linear regression
   model. Fill missing Landsat/Sentinel pixels using the MODIS-fitted values.

5. **Interpolation:** Use a ±120-day temporal join to linearly interpolate any remaining
   gaps in the NDVI time series.

6. **Classification:** Compare each pixel's NDVI time series against 12 cluster centroids
   from the pan-India L3 LULC cluster dataset (cluster 12 is excluded). Assign the class
   of the nearest centroid by squared Euclidean distance.

### Output

A single-band raster where each pixel holds one LULC class value from {1, 2, 3, 4, 6, 7,
8, 9, 10, 11} (cluster 12 is excluded from classification).

### Invariants

- Class 12 (invalid cluster) must never appear in the output.
- The nearest-centroid classification must be deterministic: equal distances must be
  broken consistently (in practice ties are rare but must not crash the pipeline).
- Output pixel values must all belong to the set of valid cluster class labels.

---

## 4. Deforestation/Afforestation Anomaly Count (shared helper)

This is the `change_deforestation_afforestation` function, used by both sub-pipelines.
It is specified here separately because it contains the most complex logic and is the most
likely to have hidden bugs.

### Invariant on anomaly count

For any interior year `i` and any pixel `p`, the anomaly conditions are mutually
non-exclusive: a single triplet can satisfy more than one condition simultaneously, so the
count at pixel `p` may be 0-11 for a single triplet, and can accumulate further across
multiple years. The count is purely additive; it does not represent the number of distinct
anomaly types, only the total number of condition-year firings.

### Invariant on correction cond1 and cond2 in Pass B

After Pass B, for any pixel `p` in interior year `i`:
- If `before[p] == 3 AND after[p] == 3`: it is NOT valid for `middle[p]` to be any
  value other than 3 when the anomaly count is ∈ {3, 4}. (cond1 must have fired.)
- If `middle[p] == 3 AND before[p] ≠ 3 AND after[p] ≠ 3`: it is NOT valid for
  `middle[p]` to still be 3 when the anomaly count is ∈ {3, 4}. (cond2 must have fired.)

These two invariants are the primary assertions for the smoothing unit test.

---

## 5. Change Detection Vector Pipeline

**File:** `computing/change_detection/change_detection_vector.py`

### Purpose

Converts the five change detection **rasters** (output of pipeline 1) into
**vector feature collections**. Each MWS polygon in the ROI receives one area
attribute per transition type, in hectares. This pipeline is a pure post-processing
step — it reads raster outputs and produces statistics, it does not recompute any
change logic.

### Dependency

This pipeline must run **after** `get_change_detection` has completed and all
five raster assets exist in GEE. It reads them by asset path; if a raster is
missing the pipeline will fail silently (GEE will return an empty image).

### Inputs

| Parameter | Type | Meaning |
|---|---|---|
| `state`, `district`, `block` | strings | Location identifiers used to construct GEE asset paths |
| `start_year`, `end_year` | int | Same year range used to generate the source rasters |
| `roi` | `ee.FeatureCollection` | MWS boundary polygons — one feature per watershed unit |

### Core computation (`generate_vector`)

This function is called once per sub-pipeline and executes the same steps for
each:

**Step 1 — Load raster**
Read the change detection raster for the given layer name and year range from GEE.
The raster has a single band named `"constant"` containing integer transition codes.

**Step 2 — For each transition entry in `args`:**

- **Single code** (`value` is an integer): mask = pixels where `raster == value`
- **Multi code** (`value` is a list): mask = pixels where `raster ∈ value`
  (logical OR across all listed codes — used for "total" summary attributes)

**Step 3 — Compute area**
Apply `ee.Image.pixelArea()` (returns m² per pixel) and mask it to the selected
pixels.

**Step 4 — Reduce to polygons**
Use `ee.Reducer.sum()` at 10 m scale to sum the pixel areas within each MWS
polygon. Result: total m² of matching pixels per polygon.

**Step 5 — Unit conversion**
Multiply the summed area by `0.0001` to convert from m² to hectares
(1 hectare = 10,000 m²).

**Step 6 — Store and clean**
Set the result as a named property on each feature using `arg["label"]`.
Remove the intermediate `"sum"` property so it does not appear in the output.

Steps 2–6 repeat for every entry in `args`, accumulating attributes on the same
feature collection.

**Step 7 — Export**
Export the enriched feature collection as a GEE vector asset.

### Output attribute schema

Each output feature collection is an `ee.FeatureCollection` where every feature
is one MWS polygon carrying all original ROI properties plus the area attributes
listed below. All area values are in **hectares (ha)**.

#### 5a. Afforestation vector

| Attribute | Raster codes | Meaning |
|---|---|---|
| `fo_fo` | 1 | Forest stayed forest (no change) |
| `bu_fo` | 2 | Built-up converted to forest |
| `fa_fo` | 3 | Farmland converted to forest |
| `ba_fo` | 4 | Barren converted to forest |
| `sc_fo` | 5 | Scrub converted to forest |
| `total_aff` | 2,3,4,5 | Any new afforestation (union of all active transitions) |

#### 5b. Deforestation vector

| Attribute | Raster codes | Meaning |
|---|---|---|
| `fo_fo` | 1 | Forest stayed forest (no change) |
| `fo_bu` | 2 | Forest converted to built-up |
| `fo_fa` | 3 | Forest converted to farmland |
| `fo_ba` | 4 | Forest converted to barren |
| `fo_sc` | 5 | Forest converted to scrub |
| `total_def` | 2,3,4,5 | Any new deforestation (union of all active transitions) |

#### 5c. Degradation vector

| Attribute | Raster codes | Meaning |
|---|---|---|
| `f_f` | 1 | Forest stayed forest (no change) |
| `f_bu` | 2 | Forest converted to built-up |
| `f_ba` | 3 | Forest converted to barren |
| `f_sc` | 4 | Forest converted to scrub |
| `total_deg` | 2,3,4 | Any degradation (union of all active transitions) |

#### 5d. Urbanization vector

| Attribute | Raster codes | Meaning |
|---|---|---|
| `bu_bu` | 1 | Built-up stayed built-up (no change) |
| `w_bu` | 2 | Water converted to built-up |
| `tr_bu` | 3 | Tree/crop converted to built-up |
| `b_bu` | 4 | Barren/scrub converted to built-up |
| `total_urb` | 2,3,4 | Any new urbanization (union of all active transitions) |

#### 5e. Crop intensity vector

| Attribute | Raster codes | Meaning |
|---|---|---|
| `do_si` | 1 | Double-crop → Single-crop (intensification decreased) |
| `tr_si` | 2 | Triple-crop → Single-crop (intensification decreased sharply) |
| `tr_do` | 3 | Triple-crop → Double-crop (intensification decreased) |
| `si_do` | 4 | Single-crop → Double-crop (intensification increased) |
| `si_tr` | 5 | Single-crop → Triple-crop (intensification increased sharply) |
| `do_tr` | 6 | Double-crop → Triple-crop (intensification increased) |
| `si_si` | 7 | Single-crop unchanged |
| `do_do` | 8 | Double-crop unchanged |
| `tr_tr` | 9 | Triple-crop unchanged |
| `total_change` | 1,2,3,4,5,6 | Any crop intensity change (excludes no-change codes 7,8,9) |

### Invariants

1. **Area conservation:** For any sub-pipeline, the sum of all individual
   transition areas must equal the total area of the source raster's non-zero
   pixels within that polygon. Formally:
   `sum(individual transition attributes) == total_* attribute` for each polygon.

2. **Non-negative areas:** Every area attribute must be ≥ 0 for every polygon.
   A negative value indicates a reducer or unit conversion error.

3. **Total is a union, not a sum:** `total_*` is computed by masking pixels
   that match ANY of the listed codes in a single pass. It is NOT the arithmetic
   sum of individual attributes. Therefore `total_* ≤ sum(individual attributes)`
   always holds (equality only when no pixel matches more than one code, which
   is guaranteed by the raster encoding).

4. **`"constant"` band dependency:** `generate_vector` calls
   `raster.select(["constant"])` on every iteration. All source rasters must
   have a band named `"constant"`. If the raster band name changes, every
   attribute computation silently fails by producing an empty mask.

5. **No-change pixels are measured but do not enter `total_*`:** Code 1 (`fo_fo`,
   `bu_bu`, etc.) is measured as an individual attribute but is excluded from
   the corresponding `total_*` by design. This correctly separates
   "area that has changed" from "area that has not changed."

### Known code concern

`generate_vector` builds GEE OR expressions by string concatenation and calls
`eval()` on them (lines 173–179). This is a fragile pattern — any change to
the value format or GEE API method names would cause a runtime error with no
static warning. A direct loop using `ee.Image.Or()` would be more robust.
