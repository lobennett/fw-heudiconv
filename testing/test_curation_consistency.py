"""Consecutive curation must not leave multiple active copies of one destination."""
import copy

import nibabel as nib
import pytest

from testing.synthetic_flywheel import (
    Client, curate, dwi_set, export, source_file, template,
)


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


@pytest.mark.parametrize('folder,suffix,source', [
    ('anat', 'T1w', 'scan'),
    ('func', 'task-rest_echo-{echo}_bold', 'scan_e2'),
    ('fmap', 'fieldmap', 'scan_fieldmap'),
])
def test_qa_rejected_newer_copy_never_displaces_the_valid_one(tmp_path, folder, suffix, source):
    valid = source_file(tmp_path, 'old_' + source + '.nii.gz', 10, '2026-01-01')
    client = Client([valid])
    tmpl = template(folder, suffix)
    curate(client, tmpl)
    rejected = source_file(tmp_path, 'new_' + source + '.nii.gz', 99, '2026-02-01')
    rejected.info['BIDS'] = {'ignore': True, 'valid': False, 'error_message': 'QA rejected'}
    rejected_before = copy.deepcopy(rejected.info)
    client.acq.files.append(rejected)

    curate(client, tmpl)

    assert valid.info['BIDS']['Path'] == 'sub-01/ses-01/' + folder
    assert valid.info['BIDS']['valid'] is True
    assert rejected.info == rejected_before
    export(client, tmp_path / 'out')
    images = list((tmp_path / 'out').rglob('*.nii.gz'))
    assert len(images) == 1
    assert nib.load(images[0]).get_fdata().mean() == 10


def test_qa_rejected_dwi_set_never_displaces_the_valid_one(tmp_path):
    valid = dwi_set(tmp_path / 'valid', 'scan', 10, '2026-01-01')
    client = Client(list(valid))
    tmpl = template('dwi', 'dwi')
    curate(client, tmpl)
    rejected = dwi_set(tmp_path / 'rejected', 'rescan', 99, '2026-02-01')
    for f in rejected:
        f.info['BIDS'] = {'ignore': True, 'valid': False, 'error_message': 'QA rejected'}
    rejected_before = copy.deepcopy([f.info for f in rejected])
    client.acq.files.extend(rejected)

    curate(client, tmpl)

    assert all(f.info['BIDS']['Path'] == 'sub-01/ses-01/dwi' for f in valid)
    assert [f.info for f in rejected] == rejected_before
    export(client, tmp_path / 'out')
    images = list((tmp_path / 'out').rglob('*.nii.gz'))
    assert len(images) == 1
    assert nib.load(images[0]).get_fdata().mean() == 10


def test_acquisition_with_only_rejected_candidates_is_omitted_untouched(tmp_path):
    rejected = source_file(tmp_path, 'scan.nii.gz', 10, '2026-01-01')
    rejected.info['BIDS'] = {'Path': 'sub-01/ses-01/anat', 'Folder': 'anat',
                             'Filename': 'sub-01_ses-01_T1w.nii.gz',
                             'ignore': True, 'valid': False, 'error_message': 'QA rejected'}
    other = source_file(tmp_path, 'rescan.nii.gz', 99, '2026-02-01')
    other.info['BIDS'] = {'Path': 'sub-01/ses-01/anat', 'Folder': 'anat',
                          'Filename': 'sub-01_ses-01_T2w.nii.gz',
                          'ignore': True, 'valid': True, 'error_message': ''}
    client = Client([rejected, other])
    before = copy.deepcopy([f.info for f in client.acq.files])

    curate(client, template('anat', 'T1w'))

    assert [f.info for f in client.acq.files] == before
    assert client.acq.calls == []
    export(client, tmp_path / 'out')
    assert not list((tmp_path / 'out').rglob('*.nii.gz'))


def test_rejected_dwi_image_with_live_gradients_is_omitted(tmp_path):
    files = dwi_set(tmp_path, 'scan')
    files[0].info['BIDS'] = {'ignore': True, 'valid': False, 'error_message': 'QA rejected'}
    client = Client(files)
    before = copy.deepcopy([f.info for f in files])

    curate(client, template('dwi', 'dwi'))

    assert [f.info for f in files] == before
    assert client.acq.calls == []
    export(client, tmp_path / 'out')
    assert not list((tmp_path / 'out').rglob('*.nii.gz'))


def test_rejected_echoes_beside_an_ineligible_image_are_omitted(tmp_path):
    echoes = [source_file(tmp_path, 'scan_e{}.nii.gz'.format(n), 10, '2026-01-01')
              for n in (1, 2, 3)]
    for f in echoes:
        f.info['BIDS'] = {'ignore': True, 'valid': False, 'error_message': 'QA rejected'}
    optcom = source_file(tmp_path, 'scan_optcom.nii.gz', 20, '2026-02-01')
    client = Client(echoes + [optcom])
    before = copy.deepcopy([f.info for f in client.acq.files])

    curate(client, template('func', 'task-rest_echo-{echo}_bold'))

    assert [f.info for f in client.acq.files] == before
    assert client.acq.calls == []


def test_rejected_image_beside_unrelated_metadata_is_omitted(tmp_path):
    rejected = source_file(tmp_path, 'scan.nii.gz', 10, '2026-01-01')
    rejected.info['BIDS'] = {'ignore': True, 'valid': False, 'error_message': 'QA rejected'}
    events = source_file(tmp_path, 'scan_events.tsv', 'onset\n1.0\n', '2026-01-01')
    client = Client([rejected, events])
    before = copy.deepcopy([f.info for f in client.acq.files])

    curate(client, template('anat', 'T1w'))

    assert [f.info for f in client.acq.files] == before
    assert client.acq.calls == []


@pytest.mark.parametrize('member', [1, 2])
def test_rejected_gradient_beside_a_live_dwi_image_still_refuses(tmp_path, member):
    """Control: QA omission must not swallow an incoherent non-rejected set."""
    files = dwi_set(tmp_path, 'scan')
    files[member].info['BIDS'] = {'ignore': True, 'valid': False,
                                  'error_message': 'QA rejected'}
    client = Client(files)

    with pytest.raises(ValueError, match='(?i)dwi'):
        curate(client, template('dwi', 'dwi'))

    assert client.acq.calls == []
