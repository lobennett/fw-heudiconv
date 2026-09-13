"""Consecutive curation must not leave multiple active copies of one destination."""
import copy

import nibabel as nib
import pytest

from testing.synthetic_flywheel import Client, curate, export, source_file, template


@pytest.mark.parametrize('folder,suffix,source', [
    ('func', 'task-rest_echo-{echo}_bold', 'scan_e2'),
    ('fmap', 'fieldmap', 'scan_fieldmap'),
    ('fmap', 'magnitude', 'scan'),
    ('anat', 'T1w', 'scan'),
])
@pytest.mark.parametrize('reverse', [False, True])
def test_recuration_retires_only_superseded_destination(tmp_path, folder, suffix, source, reverse):
    old = source_file(tmp_path, 'old_' + source + '.nii.gz', 10, '2026-01-01')
    client = Client([old])
    tmpl = template(folder, suffix)
    curate(client, tmpl)
    old.info['BIDS']['StudyQA'] = {'note': 'retain'}
    unrelated = source_file(tmp_path, 'unrelated.nii.gz', 99, '2025-01-01')
    unrelated.info['BIDS'] = {'Path': 'sub-01/ses-01/anat', 'Filename': 'other_T2w.nii.gz',
                              'Folder': 'anat', 'ignore': True, 'StudyQA': 'keep'}
    unrelated_before = copy.deepcopy(unrelated.info)
    new = source_file(tmp_path, 'new_' + source + '.nii.gz')
    client.acq.files.extend([new, unrelated])
    if reverse:
        client.acq.files.reverse()
    curate(client, tmpl)
    assert not old.info['BIDS']['Path']
    assert old.info['BIDS']['StudyQA'] == {'note': 'retain'}
    assert old.info['StudyNote'] == {'keep': ['unchanged']}
    assert unrelated.info == unrelated_before
    before = copy.deepcopy([f.info for f in client.acq.files])
    curate(client, tmpl)
    assert [f.info for f in client.acq.files] == before
    export(client, tmp_path / 'out')
    images = list((tmp_path / 'out').rglob('*.nii.gz'))
    assert len(images) == 1
    assert nib.load(images[0]).get_fdata().mean() == 20


def test_recuration_never_unignores_qa_rejection(tmp_path):
    old = source_file(tmp_path, 'old_e2.nii.gz', 10, '2026-01-01')
    client = Client([old])
    tmpl = template('func', 'task-rest_echo-{echo}_bold')
    curate(client, tmpl)
    old.info['BIDS'].update(ignore=True, error_message='old QA rejection', valid=False)
    new = source_file(tmp_path, 'new_e2.nii.gz')
    new.info['BIDS'] = dict(old.info['BIDS'], error_message='new QA rejection')
    client.acq.files.append(new)
    curate(client, tmpl)
    for f in [old, new]:
        assert f.info['BIDS']['ignore'] is True
        assert f.info['BIDS']['valid'] is False
        assert f.info['BIDS']['error_message'] == f.name[:3] + ' QA rejection'
    export(client, tmp_path / 'out')
    assert not list((tmp_path / 'out').rglob('*.nii.gz'))


def test_fieldmap_curation_preserves_magnitude_and_other_echo(tmp_path):
    fieldmap = source_file(tmp_path, 'old_fieldmap.nii.gz', 10, '2026-01-01')
    magnitude = source_file(tmp_path, 'old.nii.gz', 10, '2026-01-01')
    client = Client([fieldmap, magnitude])
    curate(client, template('fmap', 'fieldmap'))
    curate(client, template('fmap', 'magnitude'))
    before = copy.deepcopy(magnitude.info)
    client.acq.files.append(source_file(tmp_path, 'new_fieldmap.nii.gz'))
    curate(client, template('fmap', 'fieldmap'))
    assert magnitude.info == before
    assert not fieldmap.info['BIDS']['Path']
    export(client, tmp_path / 'out')
    assert len(list((tmp_path / 'out').rglob('*.nii.gz'))) == 2


def test_dry_curation_does_not_mutate_shared_metadata(tmp_path):
    old = source_file(tmp_path, 'old_e2.nii.gz', 10, '2026-01-01')
    client = Client([old])
    tmpl = template('func', 'task-rest_echo-{echo}_bold')
    curate(client, tmpl)
    new = source_file(tmp_path, 'new_e2.nii.gz')
    client.acq.files.append(new)
    before = copy.deepcopy([f.info for f in client.acq.files])
    client.acq.calls.clear()
    curate(client, tmpl, dry_run=True)
    assert [f.info for f in client.acq.files] == before
    assert client.acq.calls == []


def test_failed_replacement_keeps_prior_active_tag(tmp_path, monkeypatch):
    old = source_file(tmp_path, 'old_e2.nii.gz', 10, '2026-01-01')
    client = Client([old])
    tmpl = template('func', 'task-rest_echo-{echo}_bold')
    curate(client, tmpl)
    before = copy.deepcopy(old.info)
    new = source_file(tmp_path, 'new_e2.nii.gz')
    client.acq.files.append(new)
    update = type(client.acq).update_file_info

    def fail_new(acq, name, info):
        if name == new.name:
            raise OSError('synthetic metadata transport failure')
        update(acq, name, info)

    monkeypatch.setattr(type(client.acq), 'update_file_info', fail_new)
    with pytest.raises(OSError, match='synthetic'):
        curate(client, tmpl)
    assert old.info == before


@pytest.mark.parametrize('old_ext,new_ext', [('.nii', '.nii.gz'), ('.nii.gz', '.nii')])
def test_recuration_across_nifti_compression(tmp_path, old_ext, new_ext):
    old = source_file(tmp_path, 'old' + old_ext, 10, '2026-01-01')
    client = Client([old])
    tmpl = template('anat', 'T1w')
    curate(client, tmpl)
    client.acq.files.append(source_file(tmp_path, 'new' + new_ext))
    curate(client, tmpl)
    export(client, tmp_path / 'out')
    image, = (tmp_path / 'out').rglob('*' + new_ext)
    assert nib.load(image).get_fdata().mean() == 20
    assert not old.info['BIDS']['Path']


def test_three_echo_recuration_keeps_true_echo_entities(tmp_path):
    files = [source_file(tmp_path, 'old_e%d.nii.gz' % echo, echo, '2026-01-01')
             for echo in (1, 2, 3)]
    client = Client(files)
    tmpl = template('func', 'task-rest_echo-{echo}_bold')
    curate(client, tmpl)
    first, third = copy.deepcopy(files[0].info), copy.deepcopy(files[2].info)
    client.acq.files += [source_file(tmp_path, 'new_e2.nii.gz'),
                         source_file(tmp_path, 'optcom.nii.gz', 99, '2026-03-01')]
    curate(client, tmpl)
    assert files[0].info == first and files[2].info == third
    export(client, tmp_path / 'out')
    images = sorted((tmp_path / 'out').rglob('*.nii.gz'))
    assert [p.name for p in images] == [
        'sub-01_ses-01_task-rest_echo-1_bold.nii.gz',
        'sub-01_ses-01_task-rest_echo-2_bold.nii.gz',
        'sub-01_ses-01_task-rest_echo-3_bold.nii.gz',
    ]
    assert [nib.load(p).get_fdata().mean() for p in images] == [1, 20, 3]


def test_full_heuristic_query_curate_export_path(tmp_path):
    from fw_heudiconv.cli.curate import convert_to_bids

    heuristic = tmp_path / 'synthetic_heuristic.py'
    heuristic.write_text('''
def ReplaceSubject(label):
    return "renamed"

def ReplaceSession(label):
    return "02"

def infotodict(seqinfos):
    key = ("sub-{subject}/ses-{session}/func/sub-{subject}_ses-{session}_task-rest_run-{seqitem}_echo-{echo}_bold", ("nii.gz",), None)
    return {key: [s.series_id for s in seqinfos if s.acquisition_label == "synthetic"]}
''')
    client = Client([source_file(tmp_path, 'old_e2.nii.gz', 10, '2026-01-01')])
    convert_to_bids(client, 'synthetic', str(heuristic))
    client.acq.files.append(source_file(tmp_path, 'new_e2.nii.gz'))
    convert_to_bids(client, 'synthetic', str(heuristic))
    export(client, tmp_path / 'out')
    image, = (tmp_path / 'out').rglob('*.nii.gz')
    assert image.name == 'sub-renamed_ses-02_task-rest_run-1_echo-2_bold.nii.gz'
    assert nib.load(image).get_fdata().mean() == 20
