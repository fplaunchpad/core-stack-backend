#!/usr/bin/env bash
# End-to-end verify for drainage density: ② Python reference → ③ OCaml port → ④ compare.
# Run inside the conda env that has geopandas:  conda activate corestackenv
# Usage:  ./run_verify.sh [config/location_synthetic.json]
set -euo pipefail
cd "$(dirname "$0")"

CONFIG="${1:-config/location_synthetic.json}"
SLUG=$(python -c "import json;c=json.load(open('$CONFIG'));print('_'.join(str(c[k]).lower().replace(' ','_') for k in ('state','district','block')))")
echo "location slug: $SLUG"

echo "② Python reference …"
python 02_python_reference/drainage_density_ref.py --config "$CONFIG"

echo "③ OCaml port …"
mkdir -p "data/outputs/geocaml/$SLUG"
( cd 03_geocaml \
  && eval "$(opam env --switch=geocompute)" \
  && dune build \
  && dune exec ./bin/main.exe -- drainage \
       --mws "../data/inputs/$SLUG/mws.geojson" \
       --lines "../data/inputs/$SLUG/drainage_lines.geojson" \
       --out "../data/outputs/geocaml/$SLUG/dd.geojson" )

echo "④ Verify …"
python 04_verify/compare_geojson.py \
  --python "data/outputs/python/$SLUG/dd.geojson" \
  --geocaml "data/outputs/geocaml/$SLUG/dd.geojson" \
  --field DD --key uid --atol 1e-6
