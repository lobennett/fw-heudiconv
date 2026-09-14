"""A conflict must be diagnosed before downloading or changing prior output."""
import copy
import errno
import os
from pathlib import Path

import pytest

from fw_heudiconv.cli.export import download_bids, gather_bids
from testing.synthetic_flywheel import (
    Acquisition, Client, curate, dwi_set, export, source_file, template, tree_bytes,
)


@pytest.mark.parametrize('existing', ['stale_run', 'colliding_payload', 'empty'])
@pytest.mark.parametrize('dry_run', [False, True])
def test_existing_output_root_is_refused_untouched(tmp_path, existing, dry_run):
    """An export writes a whole new dataset; it never merges into a prior one."""
    client = Client(dwi_set(tmp_path, 'scan'))
    curate(client, template('dwi', 'dwi'))
    root = tmp_path / 'out' / 'bids'
    root.mkdir(parents=True)
    if existing != 'empty':
        # A run number the current curation no longer owns collides with nothing.
        name = 'sub-01_ses-01_dwi.nii.gz' if existing == 'colliding_payload' \
            else 'sub-01_ses-01_run-2_dwi.nii.gz'
        stale = root / 'sub-01/ses-01/dwi' / name
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b'previous valid output')
        (root / 'dataset_description.json').write_text('{"Name":"prior study"}')
        (root / '.bidsignore').write_text('study-specific-pattern\n')
    before = tree_bytes(root)
    with pytest.raises(FileExistsError) as exc:
        download_bids(client, gather_bids(client, 'synthetic'), str(root.parent),
                      name='bids', dry_run=dry_run)
    assert str(root) in str(exc.value)
    assert tree_bytes(root) == before
    assert client.acq.downloads == []


@pytest.mark.parametrize('extension', ['nii.gz', 'bval', 'bvec'])
def test_duplicate_source_destinations_rejected_before_any_write(tmp_path, extension):
    client = Client(dwi_set(tmp_path, 'scan'))
    curate(client, template('dwi', 'dwi'))
    rows = gather_bids(client, 'synthetic')
    row = next(r for r in rows['acquisition'] if r['name'].endswith('.' + extension))
    duplicate = copy.deepcopy(row)
    duplicate['name'] = 'other.' + extension
    rows['acquisition'].append(duplicate)
    with pytest.raises(FileExistsError) as exc:
        download_bids(client, rows, str(tmp_path / 'out'), name='bids', dry_run=False)
    assert row['name'] in str(exc.value) and duplicate['name'] in str(exc.value)
    assert client.acq.downloads == []
    assert not (tmp_path / 'out').exists()


def test_successful_export_preserves_metadata_and_does_not_modify_input(tmp_path):
    client = Client([source_file(tmp_path, 'scan.nii.gz')])
    curate(client, template('anat', 'T1w'))
    rows = gather_bids(client, 'synthetic')
    before_rows = copy.deepcopy(rows)
    prior = tmp_path / 'out' / 'prior_bids'
    prior.mkdir(parents=True)
    (prior / 'dataset_description.json').write_text('{"Name":"prior study"}')
    (prior / '.bidsignore').write_text('study-specific-pattern\n')
    before_files = tree_bytes(prior)
    root = tmp_path / 'out' / 'bids'
    download_bids(client, rows, str(root.parent), name='bids', dry_run=False)
    assert tree_bytes(prior) == before_files
    assert rows == before_rows
    assert len(list(root.rglob('*.nii.gz'))) == 1
    assert (root / 'dataset_description.json').exists()
    assert (root / '.bidsignore').exists()


def test_ignored_and_filtered_conflicts_do_not_block_export(tmp_path):
    client = Client([source_file(tmp_path, 'scan.nii.gz')])
    curate(client, template('anat', 'T1w'))
    rows = gather_bids(client, 'synthetic')
    duplicate = copy.deepcopy(rows['acquisition'][0])
    duplicate['BIDS']['ignore'] = True
    rows['acquisition'].append(duplicate)
    download_bids(client, rows, str(tmp_path / 'out'), name='bids', dry_run=False)
    assert len(list((tmp_path / 'out').rglob('*.nii.gz'))) == 1
    download_bids(client, rows, str(tmp_path / 'filtered'), name='bids', dry_run=False,
                  folders_to_download=['func'])
    assert not list((tmp_path / 'filtered').rglob('*.nii.gz'))


@pytest.mark.parametrize('level', ['project', 'subject', 'session'])
def test_attachment_conflicts_with_generated_sidecar_are_preflighted(tmp_path, level):
    client = Client([source_file(tmp_path, 'scan.nii.gz')])
    curate(client, template('anat', 'T1w'))
    rows = gather_bids(client, 'synthetic')
    rows[level].append({'name': 'sub-01_ses-01_T1w.json', 'data': 'acq', 'type': 'attachment',
                        'BIDS': {'Path': 'sub-01/ses-01/anat'}})
    with pytest.raises(FileExistsError, match='Conflicting BIDS destination'):
        download_bids(client, rows, str(tmp_path / 'out'), name='bids', dry_run=False)
    assert not (tmp_path / 'out').exists()
    assert client.acq.downloads == []


@pytest.mark.parametrize('path', ['../outside', '/absolute/outside'])
def test_out_of_tree_destinations_refused_before_write(tmp_path, path):
    client = Client([source_file(tmp_path, 'scan.nii.gz')])
    curate(client, template('anat', 'T1w'))
    rows = gather_bids(client, 'synthetic')
    rows['acquisition'][0]['BIDS']['Path'] = path
    with pytest.raises(ValueError, match='escapes output root'):
        download_bids(client, rows, str(tmp_path / 'out'), name='bids', dry_run=False)
    assert client.acq.downloads == []
    assert not (tmp_path / 'out').exists()


def test_dry_run_creates_gradient_paths_without_downloading(tmp_path):
    client = Client(dwi_set(tmp_path, 'scan'))
    curate(client, template('dwi', 'dwi'))
    download_bids(client, gather_bids(client, 'synthetic'), str(tmp_path / 'out'),
                  name='bids', dry_run=True)
    assert len(list((tmp_path / 'out').rglob('*.bval'))) == 1
    assert len(list((tmp_path / 'out').rglob('*.bvec'))) == 1
    assert client.acq.downloads == []


def anat_client(tmp_path):
    client = Client([source_file(tmp_path / 'sources', 'scan.nii.gz')])
    curate(client, template('anat', 'T1w'))
    return client


def test_publication_does_not_reread_the_staged_payload(tmp_path, monkeypatch):
    """Publishing a staged file must not cost a second full read+write of it."""
    if getattr(os, 'geteuid', lambda: 1)() == 0:
        pytest.skip('running as root bypasses the unreadable-source check')
    client = anat_client(tmp_path)
    real_download = Acquisition.download_file

    def download_then_seal(self, name, dest, **kwargs):
        real_download(self, name, dest, **kwargs)
        os.chmod(dest, 0)

    monkeypatch.setattr(Acquisition, 'download_file', download_then_seal)
    root = tmp_path / 'out' / 'bids'

    download_bids(client, gather_bids(client, 'synthetic'), str(root.parent),
                  name='bids', dry_run=False)

    published = root / 'sub-01/ses-01/anat/sub-01_ses-01_T1w.nii.gz'
    published.chmod(0o644)
    assert published.read_bytes() == (tmp_path / 'sources/scan.nii.gz').read_bytes()


def test_publication_falls_back_to_copying_without_hardlink_support(tmp_path, monkeypatch):
    client = anat_client(tmp_path)

    def unsupported(source, destination, **kwargs):
        raise OSError(errno.EPERM, 'hardlinks unsupported')

    monkeypatch.setattr(os, 'link', unsupported)
    root = tmp_path / 'out' / 'bids'

    download_bids(client, gather_bids(client, 'synthetic'), str(root.parent),
                  name='bids', dry_run=False)

    published = root / 'sub-01/ses-01/anat/sub-01_ses-01_T1w.nii.gz'
    assert published.read_bytes() == (tmp_path / 'sources/scan.nii.gz').read_bytes()


def test_publication_never_overwrites_a_racing_writer(tmp_path, monkeypatch):
    """Control: each destination is still claimed exclusively at publication."""
    client = anat_client(tmp_path)
    sentinel = b'another exporter got here first'
    real_mkdir = Path.mkdir

    def racing_mkdir(self, *args, **kwargs):
        real_mkdir(self, *args, **kwargs)
        if self.name == 'anat' and '.fw-heudiconv-' not in str(self):
            (self / 'sub-01_ses-01_T1w.nii.gz').write_bytes(sentinel)

    monkeypatch.setattr(Path, 'mkdir', racing_mkdir)
    root = tmp_path / 'out' / 'bids'

    with pytest.raises(FileExistsError):
        download_bids(client, gather_bids(client, 'synthetic'), str(root.parent),
                      name='bids', dry_run=False)

    assert (root / 'sub-01/ses-01/anat/sub-01_ses-01_T1w.nii.gz').read_bytes() == sentinel
