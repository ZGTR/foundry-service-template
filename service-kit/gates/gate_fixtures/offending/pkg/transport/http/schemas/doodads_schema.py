from pydantic import BaseModel


class DoodadPayload(BaseModel):
    gadget_id: str
    tenant_id: str
    label: str
