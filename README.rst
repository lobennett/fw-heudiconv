====================================================================
=======
FlywheelTools: Software for HeuDiConv-Style BIDS Curation On Flywheel
=====================================================================

.. image:: https://readthedocs.org/projects/fw-heudiconv/badge/?version=latest
  :target: http://fw-heudiconv.readthedocs.io/en/latest/?badge=latest
  :alt: Documentation Status

.. image:: https://circleci.com/gh/PennLINC/fw-heudiconv.svg?style=shield
  :target: https://circleci.com/gh/PennLINC/fw-heudiconv

.. image:: https://zenodo.org/badge/DOI/10.5281/zenodo.4752798.svg?style=shield
  :target: https://zenodo.org/record/4752798#.YJwSt5NKg8N


``FlywheelTools`` is a suite of software tools for curating your data into BIDS on Flywheel. It's comprised of 2 parts:

``fw-heudiconv``, which is a Python-based tool kit for curating BIDS data on the
Flywheel platform, and ``flaudit``, which is a Flywheel project auditor.

Full documentation at `readthedocs <http://fw-heudiconv.readthedocs.io/en/latest>`_

License: BSD-3

Offline tests
=============

GitHub Actions runs the unit tests and package doctests on Linux with Python 3.12.
From the repository root, in a Python 3.12 virtual environment, run::

    python -m pip install .
    python -m pip check
    python -m pytest -v -ra --doctest-modules --cov=fw_heudiconv testing fw_heudiconv --deselect=testing/test_fwheudiconv.py::test_client

These tests use synthetic file records and temporary JSON files for echo/file
selection, timing normalization, sidecar writing, and heuristic examples.
The exact ``test_client`` test is deselected before it constructs a live Flywheel
client. Credentialed Flywheel integration and CLI operations, participant data,
and Sherlock jobs are outside this offline suite.
