"""logintel: a read-only agent that answers questions about logs and cites the real lines."""

from .agent import Agent, Answer
from .config import Config

__version__ = "0.1.0"
__all__ = ["Agent", "Answer", "Config"]
