"""Test setup for the PTV Transport integration."""
import os
import sys

import pytest

# Load this repo's custom_components before Home Assistant adds its own
# testing_config/custom_components to the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import custom_components.ptv_transport  # noqa: E402,F401

pytest_plugins = "pytest_homeassistant_custom_component"

if sys.platform == "win32":
    # asyncio's Windows event loop needs a local socket pair, which the Home
    # Assistant test plugin blocks. The tests never touch the network.
    import pytest_socket

    pytest_socket.disable_socket = lambda *a, **k: None
    pytest_socket.socket_allow_hosts = lambda *a, **k: None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
