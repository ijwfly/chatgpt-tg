import pytest


@pytest.fixture(autouse=True)
def clean_db():
    """Unit tests touch no database — override the root autouse fixture."""
    yield
