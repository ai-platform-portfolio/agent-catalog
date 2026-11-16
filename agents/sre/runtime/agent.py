"""Bind agent intent to the shared runtime, including its required gateway boundary."""
import os
from pathlib import Path

from portfolio_agent_runtime.agent import load_config as load_shared_config, model_client as shared_model_client, run


def load_config():
    return load_shared_config(Path(__file__).resolve().parents[1] / 'config/agent.yaml')


def model_client(credential, *args, **kwargs):
    # Both hosted execution and model-only evaluations use this composition point.
    if load_config().get('gatewayAccess') and os.getenv('MODEL_CONNECTION_MODE') != 'gateway':
        raise ValueError('Declared gateway intent requires an explicit gateway connection')
    return shared_model_client(credential, *args, **kwargs)
