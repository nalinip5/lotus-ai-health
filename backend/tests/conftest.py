import pytest_asyncio
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from lotus.config import Settings
from lotus.container import Container
from lotus.llm.mock import MockLLM
from lotus.sim.generator import Generator


class CountingLLM(MockLLM):
    def __init__(self):
        super().__init__()
        self.calls: list[str] = []

    async def _complete(self, system, user, schema, task, context):
        self.calls.append(task)
        return await super()._complete(system, user, schema, task, context)


def make_settings(tmp_path) -> Settings:
    s = Settings()
    s.data_dir = tmp_path
    s.simulator_enabled = False
    return s


@pytest_asyncio.fixture
async def env(tmp_path):
    s = make_settings(tmp_path)
    llm = CountingLLM()
    c = Container(s, llm=llm)
    async with AsyncSqliteSaver.from_conn_string(str(s.checkpoint_path)) as cp:
        c.attach_checkpointer(cp)
        yield c, Generator(seed=1), llm
