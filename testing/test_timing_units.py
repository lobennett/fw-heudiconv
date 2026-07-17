"""Regression tests for millisecond->second timing normalization in sidecars.

Motivation: some Flywheel acquisitions store RepetitionTime/EchoTime in
DICOM-native milliseconds; fw-heudiconv used to copy them verbatim, producing
sidecars that fail the BIDS validator (REPETITION_TIME_MISMATCH) and mislead
fMRIPrep. `normalize_timing_units` coerces ms->s at sidecar-write time.
"""
import json

from fw_heudiconv.cli.export import normalize_timing_units, download_sidecar


def test_ms_repetition_and_echo_time_are_divided_by_1000():
    d = {"RepetitionTime": 1490, "EchoTime": 59.4}
    normalize_timing_units(d)
    assert d["RepetitionTime"] == 1.49
    assert abs(d["EchoTime"] - 0.0594) < 1e-9


def test_second_values_are_left_untouched():
    d = {"RepetitionTime": 1.49, "EchoTime": 0.0594}
    normalize_timing_units(d)
    assert d["RepetitionTime"] == 1.49
    assert d["EchoTime"] == 0.0594


def test_slicetiming_and_unknown_fields_untouched():
    d = {"RepetitionTime": 1490, "SliceTiming": [0.0, 0.7, 1.4], "TaskName": "rest"}
    normalize_timing_units(d)
    assert d["SliceTiming"] == [0.0, 0.7, 1.4]  # already seconds; never rescaled
    assert d["TaskName"] == "rest"


def test_missing_or_nonnumeric_fields_are_safe():
    d = {"TaskName": "rest"}
    normalize_timing_units(d)  # no RepetitionTime/EchoTime -> no-op, no error
    assert d == {"TaskName": "rest"}
    d2 = {"RepetitionTime": "n/a", "EchoTime": None}
    normalize_timing_units(d2)
    assert d2 == {"RepetitionTime": "n/a", "EchoTime": None}


def test_boundary_values_not_rescaled():
    # exactly at/below the ceilings -> already seconds, do not divide
    d = {"RepetitionTime": 100, "EchoTime": 1}
    normalize_timing_units(d)
    assert d["RepetitionTime"] == 100
    assert d["EchoTime"] == 1


def test_download_sidecar_writes_normalized_json(tmp_path):
    d = {"RepetitionTime": 1490, "EchoTime": 13.4, "TaskName": "rest"}
    fpath = tmp_path / "sub-x_task-rest_bold.json"
    download_sidecar(d, str(fpath), remove_bids=False)
    written = json.loads(fpath.read_text())
    assert written["RepetitionTime"] == 1.49
    assert abs(written["EchoTime"] - 0.0134) < 1e-9
