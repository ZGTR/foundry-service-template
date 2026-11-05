# GATE_MUST_FLAG_THIS -- clause: check_layering rule 2, FRAMEWORK/SDK IN AN INNER LAYER,
# for the three roots the owner's relaxation must NOT have admitted.
#
# ITS OWN FILE, not three more lines in register_gadget.py. That file is the rule 1
# DIRECTION fixture; folding rule 2 into it would let either clause break while the other
# kept the file red, which is a control that cannot fail for the reason it was written for.
#
# `pydantic_ai` is the sharp one. It shares a PREFIX with the newly allowed `pydantic` and
# is a different package root -- an agent SDK, not a validation library. If the allowance
# is ever rewritten as a prefix or a category test, this line stops being flagged and the
# offending count moves. That is the whole point of naming it here.
from fastapi import APIRouter
from pydantic_ai import Agent
from sqlalchemy import select

from pkg.domain import Gadget


def price(gadget: Gadget, router: APIRouter, agent: Agent) -> str:
    return f"{gadget.label}:{select}:{router}:{agent}"
