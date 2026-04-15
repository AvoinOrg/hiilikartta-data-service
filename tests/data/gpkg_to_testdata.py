"""Convert a GeoPackage into the GeoJSON-in-ZIP shape that the /calculation
endpoint consumes (the same format as ``tests/data/test-data-small-polygon.zip``).

Key points:

* The endpoint reads the zip with ``geopandas.read_file`` and then force-sets
  CRS to EPSG:4326 **without reprojecting**, so the GeoJSON we emit must already
  be in EPSG:4326 lon/lat.
* Each feature needs the plan-level properties the UI and calculator expect
  (``id``, ``area_ha``, ``name``, ``zoning_code``, ``geometry_mode``,
  ``selected``). Most real gpkg sources (kaava layers, etc.) do not carry a
  valid ``zoning_code`` — the ``--zoning-code`` / ``--landuse`` overrides fix
  that for every feature at once so calculations become deterministic.

Usage example::

    python tests/data/gpkg_to_testdata.py \\
        tests/data/test_data_2026_04_15.gpkg \\
        tests/data/test_data_2026_04_15.zip \\
        --zoning-code AK \\
        --landuse 20,40,30,10

The ``--landuse`` values are the four percentages (in this order):
``built, new_open_vegetation, new_tree_vegetation, existing``. They must sum
to 100. Omit the flag to leave landuse columns off the features (the
calculator falls back to ``existing=100``).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Optional, Tuple

import fiona
import geopandas as gpd


LANDUSE_COLS = (
    "landuse_built",
    "landuse_new_open_vegetation",
    "landuse_new_tree_vegetation",
    "landuse_existing",
)


def _parse_landuse(raw: Optional[str]) -> Optional[Tuple[float, float, float, float]]:
    if raw is None:
        return None
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "--landuse expects 4 comma-separated percentages: "
            "built,new_open,new_tree,existing"
        )
    values = tuple(float(p) for p in parts)
    total = sum(values)
    if abs(total - 100.0) > 1e-6:
        raise argparse.ArgumentTypeError(
            f"--landuse percentages must sum to 100 (got {total})"
        )
    return values  # type: ignore[return-value]


def convert(
    gpkg_path: Path,
    output_zip: Path,
    zoning_code: str,
    landuse: Optional[Tuple[float, float, float, float]] = None,
    layer: Optional[str] = None,
    name_prefix: str = "test-area",
) -> None:
    layers = fiona.listlayers(str(gpkg_path))
    if not layers:
        raise SystemExit(f"No layers found in {gpkg_path}")
    layer = layer or layers[0]

    gdf = gpd.read_file(str(gpkg_path), layer=layer)
    if gdf.crs is None:
        raise SystemExit(
            f"Layer {layer!r} has no CRS; set one before converting."
        )
    gdf = gdf.to_crs("EPSG:4326")

    gdf = gdf[
        gdf.geometry.notna()
        & gdf.geometry.apply(
            lambda g: g is not None and g.geom_type in ("Polygon", "MultiPolygon")
        )
    ].copy()

    invalid = ~gdf.geometry.is_valid
    if invalid.any():
        gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].buffer(0)
    gdf = gdf[gdf.geometry.is_valid].copy()

    if gdf.empty:
        raise SystemExit("No valid polygon features after cleaning.")

    # Areas: compute in EPSG:3067 (Finnish TM35FIN) because source data is FI.
    area_ha = gdf.to_crs("EPSG:3067").area / 10_000.0

    # Build the flat property set the plan ingestion expects. We strip all
    # original columns; the calculator only uses these.
    features = []
    for idx, (row_idx, row) in enumerate(gdf.iterrows()):
        feature_id = str(uuid.uuid4())
        props = {
            "id": feature_id,
            "area_ha": float(area_ha.iloc[idx]),
            "name": f"{name_prefix}-{idx + 1}",
            "zoning_code": zoning_code,
            "geometry_mode": "polygon",
            "selected": True,
        }
        if landuse is not None:
            built, new_open, new_tree, existing = landuse
            props[LANDUSE_COLS[0]] = built
            props[LANDUSE_COLS[1]] = new_open
            props[LANDUSE_COLS[2]] = new_tree
            props[LANDUSE_COLS[3]] = existing
        geometry = json.loads(
            gpd.GeoSeries([row.geometry], crs="EPSG:4326").to_json()
        )["features"][0]["geometry"]
        features.append(
            {
                "id": feature_id,
                "type": "Feature",
                "geometry": geometry,
                "properties": props,
            }
        )

    payload = {"type": "FeatureCollection", "features": features}
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("file", json.dumps(payload))

    print(
        f"Wrote {len(features)} feature(s) to {output_zip} "
        f"(zoning_code={zoning_code!r}, landuse={landuse})"
    )


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gpkg", type=Path, help="Input .gpkg file")
    parser.add_argument("output", type=Path, help="Output .zip file")
    parser.add_argument(
        "--zoning-code",
        required=True,
        help="Override zoning_code applied to every feature (e.g. AK, E, ENsl).",
    )
    parser.add_argument(
        "--landuse",
        type=_parse_landuse,
        default=None,
        help=(
            "Optional override for the landuse distribution, applied to every "
            "feature. Format: built,new_open,new_tree,existing (must sum to 100)."
        ),
    )
    parser.add_argument(
        "--layer",
        default=None,
        help="Optional gpkg layer name (defaults to the first layer).",
    )
    parser.add_argument(
        "--name-prefix",
        default="test-area",
        help="Prefix used for the per-feature name property.",
    )
    args = parser.parse_args(argv)

    convert(
        gpkg_path=args.gpkg,
        output_zip=args.output,
        zoning_code=args.zoning_code,
        landuse=args.landuse,
        layer=args.layer,
        name_prefix=args.name_prefix,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
