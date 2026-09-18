from pathlib import Path

import numpy as np
import pytest
from sklearn.preprocessing import StandardScaler

from dayhike import build_catalog

FIXTURE = Path(__file__).parent / "fixtures" / "scaler_complete_rows.osm"


def test_scaler_matches_sklearn_on_observed_values_not_imputed_rows(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    names = {hike.name: hike for hike in catalog.dayhikes}
    assert set(names) == {"Low Ele", "High Ele", "No Ele"}

    min_idx = catalog.scaler.feature_names.index("elevation_min_m")
    observed = np.array([[100.0], [300.0]])
    expected = StandardScaler().fit(observed)

    assert catalog.scaler.mean[min_idx] == pytest.approx(float(expected.mean_[0]))
    assert catalog.scaler.scale[min_idx] == pytest.approx(float(expected.scale_[0]))
    assert catalog.scaler.scale[min_idx] == pytest.approx(100.0)
    assert names["No Ele"].feature_vector.elevation_min_m is None
