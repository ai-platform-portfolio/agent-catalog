"""Bind this agent's intent file to the unchanged shared runtime."""
from pathlib import Path

from portfolio_agent_runtime.agent import load_config as load_shared_config, model_client, run


def load_config():
    return load_shared_config(Path(__file__).resolve().parents[1] / 'config/agent.yaml')
