from abc import ABC, abstractmethod
import numpy as np


class RoutingStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def select_agent(self, context, available_agents: list[str]) -> str: ...

    @abstractmethod
    def update(self, context, agent: str, reward: float) -> None: ...

    def get_scores(self) -> dict:
        return {}

    def save_state(self, path: str) -> None:
        pass

    def load_state(self, path: str) -> None:
        pass
