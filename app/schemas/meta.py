from pydantic import BaseModel

class OptionItem(BaseModel):
    value: str
    label: str
    default: bool | None = None

class MetaOptions(BaseModel):
    purposes: list[OptionItem]
    categories: list[OptionItem]
    transport: list[OptionItem]
    crowd_mode: list[OptionItem]
