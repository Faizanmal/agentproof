from typing import Literal

from pydantic import BaseModel, ConfigDict


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(Contract):
    status: Literal["alive", "ready"]


class Version(Contract):
    version: str
    api_version: Literal["v1"] = "v1"


class Dependencies(Contract):
    postgres: Literal["up", "down"]
    redis: Literal["up", "down"]


class Ready(Health):
    dependencies: Dependencies


class Problem(Contract):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    request_id: str
    dependencies: Dependencies | None = None
