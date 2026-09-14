"""Offline controls for the authorized compatibility-review corrections."""
import copy
import json
from pathlib import Path

import nibabel as nib
import pytest

from fw_heudiconv.cli.export import download_bids, gather_bids
from testing.synthetic_flywheel import (
    CONVERSION_JOB, Client, curate, dwi_set, export, source_file, template, tree_bytes,
)


@pytest.mark.parametrize('reverse', [False, True])
def test_equal_timestamp_phase_never_replaces_bold_magnitude(tmp_path, reverse):
    files = [source_file(tmp_path, 'scan_e1.nii.gz', 20),
             source_file(tmp_path, 'scan_e1_ph.nii.gz', 99)]
    # Converter metadata can also identify a phase file without the suffix.
    files.append(source_file(tmp_path, 'other_e1.nii.gz', 99, '2026-03-01',
                             info={'ImageType': ['ORIGINAL', 'PRIMARY', 'P']}))
    client = Client(list(reversed(files)) if reverse else files)
    tmpl = template('func', 'task-rest_run-{seqitem}_echo-{echo}_bold')
    curate(client, tmpl)
    export(client, tmp_path / 'out')
    image, = (tmp_path / 'out').rglob('*.nii.gz')
    assert image.name == 'sub-01_ses-01_task-rest_run-1_echo-1_bold.nii.gz'
    assert nib.load(image).get_fdata().mean() == 20
    assert all('BIDS' not in f.info for f in files[1:])


@pytest.mark.parametrize('reverse', [False, True])
def test_network_fieldmap_and_magnitude_with_phase_named_hz_map(tmp_path, reverse):
    # Exact templates/metadata of Network's admitted "fmap-fieldmap" mapping.
    fieldmap = 'sub-{subject}/{session}/fmap/sub-{subject}_{session}_run-1_fieldmap'
    magnitude = 'sub-{subject}/{session}/fmap/sub-{subject}_{session}_run-1_magnitude'
    files = [source_file(tmp_path, 'scan_e2.nii.gz', 20),
             source_file(tmp_path, 'scan_e2_ph.nii.gz', 40,
                         info={'ImageType': ['DERIVED', 'PRIMARY', 'P'], 'Units': 'Hz'})]
    client = Client(list(reversed(files)) if reverse else files)
    client.acq.label = 'fmap-fieldmap'
    for _ in range(2):
        curate(client, fieldmap, metadata_extras={'Units': 'Hz'})
        curate(client, magnitude)
    export(client, tmp_path / 'out')
    images = {p.name: nib.load(p).get_fdata().mean()
              for p in (tmp_path / 'out').rglob('*.nii.gz')}
    assert images == {'sub-01_ses-01_run-1_fieldmap.nii.gz': 40,
                      'sub-01_ses-01_run-1_magnitude.nii.gz': 20}
    sidecar, = (tmp_path / 'out').rglob('*_fieldmap.json')
    assert json.loads(sidecar.read_text())['Units'] == 'Hz'


@pytest.mark.parametrize('units', [None, 'rad'])
def test_phase_filename_alone_cannot_establish_an_hz_fieldmap(tmp_path, units):
    client = Client([source_file(tmp_path, 'scan_e2_ph.nii.gz',
                                 info={'Units': units, 'ImageType': ['ORIGINAL', 'P']})])
    with pytest.raises(ValueError, match='fieldmap'):
        curate(client, template('fmap', 'fieldmap'), metadata_extras={'Units': 'Hz'})
    assert client.acq.calls == []


@pytest.mark.parametrize('folder,suffix', [('func', 'echo-{echo}_bold'),
                                         ('anat', 'T1w'), ('dwi', 'dwi')])
def test_mapped_empty_selection_reports_acquisition_and_template(tmp_path, folder, suffix):
    client = Client([])
    tmpl = template(folder, suffix)
    with pytest.raises(ValueError) as error:
        curate(client, tmpl)
    assert 'acq' in str(error.value) and tmpl in str(error.value)
    assert client.acq.calls == []


def test_uncertain_dwi_reports_acquisition_without_mutating_it(tmp_path):
    client = Client(dwi_set(tmp_path, 'scan', origin=None))
    before = copy.deepcopy(client.acq.files)
    with pytest.raises(ValueError) as error:
        curate(client, template('dwi', 'dwi'))
    assert 'acq (synthetic)' in str(error.value) and 'provenance' in str(error.value)
    assert client.acq.files == before and client.acq.calls == []


@pytest.mark.parametrize('role', ['ADC', 'FA', 'TRACEW', 'SBRef'])
@pytest.mark.parametrize('reverse', [False, True])
def test_positive_converter_roles_exclude_newer_dwi_derivatives(tmp_path, role, reverse):
    files = dwi_set(tmp_path, 'scan', created='2026-01-01')
    info = {'ImageType': ['DERIVED', 'PRIMARY', 'DIFFUSION', role]}
    if role == 'SBRef':
        info = {'ImageType': ['ORIGINAL', 'PRIMARY', 'DIFFUSION'],
                'SeriesDescription': 'scan_SBRef'}
    files.append(source_file(tmp_path, 'extra.nii.gz', 99, '2026-03-01',
                             CONVERSION_JOB, info))
    client = Client(list(reversed(files)) if reverse else files)
    curate(client, template('dwi', 'dwi'))
    export(client, tmp_path / 'out')
    image, = (tmp_path / 'out').rglob('*.nii.gz')
    assert nib.load(image).get_fdata().mean() == 20
    assert 'BIDS' not in files[-1].info


@pytest.mark.parametrize('info', [
    {'SeriesDescription': 'scan_SBRef'},
    {'ImageType': ['ORIGINAL', 'DERIVED', 'DIFFUSION'], 'SeriesDescription': 'scan_SBRef'},
    {'ImageType': ['ORIGINAL', 'PRIMARY', 'DIFFUSION'], 'SeriesDescription': 'scan'},
])
def test_unknown_or_conflicting_reference_metadata_stays_a_candidate(tmp_path, info):
    files = dwi_set(tmp_path, 'scan', created='2026-01-01')
    files.append(source_file(tmp_path, 'new.nii.gz', 99, '2026-03-01', CONVERSION_JOB, info))
    client = Client(files)
    with pytest.raises(ValueError, match='new.nii.gz'):
        curate(client, template('dwi', 'dwi'))
    assert client.acq.calls == []


def test_only_declared_derivatives_cannot_become_raw_dwi(tmp_path):
    files = dwi_set(tmp_path, 'scan')
    files[0].info['ImageType'] = ['DERIVED', 'PRIMARY', 'DIFFUSION', 'ADC']
    client = Client(files)
    with pytest.raises(ValueError, match='DWI'):
        curate(client, template('dwi', 'dwi'))
    assert client.acq.calls == []


@pytest.mark.parametrize('text', ['0\n1000\n2000\n', ' 0\t1000\n\n 2000  \n'])
def test_equivalent_bval_whitespace_preserves_values(tmp_path, text):
    files = dwi_set(tmp_path, 'scan')
    Path(files[1].source).write_text(text)
    client = Client(files)
    curate(client, template('dwi', 'dwi'))
    export(client, tmp_path / 'out')
    bval, = (tmp_path / 'out').rglob('*.bval')
    assert bval.read_text() == text


@pytest.mark.parametrize('identity', [{'version': 1}, {'version': 0},
                                      {'hash': 'selected'}, {'version': 1, 'hash': 'selected'}])
@pytest.mark.parametrize('replaced', ['scan.nii.gz', 'scan.bval'])
def test_download_request_pins_selection_despite_stale_sdk_cache(tmp_path, monkeypatch,
                                                               identity, replaced):
    files = dwi_set(tmp_path, 'scan')
    for f in files:
        f.version, f.hash = identity.get('version'), identity.get('hash')
    client = Client(files)
    curate(client, template('dwi', 'dwi'))
    rows = gather_bids(client, 'synthetic')
    original = {f.name: Path(f.source).read_bytes() for f in files}
    new_image = source_file(tmp_path, 'replacement.nii.gz', 99)
    replacement = (Path(new_image.source).read_bytes() if replaced.endswith('.gz')
                   else b'0 500 500\n')
    cached = copy.deepcopy(files)

    def serve(acq, name, dest, **kwargs):
        # Remote versions change independently of the already loaded SDK _files.
        payload = original[name]
        if name == replaced:
            if 'version' in kwargs:
                assert kwargs['version'] == identity['version']
            elif kwargs.get('hash'):
                raise ValueError('synthetic server hash mismatch')
            else:
                payload = replacement
        Path(dest).write_bytes(payload)

    monkeypatch.setattr(type(client.acq), 'download_file', serve)
    prior = tmp_path / 'out' / 'prior_bids'
    prior.mkdir(parents=True)
    (prior / 'keep').write_bytes(b'valid prior output')
    before = tree_bytes(prior)
    if 'version' not in identity:
        with pytest.raises(ValueError, match='hash mismatch'):
            download_bids(client, rows, tmp_path / 'out', name='bids', dry_run=False)
        assert not (tmp_path / 'out' / 'bids').exists()
    else:
        download_bids(client, rows, tmp_path / 'out', name='bids', dry_run=False)
        image, = (tmp_path / 'out' / 'bids').rglob('*.nii.gz')
        bval, = (tmp_path / 'out' / 'bids').rglob('*.bval')
        assert nib.load(image).get_fdata().mean() == 20
        assert bval.read_bytes() == b'0 1000 2000\n'
    assert files == cached and tree_bytes(prior) == before


def test_missing_project_namespace_preserves_prior_noncrashing_behavior(tmp_path):
    client = Client([source_file(tmp_path, 'scan.nii.gz')])
    curate(client, template('anat', 'T1w'))
    client.project.info = {}
    export(client, tmp_path / 'out')
    assert json.loads((tmp_path / 'out' / 'bids' / 'dataset_description.json').read_text()) is None


def test_sdk_download_boundary_receives_both_selected_identities(tmp_path, monkeypatch):
    from flywheel.models.acquisition import Acquisition
    from flywheel.models.file_entry import FileEntry

    files = dwi_set(tmp_path, 'scan')
    client = Client(files)
    curate(client, template('dwi', 'dwi'))
    rows = gather_bids(client, 'synthetic')
    sdk_acq = Acquisition(id='acq', files=[
        FileEntry(name=f.name, version=f.version, hash=f.hash) for f in files])
    client.objects['acq'] = sdk_acq
    requests = []

    def transport(api, acquisition_id, name, destination, **kwargs):
        # Real SDK download_file forwards into this boundary; no SDK client authenticates.
        requests.append((acquisition_id, name, kwargs))
        source = next(f for f in files if f.name == name)
        Path(destination).write_bytes(Path(source.source).read_bytes())

    monkeypatch.setattr(sdk_acq, '_invoke_file_api', transport)
    download_bids(client, rows, tmp_path / 'out', name='bids', dry_run=False)
    assert requests == [('acq', f.name, {'version': 1, 'hash': f.hash}) for f in files]
    image, = (tmp_path / 'out').rglob('*.nii.gz')
    assert nib.load(image).get_fdata().mean() == 20


@pytest.mark.parametrize('level', ['project', 'subject', 'session'])
def test_attachment_downloads_retain_available_identity(tmp_path, level):
    client = Client([source_file(tmp_path, 'scan.nii.gz')])
    curate(client, template('anat', 'T1w'))
    attached = source_file(tmp_path, 'study.txt', 'selected study metadata')
    attached.info['BIDS'] = {'Path': ''}
    skipped = source_file(tmp_path, 'unmapped.txt', 'not exported')
    container = getattr(client, level)
    container.files = [attached, skipped]

    def download(name, dest, **kwargs):
        # The unbound request would return replacement metadata instead.
        text = 'selected study metadata' if kwargs == {'version': 1, 'hash': attached.hash} \
            else 'replacement study metadata'
        Path(dest).write_text(text)

    container.download_file = download
    export(client, tmp_path / 'out')
    assert (tmp_path / 'out' / 'bids' / 'study.txt').read_text() == 'selected study metadata'


def test_existing_dangling_output_symlink_is_refused(tmp_path):
    client = Client([source_file(tmp_path, 'scan.nii.gz')])
    curate(client, template('anat', 'T1w'))
    out = tmp_path / 'out'
    out.mkdir()
    target = tmp_path / 'absent'
    (out / 'bids').symlink_to(target, target_is_directory=True)
    with pytest.raises(FileExistsError):
        export(client, out)
    assert (out / 'bids').is_symlink() and not target.exists()
    assert client.acq.downloads == []
