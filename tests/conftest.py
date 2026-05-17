from pathlib import Path

import pytest

from cai_docs.ingest import discover
from cai_docs.models import RawFile

FIXTURES = Path(__file__).parent / "fixtures"
REAL = FIXTURES / "real"
SYNTHETIC = FIXTURES / "synthetic"

REAL_CREATE = REAL / "createMultipleIdentifier.PROCESS.xml"
REAL_RETRIEVE = REAL / "retrieveconsents.PROCESS.xml"


def load_raw(path: Path) -> RawFile:
    return RawFile(
        relpath=path.name,
        abs_path=path,
        ext=path.suffix.lstrip(".").lower(),
        data=path.read_bytes(),
    )


@pytest.fixture
def real_create() -> RawFile:
    return load_raw(REAL_CREATE)


@pytest.fixture
def real_retrieve() -> RawFile:
    return load_raw(REAL_RETRIEVE)


@pytest.fixture
def all_fixture_files() -> list[RawFile]:
    """Every fixture (real + synthetic) as RawFiles, like a discovered export."""
    files: list[RawFile] = []
    for p in sorted(FIXTURES.rglob("*")):
        if p.is_file():
            files.append(
                RawFile(
                    relpath=p.relative_to(FIXTURES).as_posix(),
                    abs_path=p,
                    ext=p.suffix.lstrip(".").lower(),
                    data=p.read_bytes(),
                )
            )
    return files


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def synthetic_dir() -> Path:
    return SYNTHETIC


@pytest.fixture
def real_dir() -> Path:
    return REAL


@pytest.fixture
def raw_loader():
    """Returns load_raw(path) so tests can pull arbitrary fixtures by name."""
    return load_raw
