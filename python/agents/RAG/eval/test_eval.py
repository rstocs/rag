# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pathlib

import dotenv
import pytest
from google.adk.evaluation.agent_evaluator import AgentEvaluator

pytest_plugins = ("pytest_asyncio",)

DATA_DIR = pathlib.Path(__file__).parent / "data"


@pytest.fixture(scope="session", autouse=True)
def load_env():
    dotenv.load_dotenv()


@pytest.mark.asyncio
async def test_core_conversation():
    """Core happy-path cases: summary, pollutant tables, water cycle, maps, CoC."""
    await AgentEvaluator.evaluate(
        agent_module="rag",
        eval_dataset_file_path_or_dir=str(DATA_DIR / "conversation.test.json"),
        num_runs=5,
    )


@pytest.mark.asyncio
async def test_edge_cases():
    """Edge cases: out-of-scope refusal, unit conversion, missing data, language."""
    await AgentEvaluator.evaluate(
        agent_module="rag",
        eval_dataset_file_path_or_dir=str(DATA_DIR / "conversation_edge.test.json"),
        num_runs=5,
    )
