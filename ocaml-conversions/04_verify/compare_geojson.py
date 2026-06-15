#!/usr/bin/env python
"""
④ Verify the OCaml output matches the Python reference (the golden-file gate).

Matches features by a key (default `uid`) and compares a numeric field (default `DD`),
reporting max / mean absolute difference and PASS/FAIL against a tolerance. Also compares
the per-order lists (DD_stream, str_len_km) if present.

    python 04_verify/compare_geojson.py \
        --python data/outputs/python/<loc>/dd.geojson \
        --geocaml data/outputs/geocaml/<loc>/dd.geojson \
        --field DD --key uid --atol 1e-6

Exit code 0 = PASS (within tolerance), 1 = FAIL.
"""
import argparse
import json
import sys


def load_features(path):
    gj = json.load(open(path))
    return gj["features"] if isinstance(gj, dict) and "features" in gj else gj


def index_by_key(features, key):
    out = {}
    for i, f in enumerate(features):
        props = f.get("properties", {}) or {}
        k = props.get(key, i)  # fall back to positional index if key missing
        out[k] = props
    return out


def abs_diffs(a, b):
    return [abs(x - y) for x, y in zip(a, b)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True)
    ap.add_argument("--geocaml", required=True)
    ap.add_argument("--field", default="DD")
    ap.add_argument("--key", default="uid")
    ap.add_argument("--atol", type=float, default=1e-6)
    args = ap.parse_args()

    py = index_by_key(load_features(args.python), args.key)
    oc = index_by_key(load_features(args.geocaml), args.key)

    common = sorted(set(py) & set(oc), key=str)
    only_py, only_oc = set(py) - set(oc), set(oc) - set(py)
    if only_py or only_oc:
        print(f"⚠ feature-set mismatch: only-python={len(only_py)} only-geocaml={len(only_oc)}")

    if not common:
        print("✗ FAIL: no common features to compare")
        sys.exit(1)

    diffs, worst = [], (None, 0.0)
    for k in common:
        try:
            d = abs(float(py[k][args.field]) - float(oc[k][args.field]))
        except (KeyError, TypeError, ValueError):
            print(f"✗ FAIL: field '{args.field}' missing/non-numeric for key {k!r}")
            sys.exit(1)
        diffs.append(d)
        if d > worst[1]:
            worst = (k, d)

    max_d = max(diffs)
    mean_d = sum(diffs) / len(diffs)
    print(f"compared {len(common)} features on '{args.field}'")
    print(f"  max  abs diff = {max_d:.3e}  (worst key={worst[0]!r})")
    print(f"  mean abs diff = {mean_d:.3e}")

    # optional: per-order list fields
    for list_field in ("DD_stream", "str_len_km"):
        ld = []
        for k in common:
            a, b = py[k].get(list_field), oc[k].get(list_field)
            if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
                ld += abs_diffs([float(x) for x in a], [float(y) for y in b])
        if ld:
            print(f"  {list_field}: max={max(ld):.3e} mean={sum(ld)/len(ld):.3e}")

    ok = max_d <= args.atol and not (only_py or only_oc)
    print("✓ PASS" if ok else f"✗ FAIL (max {max_d:.3e} > atol {args.atol:.0e})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
