from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


PAN_INDIA_DRAINAGE_LINES_GPKG_PATH = (
    PROJECT_ROOT / "data/base_layers/drainage_lines_pan_india.gpkg"
)

DRAINAGE_DENSITY_OUTPUT = PROJECT_ROOT / "data/drainage_density"
