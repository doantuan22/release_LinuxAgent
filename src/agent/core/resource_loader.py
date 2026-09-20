"""Read packaged defaults without depending on the source checkout or CWD."""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

_RESOURCE_PATHS = {
    "providers.json": ("providers.json",),
    "default_docs_seed.json": ("default_docs_seed.json",),
    "default.db": ("rag", "default.db"),
    "resource_manifest.json": ("resource_manifest.json",),
}


def _resource(name: str):
    try:
        parts = _RESOURCE_PATHS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown packaged resource: {name}") from exc
    return resources.files("agent.resources").joinpath(*parts)


def resource_exists(name: str) -> bool:
    """Check only known bundled resources; never resolve arbitrary paths."""
    return _resource(name).is_file()


@contextmanager
def get_resource(name: str) -> Iterator[Path]:
    """Yield a temporary real path, valid only inside the ``with`` block."""
    resource = _resource(name)
    if not resource.is_file():
        raise FileNotFoundError(f"Packaged resource missing: {name}")
    with resources.as_file(resource) as path:
        yield path


def copy_resource(name: str, destination: str | Path) -> bool:
    """Copy once into XDG; return False when user data already exists."""
    target = Path(destination)
    if target.exists():
        return False
    with get_resource(name) as source:
        try:
            with source.open("rb") as input_file, target.open("xb") as output_file:
                shutil.copyfileobj(input_file, output_file)
        except FileExistsError:
            return False  # Another process won the create-if-absent race.
    return True
