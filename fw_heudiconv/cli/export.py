import flywheel
import copy
import tempfile
import nibabel as nib
import numpy as np
import argparse
import os
import logging
import warnings
import json
import shutil
import re
import csv
import pandas as pd
from pathlib import Path
from fw_heudiconv.backend_funcs.query import print_directory_tree
from fw_heudiconv.backend_funcs.dwi import split_image_name, validate_dwi_sources


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('fw-heudiconv-exporter')


def regex_attachments(regex, attachment_names):

    print(attachment_names)
    print(regex)
    r = re.compile(regex)
    matches = list(filter(r.match, attachment_names))
    print(matches)
    if matches:
        return True
    else:
        return False


def get_nested(dct, *keys):
    for key in keys:
        try:
            dct = dct[key]
        except (KeyError, TypeError):
            return None
    return dct


def file_identity(f):
    """Return whichever SDK version/hash identities the selected file exposes.

    Flywheel replaces a file in place under the same name, so a name is not an
    identity. Only recorded, non-empty values are usable; absent ones are not
    reconstructible and simply leave the download unbound.
    """
    return {key: value for key, value in (('version', get_nested(f, 'version')),
                                          ('hash', get_nested(f, 'hash')))
            if value is not None and value != ''}


def normalize_timing_units(d):
    """Coerce DICOM-native millisecond timing fields to BIDS seconds, in place.

    Flywheel file ``info`` sometimes carries ``RepetitionTime``/``EchoTime`` in
    the DICOM-native millisecond unit (tags 0018,0080 / 0018,0081) rather than
    the BIDS-required seconds, and inconsistently across acquisitions in the same
    project. fw-heudiconv copies ``info`` verbatim into the sidecar, so the ms
    values leak through, fail the validator (REPETITION_TIME_MISMATCH vs the
    NIfTI header), and corrupt any downstream tool that reads TR/TE from the JSON.

    No fMRI ``RepetitionTime`` exceeds 100 s and no ``EchoTime`` exceeds 1 s, so a
    value above those bounds is unambiguously milliseconds -> divide by 1000.
    ``SliceTiming`` is already emitted in seconds by the converter and is left
    untouched. Only numeric values are touched; anything else passes through.
    """
    if not isinstance(d, dict):
        return d
    rt = d.get('RepetitionTime')
    if isinstance(rt, (int, float)) and not isinstance(rt, bool) and rt > 100:
        d['RepetitionTime'] = rt / 1000.0
    te = d.get('EchoTime')
    if isinstance(te, (int, float)) and not isinstance(te, bool) and te > 1:
        d['EchoTime'] = te / 1000.0
    return d


def download_sidecar(d, fpath, remove_bids=True):
    d = copy.deepcopy(d)

    if remove_bids and isinstance(d, dict) and 'BIDS' in d:
        if 'Task' in d['BIDS']:
            if d['BIDS']['Task'] != "":
                d['TaskName'] = d['BIDS']['Task']
        del d['BIDS']

    normalize_timing_units(d)

    with open(fpath, 'w') as sidecar:
        json.dump(d, fp=sidecar, sort_keys=True, indent=4)


def check_tasks(root_path):

    paths = [os.path.join(x[0], y) for x in os.walk(root_path) for y in x[2]]
    paths = [x for x in paths if 'func' in x and 'rest' not in x]

    if not paths:
        logger.info("No task events in this bids dataset.")
        return

    niftis = [x for x in paths if '.nii.gz' in x]
    tsvs = [x for x in paths if '.tsv' in x]

    if not tsvs:
        logger.warning("No events.tsv found in func folder; creating empty TSVs")
        for nii in niftis:
            path = re.sub(r'(?<=_)[a-zA-Z]+\.nii\.gz', 'events.tsv', nii)
            with open(str(path), "wt") as f:
                tsv_writer = csv.writer(f, delimiter='\t')
                tsv_writer.writerow(['onset', 'duration'])

    else:
        for nii in niftis:
            shortened = re.sub(r'(?<=_)[a-zA-Z]+\.nii\.gz', '', nii)
            has_matching_tsv = False
            for t in tsvs:
                if shortened in t:
                    has_matching_tsv = True
                    continue
            if not has_matching_tsv:
                logger.warning("No events.tsv found for {}; creating empty TSV".format(shortened))
                path = shortened + 'events.tsv'
                with open(str(path), "wt") as f:
                    tsv_writer = csv.writer(f, delimiter='\t')
                    tsv_writer.writerow(['onset', 'duration'])


def gather_bids(client, project_label, subject_labels=None, session_labels=None):
    '''
    {
    'name': container.filename,
    'path': path,
    'type': type of file,
    'data': container.id
    }
    '''
    logger.info("Gathering bids data:")

    to_download = {
        'dataset_description': [],
        'project': [],
        'subject': [],
        'session': [],
        'acquisition': []
    }

    # dataset description
    project_obj = client.projects.find_first('label="{}"'.format(project_label))
    assert project_obj, "Project not found! Maybe check spelling...?"

    # get dataset description file
    to_download['dataset_description'].append({
        'name': 'dataset_description.json',
        'type': 'dataset_description',
        'data': get_nested(project_obj, 'info', 'BIDS')
    })
    # download any project level files
    logger.info("Processing project files...")
    project_files = project_obj.files
    for pf in project_files:
        if 'dataset_description' in pf.name:
            continue
        d = {
            'name': pf.name,
            'type': 'attachment',
            'data': project_obj.id,
            'identity': file_identity(pf),
            'BIDS': get_nested(pf, 'info', 'BIDS')
        }
        to_download['project'].append(d)

    # session level
    logger.info("Processing session files...")
    sessions = client.get_project_sessions(project_obj.id)

    # filters
    if subject_labels:
        sessions = [s for s in sessions if s.subject['label'] in subject_labels]
    if session_labels:
        sessions = [s for s in sessions if s.label in session_labels]
    assert sessions, "No sessions found!"

    if subject_labels or session_labels:
        logger.info("Found {} sessions...".format(str(len(sessions))))

    subjects_unsorted = [x.subject for x in sessions]
    subjects = []

    for sub in subjects_unsorted:
        if sub not in subjects:
            subjects.append(sub)

    for sub in subjects:
        if sub.files and len(sub.files) > 1:
            for sf in sub.files:
                d = {
                    'name': sf.name,
                    'type': sf.type,
                    'data': sub.id,
                    'identity': file_identity(sf),
                    'BIDS': get_nested(sf, 'info', 'BIDS')
                }
                to_download['subject'].append(d)

    for ses in sessions:
        if ses.files and len(ses.files) > 1:
            for sf in ses.files:
                d = {
                    'name': sf.name,
                    'type': sf.type,
                    'data': ses.id,
                    'identity': file_identity(sf),
                    'BIDS': get_nested(sf, 'info', 'BIDS')
                }
                to_download['session'].append(d)

    # acquistion level
    logger.info("Processing acquisition files...")
    acquisitions = [a for s in sessions for a in client.get_session_acquisitions(s.id)]
    acquisitions2 = [client.get_acquisition(acq['_id']) for acq in acquisitions]
    acquisition_files = [f for acq in acquisitions2 for f in acq.get('files')]
    for af in acquisition_files:
        d = {
            'name': af.name,
            'type': af.type,
            'data': af.parent.id,
            'origin': get_nested(af, 'origin'),
            'identity': file_identity(af),
            'BIDS': get_nested(af, 'info', 'BIDS'),
            'sidecar': get_nested(af, 'info')
        }
        if any(x in d['name'] for x in ['bval', 'bvec']):
            del d['sidecar']
        if d['BIDS'] and d['BIDS'] != "NA":
            to_download['acquisition'].append(d)
    return to_download


def _export_entries(to_download, root, folders, attachments):
    """Resolve every payload and generated sidecar before touching the output tree."""
    entries = []

    def add(relative, kind, data, source):
        relative = Path(relative)
        destination = (root / relative).resolve()
        if relative.is_absolute() or root not in destination.parents:
            raise ValueError('BIDS destination escapes output root: {} ({})'.format(
                relative, source))
        entries.append(dict(path=destination, kind=kind, data=data, source=source))

    if to_download['dataset_description']:
        description = to_download['dataset_description'][0]
        add(description['name'], 'json', description['data'], 'dataset description')
    if not any(f['name'] == '.bidsignore' for f in to_download['project']):
        add('.bidsignore', 'text', 'perf/\nqsm/\n**/fmap/*.bvec\n**/fmap/*.bval',
            'default BIDS ignore')

    for level in ('project', 'subject', 'session', 'acquisition'):
        for fi in to_download[level]:
            if level == 'subject' and attachments and fi['name'] not in attachments:
                continue
            if (level == 'session' and attachments
                    and not any(re.search(pattern, fi['name']) for pattern in attachments)):
                continue
            bids = fi.get('BIDS')
            if not isinstance(bids, dict) or bids.get('ignore'):
                continue
            path = bids.get('Path')
            if level == 'acquisition':
                if not path or bids.get('Folder') not in folders:
                    continue
                filename = bids.get('Filename')
                if not filename:
                    continue
            else:
                if path is None:
                    continue
                filename = fi['name']
            source = '{} {}:{}'.format(level, fi['data'], fi['name'])
            relative = Path(path) / filename
            add(relative, 'file', fi, source)
            if level == 'acquisition' and 'sidecar' in fi and fi['type'] == 'nifti':
                stem, extension = split_image_name(filename)
                if extension not in ('.nii', '.nii.gz'):
                    raise ValueError('Invalid NIfTI destination: ' + source)
                add(Path(path) / (stem + '.json'), 'sidecar', fi['sidecar'], source + ' JSON')
    return entries


def _preflight_destinations(entries):
    """Reject curations that cannot be written as one coherent tree."""
    owners = {}
    for entry in entries:
        destination = entry['path']
        if destination in owners:
            raise FileExistsError('Conflicting BIDS destination {}: {} and {}'.format(
                destination, owners[destination], entry['source']))
        owners[destination] = entry['source']
    for destination, source in owners.items():
        for parent in destination.parents:
            if parent in owners:
                raise FileExistsError('Conflicting BIDS directory {} for {} ({})'.format(
                    parent, destination, source))


def _dwi_export_sets(entries):
    groups = {}
    for entry in entries:
        if entry['kind'] != 'file':
            continue
        stem, extension = split_image_name(entry['path'].name)
        if stem.endswith('_dwi') and extension:
            groups.setdefault(entry['path'].with_name(stem), []).append(entry)
    for destination, group in groups.items():
        sources = [entry['data'] for entry in group]
        if len({f['data'] for f in sources}) != 1:
            raise ValueError('Ambiguous DWI pairing at {}: different acquisitions: {}'.format(
                destination, ', '.join(entry['source'] for entry in group)))
        try:
            validate_dwi_sources(sources)
        except ValueError as exc:
            raise ValueError('{} at destination {}'.format(exc, destination)) from exc
    return groups.values()


def _validate_dwi_gradients(group, staged):
    """Validate counts/shape only after source identity has established pairing."""
    paths = {}
    for entry in group:
        extension = split_image_name(entry['path'].name)[1]
        paths['nifti' if extension in ('.nii', '.nii.gz') else extension] = staged[entry['path']]
    evidence = ', '.join(entry['source'] for entry in group)
    try:
        shape = nib.load(str(paths['nifti'])).shape
        if len(shape) not in (3, 4) or any(n < 1 for n in shape):
            raise ValueError('expected a nonempty 3D or 4D DWI image, got {}'.format(shape))
        volumes = shape[3] if len(shape) == 4 else 1
        # Bvals are a vector; whitespace layout does not change its values.
        bvals = np.asarray([float(value) for value in paths['.bval'].read_text().split()])
        bvecs = np.loadtxt(paths['.bvec'], ndmin=2)
        if bvals.size != volumes or bvecs.shape != (3, volumes):
            raise ValueError('image has {} volumes; bval shape {}, bvec shape {}; '
                             'expected N bvals and (3, N) bvecs'.format(
                                 volumes, bvals.shape, bvecs.shape))
        if not np.isfinite(bvals).all() or not np.isfinite(bvecs).all():
            raise ValueError('nonfinite bval or bvec values')
    except (ValueError, OSError, nib.filebasedimages.ImageFileError) as exc:
        raise ValueError('Invalid DWI gradients for {}: {}'.format(evidence, exc)) from exc


def download_bids(
    client, to_download, root_path,
    folders_to_download=['anat', 'dwi', 'func', 'fmap', 'perf'],
    attachments=None, dry_run=True, name='bids_dataset'
        ):
    root = Path(root_path, name)
    # An export writes one whole dataset. Merging into an existing tree would
    # silently mix it with output the current curation no longer owns, and
    # deleting that tree would destroy prior results, so refuse it untouched.
    if root.exists() or root.is_symlink():
        raise FileExistsError(
            'BIDS output directory already exists: {}. Export never merges into or '
            'deletes prior output; choose an unused --destination/--directory-name.'
            .format(root))
    root = root.resolve()
    entries = _export_entries(to_download, root, folders_to_download, attachments)
    _preflight_destinations(entries)
    dwi_sets = list(_dwi_export_sets(entries))
    if dry_run:
        logger.info('Preparing output directory tree (gradient contents are not checked)...')
        root.mkdir(parents=True)
        for entry in entries:
            entry['path'].parent.mkdir(parents=True, exist_ok=True)
            entry['path'].touch(exist_ok=False)
    else:
        logger.info('Downloading files...')
        # Stage under the chosen destination parent. Failed downloads or invalid
        # DWI contents leave no output root behind at all.
        root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.fw-heudiconv-', dir=root.parent) as scratch:
            staged = {}
            for entry in entries:
                target = Path(scratch) / entry['path'].relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                kind, data = entry['kind'], entry['data']
                if kind == 'file':
                    container = client.get(data['data'])
                    # The SDK forwards these to the server's download endpoint.
                    # A cached or refreshed metadata comparison cannot bind bytes.
                    container.download_file(data['name'], str(target),
                                            **(data.get('identity') or {}))
                elif kind in ('json', 'sidecar'):
                    download_sidecar(data, str(target), remove_bids=(kind == 'sidecar'))
                else:
                    target.write_text(data)
                staged[entry['path']] = target
            for group in dwi_sets:
                _validate_dwi_gradients(group, staged)
            # Claim the root exclusively, so an exporter racing us into the same
            # dataset fails here rather than interleaving two curations.
            root.mkdir(parents=True)
            for destination, source in staged.items():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open('xb') as output, source.open('rb') as downloaded:
                    shutil.copyfileobj(downloaded, output)
    logger.info('Done!')
    print_directory_tree(str(root))


def get_parser():

    parser = argparse.ArgumentParser(
        description="Export BIDS-curated data from Flywheel")
    parser.add_argument(
        "--project",
        help="The project in flywheel",
        required=True
    )
    parser.add_argument(
        "--path",
        help="The target directory to download [DEPRECATED. PLEASE USE <DESTINATION> INSTEAD]",
        default=None
    )
    parser.add_argument(
        "--subject",
        help="The subject(s) to export",
        nargs="+",
        default=None,
        type=str
    )
    parser.add_argument(
        "--session",
        help="The session(s) to export",
        nargs="+",
        default=None,
        type=str
    )
    parser.add_argument(
        "--folders",
        help="The BIDS folders to export",
        nargs="+",
        default=['anat', 'dwi', 'fmap', 'func', 'perf']
    )
    parser.add_argument(
        "--attachments",
        help="Only download attachment files matching these names",
        nargs="+",
        default=None
    )
    parser.add_argument(
        "--dry-run",
        help="Don't apply changes (only print the directory tree to the console)",
        action='store_true',
        default=False
    )
    parser.add_argument(
        "--destination",
        help="Path to destination directory",
        default=".",
        type=str
    )
    parser.add_argument(
        "--directory-name",
        help="Name of destination directory",
        default="bids_directory",
        type=str
    )
    parser.add_argument(
        "--api-key",
        help="API Key",
        action='store',
        default=None
    )
    parser.add_argument(
        "--verbose",
        help="Print ongoing messages of progress",
        action='store_true',
        default=False
    )

    return parser


def main():

    logger.info("{:=^70}\n".format(": fw-heudiconv exporter starting up :"))
    parser = get_parser()
    args = parser.parse_args()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if args.api_key:
            fw = flywheel.Client(args.api_key)
        else:
            fw = flywheel.Client()
    assert fw, "Your Flywheel CLI credentials aren't set!"

    if args.path:
        destination = args.path
    else:
        destination = args.destination

    if not os.path.exists(destination):
        logger.info("Creating destination directory...")
        os.makedirs(args.destination)

    downloads = gather_bids(
        client=fw, project_label=args.project, session_labels=args.session,
        subject_labels=args.subject
        )

    if args.attachments is not None and args.verbose:
        logger.info("Filtering attachments...")
        logger.info(args.attachments)

    download_bids(
        client=fw, to_download=downloads, root_path=destination,
        folders_to_download=args.folders, dry_run=args.dry_run,
        attachments=args.attachments, name=args.directory_name
        )

    if args.dry_run:
        shutil.rmtree(Path(args.destination, args.directory_name))

    logger.info("Done!")
    logger.info("{:=^70}".format(": Exiting fw-heudiconv exporter :"))


if __name__ == '__main__':
    main()
