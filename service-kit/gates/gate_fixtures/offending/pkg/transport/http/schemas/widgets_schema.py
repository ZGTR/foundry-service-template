from pydantic import BaseModel


class WidgetPayload(BaseModel):
    widget_id: str
