"""Unit tests for multi-echo file selection in apply_heuristic.

fw-heudiconv upstream numbers every convertible file in an acquisition with a
positional {item}. Multi-echo BOLD acquisitions on Flywheel carry extra
derivative NIfTIs (optimally-combined, t2smap, sbref, ...) alongside the raw
echoes, so {item} runs 1..N and never matches the real echo index. These tests
pin the sherlock-compat behaviour: when a template contains {echo}, select only
the raw-echo NIfTIs (filename ``_e<N>``), deduplicate per echo by created time,
and index by the true echo number.
"""

from fw_heudiconv.backend_funcs.convert import _select_echo_files, _select_files


class FakeFile:
    def __init__(self, name, created=""):
        self.name = name
        self.created = created


def test_selects_three_echoes_dropping_derivatives():
    files = [
        FakeFile("sub_9_1_e1.nii.gz"),
        FakeFile("sub_9_1_e2.nii.gz"),
        FakeFile("sub_9_1_e3.nii.gz"),
        FakeFile("sub_9_1_optcom.nii.gz"),   # derivative, no _e<N>
        FakeFile("sub_9_1_t2smap.nii.gz"),   # derivative, no _e<N>
        FakeFile("sub_9_1.json"),            # sidecar, not a nifti
    ]
    got = _select_echo_files(files)
    assert [echo for _, echo in got] == [1, 2, 3]
    assert [f.name for f, _ in got] == [
        "sub_9_1_e1.nii.gz",
        "sub_9_1_e2.nii.gz",
        "sub_9_1_e3.nii.gz",
    ]


def test_dedups_per_echo_by_created_time():
    files = [
        FakeFile("run_e1.nii.gz", created="2021-01-01"),
        FakeFile("run_e1.nii.gz", created="2022-06-01"),  # newer wins
        FakeFile("run_e2.nii.gz", created="2021-01-01"),
    ]
    got = _select_echo_files(files)
    assert [echo for _, echo in got] == [1, 2]
    e1 = next(f for f, e in got if e == 1)
    assert e1.created == "2022-06-01"


def test_empty_when_no_echo_files():
    files = [FakeFile("anat_T1w.nii.gz"), FakeFile("anat_T1w.json")]
    assert _select_echo_files(files) == []


def test_select_files_splits_fieldmap_and_magnitude():
    files = [
        FakeFile("sess_11_1_fieldmap.nii.gz"),
        FakeFile("sess_11_1.nii.gz"),  # magnitude (no _fieldmap tag)
    ]
    fmap = _select_files(files, "sub-{subject}/{session}/fmap/sub-{subject}_{session}_run-1_fieldmap")
    assert [f.name for f, _ in fmap] == ["sess_11_1_fieldmap.nii.gz"]
    mag = _select_files(files, "sub-{subject}/{session}/fmap/sub-{subject}_{session}_run-1_magnitude")
    assert [f.name for f, _ in mag] == ["sess_11_1.nii.gz"]


def test_select_files_echo_and_default():
    echo_tmpl = "sub-{subject}/{session}/func/x_run-1_echo-{echo}_bold"
    files = [FakeFile("r_e1.nii.gz"), FakeFile("r_e2.nii.gz"), FakeFile("r_optcom.nii.gz")]
    assert [e for _, e in _select_files(files, echo_tmpl)] == [1, 2]
    # non-echo, non-fmap template keeps all files, un-indexed
    anat = [FakeFile("a.nii.gz"), FakeFile("a.bval")]
    assert _select_files(anat, "x_run-1_T1w") == [(anat[0], None), (anat[1], None)]
