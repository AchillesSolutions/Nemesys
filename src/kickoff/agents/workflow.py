"""Wires the agents into an Agent Framework workflow.

    Moment ──► dispatcher ─┬─► teacher ──────┐
                           ├─► storyteller ──┼─► producer ──► Decision
                           └─► league_guide ─┘

The specialists run concurrently (fan-out). The Producer runs once all
three have answered (fan-in) and sees every proposal together.
"""

from __future__ import annotations

from agent_framework import Agent, Executor, Workflow, WorkflowBuilder, WorkflowContext, handler

from ..models import World
from .context import Describer
from .llm import ClientFactory, options_for
from .messages import Moment, ProducerReply, SpecialistReply
from .producer import Producer, producer_instructions
from .specialists import SPECIALISTS, instructions


class Dispatcher(Executor):
    @handler
    async def dispatch(self, moment: Moment, ctx: WorkflowContext[Moment]) -> None:
        await ctx.send_message(moment)


def build_workflow(world: World, describer: Describer, clients: ClientFactory) -> Workflow:
    def agent(role: str, prompt: str, options: dict) -> Agent:
        client = clients(role)  # each role can run on its own model (see llm.py)
        return Agent(client, prompt, name=role, default_options=options_for(client, options))

    specialists = [
        cls(agent(cls.name, instructions(cls.ROLE), {"response_format": SpecialistReply, "temperature": 0.6}),
            world, describer)
        for cls in SPECIALISTS
    ]
    producer = Producer(agent("producer", producer_instructions(),
                              {"response_format": ProducerReply, "temperature": 0.3}), describer)
    dispatcher = Dispatcher(id="dispatcher")
    return (
        WorkflowBuilder(start_executor=dispatcher, name="kickoff-commentary")
        .add_fan_out_edges(dispatcher, specialists)
        .add_fan_in_edges(specialists, producer)
        .build()
    )
