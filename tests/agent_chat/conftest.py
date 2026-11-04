import pytest

from tests.support.agent_chat_product import empty_tables


@pytest.fixture
def engine(worker_engine):
    """This worker's real PostgreSQL; the template's and stand-in product's tables empty."""
    empty_tables(worker_engine)
    return worker_engine
