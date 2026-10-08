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
from .llm import ClientFactory
from .messages import Moment, ProducerReply, SpecialistReply
from .producer import Producer, producer_instructions
from .specialists import SPECIALISTS, instructions


class Dispatcher(Executor):
    @handler
    async def dispatch(self, moment: Moment, ctx: WorkflowContext[Moment]) -> None:
        await ctx.send_message(moment)


def build_workflow(world: World, describer: Describer, clients: ClientFactory) -> Workflow:
    specialists = [
        cls(Agent(clients(cls.name), instructions(cls.ROLE), name=cls.name,
                  default_options={"response_format": SpecialistReply, "temperature": 0.6}),
            world, describer)
        for cls in SPECIALISTS
    ]
    producer = Producer(
        Agent(clients("producer"), producer_instructions(), name="producer",
              default_options={"response_format": ProducerReply, "temperature": 0.3}),
        describer,
    )
    dispatcher = Dispatcher(id="dispatcher")
    return (
        WorkflowBuilder(start_executor=dispatcher, name="kickoff-commentary")
        .add_fan_out_edges(dispatcher, specialists)
        .add_fan_in_edges(specialists, producer)
        .build()
    )
