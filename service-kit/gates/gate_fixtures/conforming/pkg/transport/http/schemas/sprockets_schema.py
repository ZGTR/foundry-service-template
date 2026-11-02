from pydantic import BaseModel


class SprocketPayload(BaseModel):
    gadget_id: str
    tenant_id: str
    label: str
