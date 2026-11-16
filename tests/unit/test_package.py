"""Package-level smoke tests: the scaffold imports and declares a version."""

from __future__ import annotations

import persdocbench


def test_version_is_declared() -> None:
    assert persdocbench.__version__


def test_stage_packages_are_importable() -> None:
    stage_names = [
        "common",
        "schemas",
        "content",
        "rendering",
        "augmentation",
        "annotation",
        "studio",
        "evaluation",
        "adapters",
        "training",
    ]
    for name in stage_names:
        module = __import__(f"persdocbench.{name}", fromlist=[name])
        assert module.__doc__, f"persdocbench.{name} is missing a module docstring"
