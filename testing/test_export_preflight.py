"""A conflict must be diagnosed before downloading or changing prior output."""
import copy

import pytest

from fw_heudiconv.cli.export import download_bids, gather_bids
from testing.synthetic_flywheel import (
    Client, curate, dwi_set, export, source_file, template, tree_bytes,
)


@pytest.mark.parametrize('extension', ['nii.gz', 'json', 'bval', 'bvec'])
@pytest.mark.parametrize('dry_run', [False, True])
def test_existing_destination_conflict_preserves_whole_tree(tmp_path, extension, dry_run):
    client = Client(dwi_set(tmp_path, 'scan'))
    curate(client, template('dwi', 'dwi'))
    root = tmp_path / 'out' / 'bids'
    dest = root / 'sub-01/ses-01/dwi' / ('sub-01_ses-01_dwi.' + extension)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b'previous valid output')
    (root / 'dataset_description.json').write_text('{"Name":"prior study"}')
    (root / '.bidsignore').write_text('study-specific-pattern\n')
    before = tree_bytes(root)
    with pytest.raises(FileExistsError) as exc:
        download_bids(client, gather_bids(client, 'synthetic'), str(root.parent),
                      name='bids', dry_run=dry_run)
    assert str(dest) in str(exc.value)
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
    root = tmp_path / 'out' / 'bids'
    root.mkdir(parents=True)
    (root / 'dataset_description.json').write_text('{"Name":"prior study"}')
    (root / '.bidsignore').write_text('study-specific-pattern\n')
    before_files = tree_bytes(root)
    download_bids(client, rows, str(root.parent), name='bids', dry_run=False)
    for filename, data in before_files.items():
        assert (root / filename).read_bytes() == data
    assert rows == before_rows
    assert len(list(root.rglob('*.nii.gz'))) == 1


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
