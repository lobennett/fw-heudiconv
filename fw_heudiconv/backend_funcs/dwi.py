"""DWI source identity shared by curation and export (no SDK calls)."""


def split_image_name(name):
    """Return the source stem and imaging extension, including compound .nii.gz."""
    for extension in ('.nii.gz', '.nii', '.bval', '.bvec'):
        if name.endswith(extension):
            return name[:-len(extension)], extension
    return name, ''


def validate_dwi_sources(files):
    """Require one named conversion set, rejecting contradictory job provenance.

    Matching source stems are the converter's sidecar association, unlike BIDS
    destinations (assigned later), upload times or equal gradient counts. If
    job provenance is present, require the same job on all three files. A user
    or device ID is not a conversion ID.
    """
    evidence = ', '.join(
        '{} (origin={!r})'.format(f['name'], f.get('origin')) for f in files)
    by_extension = {}
    for f in files:
        stem, extension = split_image_name(f['name'])
        kind = 'nifti' if extension in ('.nii', '.nii.gz') else extension
        by_extension.setdefault(kind, []).append(f)
    if (set(by_extension) != {'nifti', '.bval', '.bvec'}
            or any(len(group) != 1 for group in by_extension.values())):
        raise ValueError('Ambiguous DWI set: require one NIfTI, bval and bvec; '
                         'inspect conversion outputs: ' + evidence)
    ordered = [by_extension[k][0] for k in ('nifti', '.bval', '.bvec')]
    if len({split_image_name(f['name'])[0] for f in ordered}) != 1:
        raise ValueError('Ambiguous DWI pairing: source stems differ; inspect '
                         'the original conversion outputs: ' + evidence)
    origins = [f.get('origin') or {} for f in ordered]
    jobs = [o.get('id') if o.get('type') == 'job' else None for o in origins]
    if any(jobs) and (not all(jobs) or len(set(jobs)) != 1):
        raise ValueError('Ambiguous DWI pairing: missing or conflicting conversion '
                         'job provenance; inspect conversion outputs: ' + evidence)
    return ordered


def select_dwi_files(files):
    """Select the newest image and its source sidecars, never extension-wise newest."""
    niftis = [f for f in files if split_image_name(f['name'])[1] in ('.nii', '.nii.gz')]
    if not niftis:
        if any(split_image_name(f['name'])[1] in ('.bval', '.bvec') for f in files):
            validate_dwi_sources(files)  # Report orphan gradients with filenames.
        return []
    if len(niftis) > 1:
        dates = [f.get('created') for f in niftis]
        if not all(dates) or dates.count(max(dates)) != 1:
            raise ValueError('Ambiguous DWI image selection: missing or tied creation '
                             'times; inspect conversion outputs: '
                             + ', '.join(f['name'] for f in niftis))
    image = max(niftis, key=lambda f: f.get('created') or '')
    stem = split_image_name(image['name'])[0]
    candidates = [image] + [
        f for f in files if split_image_name(f['name'])[0] == stem
        and split_image_name(f['name'])[1] in ('.bval', '.bvec')]
    try:
        return validate_dwi_sources(candidates)
    except ValueError as exc:
        raise ValueError('{}; acquisition candidates: {}'.format(
            exc, ', '.join(f['name'] for f in files))) from exc
