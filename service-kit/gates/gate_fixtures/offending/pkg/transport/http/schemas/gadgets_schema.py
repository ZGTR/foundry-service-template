from pydantic import BaseModel


class GadgetPayload(BaseModel):
    gadget_id: str
    tenant_id: str
    label: str
