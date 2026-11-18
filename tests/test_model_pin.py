"""scripts/mlx_pinned_server.py: the model server refuses a request naming another model
(docs/MODEL-MEMORY-GUARD-DESIGN.md, option B). Fake mlx_vlm modules stand in for the real
ones; nothing here imports mlx_vlm or starts a server."""
import importlib.util
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mlx_pinned_server", REPO / "scripts" / "mlx_pinned_server.py")
pin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pin)

PINNED = "mlx-community/Qwen3.6-35B-A3B-4bit"


class HTTPError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _fake_server():
    """A package whose routes reach get_cached_model through the package attribute, as mlx_vlm 0.6.13 does."""
    package = types.ModuleType("fake_mlx_server")
    loads = []

    def get_cached_model(model_path, adapter_path=None, *, model_kind="auto"):
        loads.append((model_path, model_kind))
        return "model", "processor", "config"

    package.get_cached_model = get_cached_model
    app = types.SimpleNamespace(_protocol_deps=types.SimpleNamespace(
        get_cached_model=lambda *a, **k: package.get_cached_model(*a, **k)))
    route = types.SimpleNamespace(get_cached_model=app._protocol_deps.get_cached_model)
    return package, app, route, loads


def test_the_pinned_model_is_served():
    package, app, route, loads = _fake_server()
    assert pin.install(package, app, PINNED, HTTPError, [route]) == ""
    assert route.get_cached_model(PINNED) == ("model", "processor", "config")
    assert loads == [(PINNED, "auto")]


@pytest.mark.parametrize("asked", ["", "local-qwen", "mlx-community/Qwen2.5-7B-Instruct-4bit",
                                   "mlx-community/UI-TARS-1.5-7B-6bit"])
def test_another_model_gets_409_and_nothing_loads(asked):
    package, app, route, loads = _fake_server()
    assert pin.install(package, app, PINNED, HTTPError, [route]) == ""
    with pytest.raises(HTTPError) as err:
        route.get_cached_model(asked)
    assert err.value.status_code == 409
    assert loads == []


def test_the_pinned_model_in_another_kind_is_refused():
    """An embedding or image request naming the pinned id would load a second copy."""
    package, app, route, loads = _fake_server()
    pin.install(package, app, PINNED, HTTPError, [route])
    with pytest.raises(HTTPError):
        route.get_cached_model(PINNED, model_kind="embedding")
    assert route.get_cached_model(PINNED, None, model_kind="text_generation")
    assert loads == [(PINNED, "text_generation")]


def test_changed_internals_leave_mlx_vlm_unpinned():
    package, app, route, loads = _fake_server()
    original = package.get_cached_model
    app._protocol_deps.get_cached_model = original    # routes no longer look the package up
    assert pin.install(package, app, PINNED, HTTPError, []) == "internals_changed"
    assert package.get_cached_model is original


def test_a_route_holding_its_own_copy_leaves_mlx_vlm_unpinned():
    package, app, route, loads = _fake_server()
    original = package.get_cached_model
    route.get_cached_model = original
    assert pin.install(package, app, PINNED, HTTPError, [route]) == "internals_changed"
    assert package.get_cached_model is original


def test_no_model_argument_leaves_it_unpinned():
    package, app, route, loads = _fake_server()
    assert pin.install(package, app, None, HTTPError, [route]) == "no_model"


def test_the_probe_loads_nothing():
    package, app, route, loads = _fake_server()
    pin.install(package, app, PINNED, HTTPError, [route])
    assert loads == []


@pytest.mark.parametrize("argv, model", [
    (["--model", PINNED, "--port", "8083"], PINNED),
    ([f"--model={PINNED}"], PINNED),
    (["--port", "8083"], None),
])
def test_model_arg(argv, model):
    assert pin.model_arg(argv) == model
