"""DWI source identity shared by curation and export (no SDK calls)."""

DERIVATIVE_IMAGE_TYPES = frozenset(
    ('ADC', 'EADC', 'TRACEW', 'FA', 'COLFA', 'TENSOR', 'TENSOR_B0', 'EXP'))


def split_image_name(name):
    """Return the source stem and imaging extension, including compound .nii.gz."""
    for extension in ('.nii.gz', '.nii', '.bval', '.bvec'):
        if name.endswith(extension):
            return name[:-len(extension)], extension
    return name, ''


def is_dwi_derivative(f):
    """Recognize positive converter metadata for derived maps and SBRef images.

    Only positive per-file converter metadata counts: the DICOM ``ImageType``
    the converter copied into the file's ``info`` must mark the file ``DERIVED``
    and name a known map role. A diffusion ``ORIGINAL`` with a SeriesDescription
    ending in ``_SBRef`` is a converter-recognized reference. Absent metadata, an unknown
    role, or a contradictory ``ORIGINAL``/``DERIVED`` pair leaves the file a
    candidate for the raw image. Filenames and missing gradients never imply a
    derivative.
    """
    info = f.get('info') or {}
    image_type = info.get('ImageType')
    if not isinstance(image_type, (list, tuple)):
        return False
    tokens = {str(token).strip().upper() for token in image_type}
    if {'DERIVED', 'ORIGINAL'} <= tokens:
        return False
    roles = tokens & DERIVATIVE_IMAGE_TYPES
    description = info.get('SeriesDescription')
    reference = isinstance(description, str) and description.upper().endswith('_SBREF')
    if roles:
        return len(roles) == 1 and 'DERIVED' in tokens and not reference
    return reference and {'ORIGINAL', 'DIFFUSION'} <= tokens


def validate_dwi_sources(files):
    """Require one named conversion set with coherent job provenance.

    Matching source stems are the converter's sidecar association, unlike BIDS
    destinations (assigned later), upload times or equal gradient counts. Stems
    alone do not establish generation, so every member must also carry the same
    conversion job ID. A user or device ID is not a conversion ID, and an
    absent origin is not provenance.
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
    jobs = {o.get('id') if o.get('type') == 'job' else None for o in origins}
    if len(jobs) != 1 or not next(iter(jobs)):
        raise ValueError('Ambiguous DWI pairing: missing or conflicting conversion '
                         'job provenance; inspect conversion outputs: ' + evidence)
    return ordered


def select_dwi_files(files):
    """Select the newest raw image and its source sidecars, never extension-wise newest.

    Converter-declared derivatives never compete to be the raw image. An
    acquisition containing only known derivatives has no raw DWI candidate.
    """
    niftis = [f for f in files if split_image_name(f['name'])[1] in ('.nii', '.nii.gz')]
    if not niftis:
        if any(split_image_name(f['name'])[1] in ('.bval', '.bvec') for f in files):
            validate_dwi_sources(files)  # Report orphan gradients with filenames.
        return []
    images = [f for f in niftis if not is_dwi_derivative(f)]
    if not images:
        raise ValueError('No raw DWI candidate: all images have known converter '
                         'derivative/reference roles; inspect outputs: '
                         + ', '.join(f['name'] for f in niftis))
    if len(images) > 1:
        dates = [f.get('created') for f in images]
        if not all(dates) or dates.count(max(dates)) != 1:
            raise ValueError('Ambiguous DWI image selection: missing or tied creation '
                             'times; inspect conversion outputs: '
                             + ', '.join(f['name'] for f in images))
    image = max(images, key=lambda f: f.get('created') or '')
    stem = split_image_name(image['name'])[0]
    candidates = [image] + [
        f for f in files if split_image_name(f['name'])[0] == stem
        and split_image_name(f['name'])[1] in ('.bval', '.bvec')]
    try:
        return validate_dwi_sources(candidates)
    except ValueError as exc:
        raise ValueError('{}; acquisition candidates: {}'.format(
            exc, ', '.join(f['name'] for f in files))) from exc
