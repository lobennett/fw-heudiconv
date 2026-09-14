"""Source identity must establish DWI pairing before dimension validation."""
import copy

import nibabel as nib
import pytest

from testing.synthetic_flywheel import (
    CONVERSION_JOB, Client, Obj, curate, dwi_set, export, source_file, template, tree_bytes,
)

DERIVED_ADC = {'ImageType': ['DERIVED', 'PRIMARY', 'DIFFUSION', 'ADC']}


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('suffix', ['dwi', 'run-{item}_dwi'])
def test_new_image_exports_its_own_gradients(tmp_path, reverse, suffix):
    old = dwi_set(tmp_path, 'z_old', 10, '2026-01-01')
    (tmp_path / 'z_old.bval').write_text('0 500 500\n')
    (tmp_path / 'z_old.bvec').write_text('0 0 0\n0 1 1\n0 0 0\n')
    client = Client(old[:])
    tmpl = template('dwi', suffix)
    curate(client, tmpl)
    new = dwi_set(tmp_path, 'a_new')
    # Upload time does not identify a conversion: older gradients can be uploaded later.
    for f in old[1:]:
        f.created = '2026-03-01'
    client.acq.files.extend(new)
    if reverse:
        client.acq.files.reverse()
    curate(client, tmpl)
    export(client, tmp_path / 'out')
    image, = (tmp_path / 'out').rglob('*.nii.gz')
    assert nib.load(image).get_fdata().mean() == 20
    stem = image.with_name(image.name[:-7])
    assert stem.with_suffix('.bval').read_text() == '0 1000 2000\n'
    assert stem.with_suffix('.bvec').read_text() == '0 1 0\n0 0 1\n0 0 0\n'
    assert not any(f.info['BIDS']['Path'] for f in old)
    before = copy.deepcopy([f.info for f in client.acq.files])
    curate(client, tmpl)
    assert [f.info for f in client.acq.files] == before


@pytest.mark.parametrize('case', ['missing', 'other_stem', 'different_job', 'tied_images'])
def test_ambiguous_dwi_rejected_before_curation_changes(tmp_path, case):
    files = dwi_set(tmp_path, 'scan', origin=Obj(type='job', id='conversion-new'))
    if case == 'missing':
        files.pop()
    elif case == 'other_stem':
        files[1] = source_file(tmp_path, 'other.bval', '0 500 500\n')
    elif case == 'different_job':
        files[1].origin = Obj(type='job', id='conversion-old')
    else:
        files += dwi_set(tmp_path, 'another')
    client = Client(files)
    before = copy.deepcopy([f.info for f in files])
    with pytest.raises(ValueError, match='(?i)DWI') as exc:
        curate(client, template('dwi', 'dwi'))
    assert 'scan' in str(exc.value)
    assert [f.info for f in files] == before
    assert client.acq.calls == []


@pytest.mark.parametrize('bad_file,text', [
    ('bval', '0 1000\n'), ('bvec', '0 1\n0 0\n0 0\n'),
    ('bvec', '0 1 0\n0 0 1\n'), ('bval', '0 NaN 2000\n'),
])
def test_invalid_gradients_leave_existing_outputs_unchanged(tmp_path, bad_file, text):
    files = dwi_set(tmp_path, 'scan')
    (tmp_path / ('scan.' + bad_file)).write_text(text)
    client = Client(files)
    curate(client, template('dwi', 'dwi'))
    out = tmp_path / 'out'
    prior = out / 'prior_bids'
    prior.mkdir(parents=True)
    (prior / 'prior.txt').write_text('valid previous output')
    before = tree_bytes(prior)
    with pytest.raises(ValueError, match='(?i)gradient') as exc:
        export(client, out)
    assert 'scan' in str(exc.value)
    assert tree_bytes(prior) == before
    assert not (out / 'bids').exists()


def test_export_rejects_legacy_same_count_mixed_sources(tmp_path):
    new = dwi_set(tmp_path, 'new')
    old = dwi_set(tmp_path, 'old', 10, '2026-01-01')
    client = Client(new)
    curate(client, template('dwi', 'dwi'))
    for source, target in zip(new[1:], old[1:]):
        target.info['BIDS'] = copy.deepcopy(source.info['BIDS'])
    client.acq.files = [new[0], *old[1:]]
    with pytest.raises(ValueError, match='(?i)DWI'):
        export(client, tmp_path / 'out')
    assert client.acq.downloads == []
    assert not (tmp_path / 'out' / 'bids').exists()


@pytest.mark.parametrize('origin', [CONVERSION_JOB, Obj(type='job', id='dcm2niix-42')])
def test_single_coherent_set_and_qa_preservation(tmp_path, origin):
    files = dwi_set(tmp_path, 'scan', origin=origin)
    client = Client(files)
    tmpl = template('dwi', 'dwi')
    curate(client, tmpl)
    export(client, tmp_path / 'out')
    assert len(list((tmp_path / 'out').rglob('*.bval'))) == 1
    for f in files:
        f.info['BIDS'].update(ignore=True, valid=False, error_message='study QA rejected')
    curate(client, tmpl)
    for f in files:
        assert f.info['BIDS']['ignore'] is True
        assert f.info['BIDS']['valid'] is False
        assert f.info['BIDS']['error_message'] == 'study QA rejected'
    export(client, tmp_path / 'ignored')
    assert not list((tmp_path / 'ignored').rglob('*.bval'))
    assert not list((tmp_path / 'ignored').rglob('*.nii.gz'))


def test_one_ignored_gradient_refuses_incomplete_export(tmp_path):
    files = dwi_set(tmp_path, 'scan')
    client = Client(files)
    curate(client, template('dwi', 'dwi'))
    files[1].info['BIDS']['ignore'] = True
    with pytest.raises(ValueError, match='(?i)DWI'):
        export(client, tmp_path / 'out')
    assert files[1].info['BIDS']['ignore'] is True
    assert not (tmp_path / 'out').exists()
    assert client.acq.downloads == []


def test_sdk_file_models_follow_same_pairing_rules():
    from datetime import datetime, timezone
    from flywheel.models.file_entry import FileEntry
    from flywheel.models.file_origin import FileOrigin
    from fw_heudiconv.backend_funcs.dwi import select_dwi_files

    origin = FileOrigin(type='job', id='conversion')
    files = [FileEntry(name='scan.' + ext, type=kind, origin=origin,
                       created=datetime(2026, 1, 1, tzinfo=timezone.utc))
             for ext, kind in [('nii.gz', 'nifti'), ('bval', 'bval'), ('bvec', 'bvec')]]
    assert [f.name for f in select_dwi_files(files)] == ['scan.nii.gz', 'scan.bval', 'scan.bvec']
    files[1].origin = FileOrigin(type='job', id='other-conversion')
    with pytest.raises(ValueError, match='provenance'):
        select_dwi_files(files)
    files[1].origin = None
    with pytest.raises(ValueError, match='provenance'):
        select_dwi_files(files)


def test_failed_download_publishes_no_partial_set(tmp_path, monkeypatch):
    client = Client(dwi_set(tmp_path, 'scan'))
    curate(client, template('dwi', 'dwi'))
    out = tmp_path / 'out'
    prior = out / 'prior_bids'
    prior.mkdir(parents=True)
    (prior / 'prior.txt').write_text('prior result')
    before = tree_bytes(prior)
    download = type(client.acq).download_file

    def fail_bvec(acq, name, dest, **kwargs):
        if name.endswith('.bvec'):
            raise OSError('synthetic download failure')
        download(acq, name, dest, **kwargs)

    monkeypatch.setattr(type(client.acq), 'download_file', fail_bvec)
    with pytest.raises(OSError, match='synthetic'):
        export(client, out)
    assert tree_bytes(prior) == before
    assert not (out / 'bids').exists()
    assert not list(out.glob('.fw-heudiconv-*'))


def test_derived_map_never_displaces_the_raw_dwi_image(tmp_path):
    files = dwi_set(tmp_path, 'scan', created='2026-01-01')
    files.append(source_file(tmp_path, 'scan_ADC.nii.gz', 99, '2026-03-01',
                             CONVERSION_JOB, DERIVED_ADC))
    client = Client(files)
    curate(client, template('dwi', 'dwi'))
    export(client, tmp_path / 'out')
    image, = (tmp_path / 'out').rglob('*.nii.gz')
    assert image.name == 'sub-01_ses-01_dwi.nii.gz'
    assert nib.load(image).get_fdata().mean() == 20
    assert image.with_name('sub-01_ses-01_dwi.bval').read_text() == '0 1000 2000\n'
    assert 'BIDS' not in files[3].info


@pytest.mark.parametrize('image_type', [
    ['DERIVED', 'PRIMARY', 'M', 'ND'],
    ['ORIGINAL', 'DERIVED', 'DIFFUSION', 'ADC'],
    'DERIVED\\PRIMARY\\DIFFUSION\\ADC',
])
def test_unknown_or_contradictory_roles_stay_candidates(tmp_path, image_type):
    files = dwi_set(tmp_path, 'scan', created='2026-01-01')
    files.append(source_file(tmp_path, 'scan_other.nii.gz', 99, '2026-03-01',
                             CONVERSION_JOB, {'ImageType': image_type}))
    client = Client(files)
    with pytest.raises(ValueError, match='(?i)DWI') as exc:
        curate(client, template('dwi', 'dwi'))
    assert 'scan_other.nii.gz' in str(exc.value)
    assert client.acq.calls == []


@pytest.mark.parametrize('origin', [
    None, Obj(type='user', id='someone@example.org'), Obj(type='job'), Obj(type='job', id=''),
])
def test_absent_or_nonconversion_provenance_is_refused(tmp_path, origin):
    files = dwi_set(tmp_path, 'scan', origin=origin)
    client = Client(files)
    before = copy.deepcopy([f.info for f in files])
    with pytest.raises(ValueError, match='provenance') as exc:
        curate(client, template('dwi', 'dwi'))
    assert 'scan.nii.gz' in str(exc.value)
    assert [f.info for f in files] == before
    assert client.acq.calls == []


def test_incomplete_new_conversion_does_not_fall_back_to_older_set(tmp_path):
    old = dwi_set(tmp_path, 'old', 10, '2026-01-01')
    client = Client(old[:])
    curate(client, template('dwi', 'dwi'))
    before = copy.deepcopy([f.info for f in old])
    client.acq.files.append(source_file(tmp_path, 'new.nii.gz'))
    with pytest.raises(ValueError, match='new.nii.gz'):
        curate(client, template('dwi', 'dwi'))
    assert [f.info for f in old] == before
