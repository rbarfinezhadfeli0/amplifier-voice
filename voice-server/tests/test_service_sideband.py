"""Tests for sideband registry and /voice/sideband + /voice/end endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import voice_server.service as service_module
from voice_server.service import service_init


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_test_app() -> FastAPI:
    """Create a minimal FastAPI app with service endpoints registered."""
    app = FastAPI()
    handlers: list = []

    def register_handler(handler):
        handlers.append(handler)

    service_init(app, register_handler)
    return app


# Single shared app — endpoints are registered once at module load time.
_APP = make_test_app()


# ---------------------------------------------------------------------------
# Tests: sideband registry
# ---------------------------------------------------------------------------


class TestSidebandRegistry:
    def test_registry_is_importable_and_is_dict(self):
        """_sideband_registry exists in service module and is a dict."""
        from voice_server.service import _sideband_registry

        assert isinstance(_sideband_registry, dict)


# ---------------------------------------------------------------------------
# Tests: POST /voice/sideband
# ---------------------------------------------------------------------------


class TestVoiceSidebandEndpoint:
    """Tests for the POST /voice/sideband endpoint."""

    def test_returns_400_when_call_id_missing(self):
        """Missing call_id field → 400."""
        with patch.object(service_module, "_amplifier_bridge", MagicMock()):
            client = TestClient(_APP, raise_server_exceptions=True)
            resp = client.post("/voice/sideband", json={"ephemeral_key": "ek_test"})
        assert resp.status_code == 400

    def test_returns_400_when_ephemeral_key_missing(self):
        """Missing ephemeral_key field → 400."""
        with patch.object(service_module, "_amplifier_bridge", MagicMock()):
            client = TestClient(_APP, raise_server_exceptions=True)
            resp = client.post("/voice/sideband", json={"call_id": "call_123"})
        assert resp.status_code == 400

    def test_returns_400_when_body_is_empty(self):
        """Empty body (both fields missing) → 400."""
        with patch.object(service_module, "_amplifier_bridge", MagicMock()):
            client = TestClient(_APP, raise_server_exceptions=True)
            resp = client.post("/voice/sideband", json={})
        assert resp.status_code == 400

    def test_returns_503_when_bridge_not_initialized(self):
        """Returns 503 when _amplifier_bridge is None."""
        with patch.object(service_module, "_amplifier_bridge", None):
            client = TestClient(_APP, raise_server_exceptions=True)
            resp = client.post(
                "/voice/sideband",
                json={"call_id": "call_123", "ephemeral_key": "ek_test"},
            )
        assert resp.status_code == 503

    def test_returns_connected_status_on_success(self):
        """Returns {status: connected, call_id: ...} when sideband connects."""
        mock_sideband = AsyncMock()
        mock_sideband.is_connected = True

        with patch.object(service_module, "_amplifier_bridge", MagicMock()):
            with patch(
                "voice_server.sideband.VoiceSideband", return_value=mock_sideband
            ):
                with patch.dict(service_module._sideband_registry, {}, clear=True):
                    client = TestClient(_APP, raise_server_exceptions=True)
                    resp = client.post(
                        "/voice/sideband",
                        json={"call_id": "call_abc", "ephemeral_key": "ek_test"},
                    )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "connected"
        assert data["call_id"] == "call_abc"

    def test_stores_sideband_in_registry_on_connect(self):
        """Sideband instance is stored in registry keyed by call_id."""
        mock_sideband = AsyncMock()
        mock_sideband.is_connected = True

        with patch.object(service_module, "_amplifier_bridge", MagicMock()):
            with patch(
                "voice_server.sideband.VoiceSideband", return_value=mock_sideband
            ):
                with patch.dict(service_module._sideband_registry, {}, clear=True):
                    client = TestClient(_APP, raise_server_exceptions=True)
                    client.post(
                        "/voice/sideband",
                        json={"call_id": "call_stored", "ephemeral_key": "ek_test"},
                    )
                    # Check while patch.dict is still active
                    assert "call_stored" in service_module._sideband_registry

    def test_returns_already_connected_when_in_registry(self):
        """Returns {status: already_connected} when call_id already connected."""
        existing = MagicMock()
        existing.is_connected = True

        with patch.object(service_module, "_amplifier_bridge", MagicMock()):
            with patch.dict(
                service_module._sideband_registry,
                {"call_xyz": existing},
                clear=True,
            ):
                client = TestClient(_APP, raise_server_exceptions=True)
                resp = client.post(
                    "/voice/sideband",
                    json={"call_id": "call_xyz", "ephemeral_key": "ek_test"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "already_connected"
        assert data["call_id"] == "call_xyz"

    def test_returns_502_when_connect_leaves_disconnected(self):
        """Returns 502 when connect() runs but is_connected remains False."""
        mock_sideband = AsyncMock()
        mock_sideband.is_connected = False  # connect() didn't succeed

        with patch.object(service_module, "_amplifier_bridge", MagicMock()):
            with patch(
                "voice_server.sideband.VoiceSideband", return_value=mock_sideband
            ):
                with patch.dict(service_module._sideband_registry, {}, clear=True):
                    client = TestClient(_APP, raise_server_exceptions=False)
                    resp = client.post(
                        "/voice/sideband",
                        json={"call_id": "call_fail", "ephemeral_key": "ek_test"},
                    )

        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Tests: POST /voice/end
# ---------------------------------------------------------------------------


class TestVoiceEndEndpoint:
    """Tests for the POST /voice/end endpoint."""

    def test_returns_not_found_when_call_id_absent(self):
        """Returns {status: not_found} when call_id is not in registry."""
        with patch.dict(service_module._sideband_registry, {}, clear=True):
            client = TestClient(_APP, raise_server_exceptions=True)
            resp = client.post("/voice/end", json={"call_id": "call_unknown"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"

    def test_returns_disconnected_when_call_id_present(self):
        """Returns {status: disconnected} when call_id is found and ended."""
        mock_sideband = AsyncMock()

        with patch.dict(
            service_module._sideband_registry,
            {"call_to_end": mock_sideband},
            clear=True,
        ):
            client = TestClient(_APP, raise_server_exceptions=True)
            resp = client.post("/voice/end", json={"call_id": "call_to_end"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "disconnected"

    def test_calls_disconnect_on_sideband(self):
        """disconnect() is awaited on the sideband when ending a call."""
        mock_sideband = AsyncMock()

        with patch.dict(
            service_module._sideband_registry,
            {"call_to_end": mock_sideband},
            clear=True,
        ):
            client = TestClient(_APP, raise_server_exceptions=True)
            client.post("/voice/end", json={"call_id": "call_to_end"})

        mock_sideband.disconnect.assert_awaited_once()

    def test_removes_call_id_from_registry(self):
        """call_id is removed from registry after successful /voice/end."""
        mock_sideband = AsyncMock()

        with patch.dict(
            service_module._sideband_registry,
            {"call_to_remove": mock_sideband},
            clear=True,
        ):
            client = TestClient(_APP, raise_server_exceptions=True)
            client.post("/voice/end", json={"call_id": "call_to_remove"})
            # Check inside the patch.dict context while it's active
            assert "call_to_remove" not in service_module._sideband_registry
