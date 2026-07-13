"""Minimal vendored replacement for heudiconv.utils.load_heuristic.

fw-heudiconv only used heudiconv for this one helper (loading a heuristic .py
file as a module). Vendoring it drops the entire heavy classic-heudiconv
dependency tree (datalad/nipype/dcm2niix) that made the package un-installable
on a modern stack.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def load_heuristic(heuristic_path):
    path = Path(heuristic_path)
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
