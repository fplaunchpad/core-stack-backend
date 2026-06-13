# CoRE Stack Backend Architecture & Project Directory Mapping

The **CoRE (Common Resource Engine) Stack Backend** is a Django-based geospatial and planning platform designed to support natural resource management (NRM), watershed planning, and rural development monitoring. It acts as the data-processing and orchestration engine that links field observations, remote sensing data, and geospatial visualization.

---

## 📁 Project Structure & Application Directory Mapping

The codebase is structured into self-contained Django applications. Below is a mapping of the directory layout and the scientific/operational role of each application:

### 1. Geospatial & Scientific Calculation (`computing/`)
This is the core scientific engine of the stack. It communicates with Google Earth Engine (GEE) using Celery background tasks to execute spatial operations.
*   **Microwatershed Hydrology ([computing/mws/](file:///home/snaveen/Desktop/core-stack-backend/computing/mws/))**: Calculates spatial and temporal water budgets (Precipitation, SCS-CN Runoff, Evapotranspiration, Groundwater Recharge, Aquifer Yield, and Well Depth Fluctuation).
*   **Drought Assessment ([computing/drought/](file:///home/snaveen/Desktop/core-stack-backend/computing/drought/))**: Runs composite meteorological and agricultural drought classification loops utilizing CHIRPS precipitation, MODIS NDVI, and MODIS NDWI.
*   **CLART Engine ([computing/clart/](file:///home/snaveen/Desktop/core-stack-backend/computing/clart/))**: Generates soil and water conservation recommendations based on slope gradients, drainage densities, lineaments, and matched lithology formations.
*   **Plantation Suitability ([computing/plantation/](file:///home/snaveen/Desktop/core-stack-backend/computing/plantation/))**: Identifies suitable locations for vegetative guards and afforestation.

### 2. Geographical Administration (`geoadmin/` & `organization/`)
*   **Boundary Directories ([geoadmin/](file:///home/snaveen/Desktop/core-stack-backend/geoadmin/))**: Manages official Government administrative hierarchy lookups mapping State, District, Block/Tehsil, Gram Panchayat, and Census codes.
*   **Multi-Tenancy ([organization/](file:///home/snaveen/Desktop/core-stack-backend/organization/))**: Structures tenant organizations and governs data access isolation.

### 3. Participatory Planning & DPR Generation (`plans/` & `dpr/`)
*   **ODK Field Survey Parser ([plans/](file:///home/snaveen/Desktop/core-stack-backend/plans/))**: Ingests, validates, and parses field planner XML survey results uploaded via Open Data Kit (ODK).
*   **Detailed Project Reports ([dpr/](file:///home/snaveen/Desktop/core-stack-backend/dpr/))**: Auto-generates administrative and engineering reports in PDF/Excel formats matching state government templates (e.g. Yuktdhara).
*   **Water Rejuvenation Structures ([waterrejuvenation/](file:///home/snaveen/Desktop/core-stack-backend/waterrejuvenation/))**: Manages design models and estimations for check dams, contour trenches, farm ponds, and percolation tanks.

### 4. Public Services & Chatbot Webhooks (`public_api/`, `public_dataservice/` & `bot_interface/`)
*   **Lat/Lon Boundary Resolution ([public_api/](file:///home/snaveen/Desktop/core-stack-backend/public_api/))**: Exposes REST endpoints to query Government boundary codes and Microwatershed IDs for any coordinates.
*   **Public Data Services ([public_dataservice/](file:///home/snaveen/Desktop/core-stack-backend/public_dataservice/))**: Serving spatial layers and reports publicly.
*   **Chatbot Webhooks ([bot_interface/](file:///home/snaveen/Desktop/core-stack-backend/bot_interface/))**: Webhook integrations (e.g. WhatsApp) for query syncs.

### 5. Shared Configurations & Utilities (`utilities/` & `status_monitor/`)
*   **Threshold Constants ([utilities/constants.py](file:///home/snaveen/Desktop/core-stack-backend/utilities/constants.py))**: Central definition of scientific scales, GEE asset IDs, and categorization boundaries.
*   **GEE Interface Utilities ([utilities/gee_utils.py](file:///home/snaveen/Desktop/core-stack-backend/utilities/gee_utils.py))**: Standardizes Earth Engine initializations, task status polling, and export controls.
*   **Task Monitoring ([status_monitor/](file:///home/snaveen/Desktop/core-stack-backend/status_monitor/))**: Background dashboard to track GEE exports and Celery processing queues.

---

## 🔄 Backend Data & Sync Workflows

### 1. GIS App Interactions
The front-end client queries boundary codes and layers:
*   Resolves coordinates via APIs under `/api/v1/public_api/`.
*   Renders computed layers via WMS/WFS served by Geoserver (synced in [computing/clart/clart.py](file:///home/snaveen/Desktop/core-stack-backend/computing/clart/clart.py#L243-L255)).

### 2. Field Worker Offline Ingestion
*   ODK submissions are retrieved in background tasks.
*   Data is synced via `/api/v1/sync_offline_data/` and mapped to NRM assets.

### 3. Celery Asynchronous Workers
*   Long-running GIS/GEE processing tasks are queued under the `nrm` queue.
*   Monitored using Celery task definitions inside each app directory.
