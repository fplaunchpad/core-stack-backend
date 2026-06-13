# Project Abbreviation Glossary

This glossary contains key abbreviations, acronyms, and terminologies used throughout the CoRE Stack codebase, databases, and scientific models.

---

## 🛰️ Remote Sensing & Datasets
*   **CHIRPS**: Climate Hazards Group InfraRed Precipitation with Station data (used for long-term rainfall statistics and drought calculations).
*   **DEM**: Digital Elevation Model (topographical elevation map representation).
*   **FLDAS**: Famine Early Warning Systems Network Land Data Assimilation System (NASA dataset providing soil moisture and evapotranspiration data).
*   **GLDAS**: Global Land Data Assimilation System (NASA model providing global land surface indicators).
*   **GSMaP**: Global Satellite Mapping of Precipitation (JAXA dataset offering high-resolution rainfall data).
*   **JAXA**: Japan Aerospace Exploration Agency (satellite dataset provider).
*   **LULC**: Land Use Land Cover (surface classification mapping, e.g., forest, crops, urban).
*   **MODIS**: Moderate Resolution Imaging Spectroradiometer (satellite sensor providing NDVI, NDWI, and PET data).
*   **NDVI**: Normalized Difference Vegetation Index (measures vegetative canopy greenness/health).
*   **NDWI**: Normalized Difference Water Index (measures liquid water content in vegetation and surface water bodies).
*   **SRTM**: Shuttle Radar Topography Mission (NASA high-resolution global DEM).
*   **STAC**: SpatioTemporal Asset Catalog (specification for organizing and querying geospatial metadata).

---

## 💧 Hydrological & Climate Science
*   **ET**: Evapotranspiration (total water transferred from the land to the atmosphere).
    *   *Implementation*: [computing/mws/evapotranspiration.py](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/evapotranspiration.py)
*   **MAI**: Moisture Adequacy Index (ratio of actual evapotranspiration to potential evapotranspiration, $ET/PET$).
    *   *Implementation*: [computing/drought/generate_layers.py](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/generate_layers.py#L998-L1007)
*   **MWS**: Microwatershed (a small hydrological drainage basin used as the baseline geographical unit for local planning).
    *   *Implementation*: [computing/mws/mws.py](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/mws.py)
*   **PAS**: Percent of Area Sown (compares current cultivated crop acreage to historical crop potential).
    *   *Implementation*: [computing/drought/generate_layers.py](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/generate_layers.py)
*   **PET**: Potential Evapotranspiration (maximum possible water vaporization under prevailing atmospheric conditions).
*   **SCS-CN**: Soil Conservation Service Curve Number (empirical hydrologic method used to estimate surface runoff from rainfall).
    *   *Implementation*: [computing/mws/run_off.py](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/run_off.py)
*   **SPI**: Standardized Precipitation Index (meteorological index used to quantify rainfall deficits/droughts over time).
    *   *Implementation*: [computing/drought/generate_layers.py](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/generate_layers.py#L692-L698)
*   **VCI**: Vegetation Condition Index (standardized index monitoring vegetation health compared to historical extremes).
    *   *Implementation*: [computing/drought/generate_layers.py](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/generate_layers.py#L852-L890)

---

## 🛠️ Planning & Administrative
*   **CGWB**: Central Ground Water Board (government agency providing aquifer classification datasets in India).
*   **CLART**: Composite Land Assessment and Restoration Tool (decision tool recommending locations for groundwater structures).
    *   *Implementation*: [computing/clart/clart.py](file:///home/snaveen/Desktop/core-stack-backend/computing/clart/clart.py)
*   **DET**: Design Estimation Tool (companion software for engineering dimensions and cost estimations of NRM assets).
*   **DPR**: Detailed Project Report (comprehensive technical and budgetary planning report generated for block level NRM approval).
    *   *Implementation*: [dpr/](file:///home/snaveen/Desktop/core-stack-backend/dpr/)
*   **GP**: Gram Panchayat (local village governing council).
*   **LGD / Census**: Local Government Directory (official government administrative census coding).
    *   *Implementation*: [geoadmin/](file:///home/snaveen/Desktop/core-stack-backend/geoadmin/)
*   **MGNREGA / NREGA**: Mahatma Gandhi National Rural Employment Guarantee Act (public employment program funding local watershed works).
*   **NRM**: Natural Resource Management (general planning category of soil, water, and forest conservation projects).
*   **SOI**: Survey of India (national mapping agency providing administrative boundary datasets).

---

## 💻 Tech & GIS Infrastructure
*   **API**: Application Programming Interface.
    *   *Endpoints*: [public_api/](file:///home/snaveen/Desktop/core-stack-backend/public_api/)
*   **GEE**: Google Earth Engine (cloud computing platform for processing planetary-scale geospatial data).
    *   *GEE Utils*: [utilities/gee_utils.py](file:///home/snaveen/Desktop/core-stack-backend/utilities/gee_utils.py)
*   **GCS**: Google Cloud Storage.
*   **JWT**: JSON Web Token (user authentication token).
*   **ODK**: Open Data Kit (open-source tool suite used to build mobile survey forms and aggregate field planner sync logs).
    *   *Forms Parser*: [plans/](file:///home/snaveen/Desktop/core-stack-backend/plans/)
*   **SLD**: Styled Layer Descriptor (XML styling schema used by Geoserver to render maps).
*   **WFS**: Web Feature Service (OGC standard for serving vector map coordinates).
*   **WMS**: Web Map Service (OGC standard for serving georeferenced map images).
