# Exploration & Feature Implementation Dashboard

This document serves as a unified reference dashboard for understanding the scientific resources behind the CoRE Stack project and tracking future feature implementations.

---

## 🔗 Part 1: Scientific & Project References

### A. CoRE Stack Platform
*   **[CoRE Stack Official Documentation](https://docs.core-stack.org/)**: Central guide for setup, installation, and deployment.
*   **[CoRE Stack API Documentation](https://api-doc.core-stack.org/)**: Interactive Swagger portal to explore endpoints.
*   **[CoRE Stack Database Design](https://github.com/core-stack-org/core-stack-backend/wiki/DB-Design)**: Wiki containing PostgreSQL schema and tables.
*   **[Integrating Custom Pipelines Guide](https://docs.google.com/document/d/1lfx2hJKndmzVp55ZHIIFYqRTz-8fZCWc9QikUDQpTN0/edit?usp=sharing)**: Outlines how to build and register new Celery tasks.

### B. Hydrology & Terrain Modeling
*   **[SCS-CN Runoff Method Reference](https://www.wcc.nrcs.usda.gov/ftpref/wntsc/H&H/NEHhydrology/ch10.pdf)**: USDA National Engineering Handbook chapter on the Curve Number method.
*   **[JAXA GSMaP Precipitation (GEE Catalog)](https://developers.google.com/earth-engine/datasets/catalog/JAXA_GPM_L3_GSMaP_v6_operational)**: JAXA satellite rainfall dataset catalog.
*   **[NASA FLDAS Land Data Assimilation System](https://ldas.gsfc.nasa.gov/fldas)**: NASA FEWS NET land surface indicators.
*   **[Google Dynamic World LULC (GEE Catalog)](https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_DYNAMICWORLD_V1)**: 10m deep-learning land cover composite catalog.
*   **[FABDEM Global Elevation (GEE Catalog)](https://developers.google.com/earth-engine/datasets/catalog/projects_sat-io_open-datasets_FABDEM)**: Forest-and-building adjusted 30m DEM.

### C. Participatory Planning (CLART)
*   **[India Observatory Portal](https://www.indiaobservatory.org.in/)**: Spatial decision platform built by the Foundation for Ecological Security (FES).
*   **[Foundation for Ecological Security (FES India)](https://fes.org.in/)**: The non-profit leading community ecological restoration programs.
*   **[CLART App Concept Overview (YouTube Video)](https://www.youtube.com/watch?v=0k5G9_U-aF8)**: Video guide on translating geology layers into village-level recommendations.

---

## 🚀 Part 2: Feature Implementation Backlog

The following features are proposed to expand the CoRE Stack project into a collaborative open-data and sponsorship network. For full architectures, refer to the [Future Feature Roadmap](file:///home/snaveen/Desktop/core-stack-backend/myref/myref/extra.md).

### 1. Advanced Data Exchange
*   `[ ]` **Open-GIS STAC Catalog**: Expose calculated spatial layers via SpatioTemporal Asset Catalog (STAC) standards (Proposed in a new subfolder in [public_api/](file:///home/snaveen/Desktop/core-stack-backend/public_api/)).
*   `[ ]` **IoT Well Telemetry Receiver**: Build webhook handlers to ingest piezometer data (Proposed in [bot_interface/](file:///home/snaveen/Desktop/core-stack-backend/bot_interface/) or a new `telemetry/` app).
*   `[ ]` **Secure Org-to-Org Data Rooms**: Allow private sharing of shapefile layers for third-party audits (Proposed in [organization/](file:///home/snaveen/Desktop/core-stack-backend/organization/)).

### 2. Next-Gen Mobile Collection
*   `[ ]` **Offline Mobile AI Classifier**: Train and integrate an offline TensorFlow Lite model in ODK client forms (Proposed parser in [plans/](file:///home/snaveen/Desktop/core-stack-backend/plans/)).
*   `[ ]` **Augmented Reality (AR) Overlay**: Validate recommended check-dam placement relative to physical contours in real-time (Proposed utility in [waterrejuvenation/](file:///home/snaveen/Desktop/core-stack-backend/waterrejuvenation/)).
*   `[ ]` **Citizen Science Gamification**: Reward local youth logging rainfall metrics with leaderboard points (Proposed in a new `community_engagement/` app).

### 3. Digital Sponsorship & Donations
*   `[ ]` **"Sponsor a Structure" Map**: Public crowdfunding portal exposing recommended CLART assets (Proposed frontend dashboard and endpoints in [public_api/](file:///home/snaveen/Desktop/core-stack-backend/public_api/)).
*   `[ ]` **eROI Dashboard (Ecological ROI)**: Run automated GEE time-series analyses surrounding funded coordinates (Proposed task in [computing/misc/](file:///home/snaveen/Desktop/core-stack-backend/computing/misc/)).
*   `[ ]` **Compute & Dataset Donations**: Allow institutions to donate GEE cloud quotas or labeled training polygon datasets.
