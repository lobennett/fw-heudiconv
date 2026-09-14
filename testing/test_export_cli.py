"""The exporter CLI must act on one effective output root, never a second one."""
import sys

import flywheel
import pytest

from fw_heudiconv.cli.export import main
from testing.synthetic_flywheel import Client, curate, source_file, template, tree_bytes

EXPORTED = 'bids_directory/sub-01/ses-01/anat/sub-01_ses-01_T1w.nii.gz'


@pytest.fixture
def run_cli(tmp_path, monkeypatch):
    """Run ``fw-heudiconv-export`` from an empty cwd against a synthetic project."""
    client = Client([source_file(tmp_path / 'sources', 'scan.nii.gz')])
    curate(client, template('anat', 'T1w'))
    cwd = tmp_path / 'cwd'
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(flywheel, 'Client', lambda *args, **kwargs: client)

    def run(*argv):
        monkeypatch.setattr(sys, 'argv', ['fw-heudiconv-export', '--project',
                                          'synthetic'] + list(argv))
        main()

    run.cwd = cwd
    return run


def prior_export(root):
    """A prior valid export owned by some other invocation."""
    path = root / 'bids_directory'
    (path / 'sub-01/ses-01/anat').mkdir(parents=True)
    (path / 'sub-01/ses-01/anat/sub-01_ses-01_T1w.nii.gz').write_bytes(b'prior image')
    (path / 'dataset_description.json').write_text('{"Name":"prior study"}')
    return path


def test_dry_run_cleanup_leaves_unrelated_prior_export_intact(run_cli):
    """``--path`` moves the whole export, including which tree is cleaned up."""
    untouched = prior_export(run_cli.cwd)
    before = tree_bytes(untouched)
    scratch = run_cli.cwd / 'scratch'
    scratch.mkdir()

    run_cli('--path', str(scratch), '--dry-run')

    assert tree_bytes(untouched) == before
    assert not (scratch / 'bids_directory').exists()
    assert scratch.exists()


def test_dry_run_cleanup_removes_only_the_placeholder_tree(run_cli):
    """Cleanup removes the tree this invocation built, not its parent."""
    run_cli('--destination', 'scratch', '--dry-run')

    assert not (run_cli.cwd / 'bids_directory').exists()
    assert (run_cli.cwd / 'scratch').is_dir()
    assert not (run_cli.cwd / 'scratch/bids_directory').exists()


@pytest.mark.parametrize('flag', ['--path', '--destination'])
def test_missing_destination_directory_is_created_at_the_effective_root(run_cli, flag):
    run_cli(flag, 'scratch/deep')

    assert (run_cli.cwd / 'scratch/deep' / EXPORTED).read_bytes()
    assert not (run_cli.cwd / 'bids_directory').exists()


def test_refused_export_root_deletes_nothing(run_cli):
    """A refused export must not fall through to any cleanup."""
    untouched = prior_export(run_cli.cwd)
    before = tree_bytes(untouched)
    occupied = prior_export(run_cli.cwd / 'scratch')
    occupied_before = tree_bytes(occupied)

    with pytest.raises(FileExistsError) as exc:
        run_cli('--path', str(run_cli.cwd / 'scratch'), '--dry-run')

    assert str(occupied) in str(exc.value)
    assert tree_bytes(untouched) == before
    assert tree_bytes(occupied) == occupied_before
