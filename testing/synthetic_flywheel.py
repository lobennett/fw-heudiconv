"""Local SDK transport for synthetic curation/export tests; never authenticates."""
import copy
from pathlib import Path
from types import SimpleNamespace

import nibabel as nib
import numpy as np

from fw_heudiconv.backend_funcs.convert import apply_heuristic
from fw_heudiconv.cli.export import download_bids, gather_bids


class Obj(dict):
    __setattr__ = dict.__setitem__

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None

    def to_dict(self):
        return copy.deepcopy(dict(self))


class Acquisition(Obj):
    def update_file_info(self, name, info):
        self.calls.append((name, copy.deepcopy(info)))
        target = next(f for f in self.files if f.name == name)
        target.info.update(copy.deepcopy(info))

    def get_file(self, name):
        return next(f for f in self.files if f.name == name)

    def download_file(self, name, dest):
        self.downloads.append(name)
        Path(dest).write_bytes(Path(self.get_file(name).source).read_bytes())


class Client:
    def __init__(self, files):
        self.subject = Obj(id='subject', label='01', files=[])
        self.session = Obj(id='session', label='01', subject=self.subject, files=[])
        self.project = Obj(id='project', label='synthetic', files=[],
                           info={'BIDS': {'Name': 'synthetic', 'BIDSVersion': '1.6.0'}})
        self.acq = Acquisition(id='acq', label='synthetic', files=files,
                               parents=Obj(subject='subject', session='session'),
                               calls=[], downloads=[])
        self.session.acquisitions = lambda: [self.acq]
        self.projects = SimpleNamespace(find_first=lambda query: self.project)
        self.objects = {o.id: o for o in [self.subject, self.session, self.project, self.acq]}

    def get(self, oid):
        return self.objects[oid]

    get_acquisition = get

    def get_project_sessions(self, pid):
        return [self.session]

    def get_session_acquisitions(self, sid):
        return [Obj(_id='acq')]


def source_file(root, name, value=20, created='2026-02-01', origin=None):
    source = root / name
    source.parent.mkdir(parents=True, exist_ok=True)
    if name.endswith(('.nii.gz', '.nii')):
        ftype = 'nifti'
        nib.save(nib.Nifti1Image(np.full((2, 2, 2, 3), value, np.float32), np.eye(4)), source)
    else:
        ftype = name.rsplit('.', 1)[-1]
        source.write_text(value)
    return Obj(name=name, type=ftype, created=created, source=str(source), origin=origin,
               info={'StudyNote': {'keep': ['unchanged']}}, parent=Obj(id='acq'))


def dwi_set(root, stem, value=20, created='2026-02-01', origin=None):
    return [source_file(root, stem + '.nii.gz', value, created, origin),
            source_file(root, stem + '.bval', '0 1000 2000\n', created, origin),
            source_file(root, stem + '.bvec', '0 1 0\n0 0 1\n0 0 0\n', created, origin)]


def template(folder, suffix):
    return 'sub-{subject}/ses-{session}/' + folder + '/sub-{subject}_ses-{session}_' + suffix


def curate(client, tmpl, **kwargs):
    apply_heuristic(client, (tmpl, ('nii.gz',), None), 'acq', **kwargs)


def export(client, root, **kwargs):
    rows = gather_bids(client, 'synthetic')
    download_bids(client, rows, str(root), name='bids', dry_run=False, **kwargs)


def tree_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
