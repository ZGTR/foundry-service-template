import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(database_path=os.environ.get("DATABASE_PATH", "gadgets.db"))
