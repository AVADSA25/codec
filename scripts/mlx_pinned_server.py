"""Start mlx_vlm.server with its model pinned (docs/MODEL-MEMORY-GUARD-DESIGN.md, option B).

mlx_vlm.server loads whatever model a request names. A request naming another model
unloads the loaded one and loads (or downloads) the other in place, and that memory is
not given back: on 17 Nov 2026 eight such swaps took the server from 20 GB to 88 GB.
This wrapper makes the server refuse those requests with HTTP 409 instead. The model it
serves is the --model the launcher passes; every other argument goes to mlx_vlm as is.

It relies on mlx_vlm 0.6.13 internals: the routes reach get_cached_model through the
mlx_vlm.server package attribute (app._protocol_deps). It proves that at start with a
probe that loads nothing. If the probe fails (a newer mlx_vlm), it runs mlx_vlm unpinned
and says so on stderr: refusing to start would leave CODEC with no local model.

Run by scripts/start_model_server.sh with the model server's own Python. Standard
library only, apart from mlx_vlm and the FastAPI it brings.
"""
import sys

# Model kinds the pinned model may be asked for. Any other kind (image, audio, embedding)
# would load a second model, or a second copy of this one, in another cache group.
ALLOWED_KINDS = ("auto", "text_generation")
REFUSAL = ("This server holds one model and does not load another. Ask for the model "
           "it serves (GET /v1/models).")
_PROBE_ID = "codec-pin-probe"
_PROTOCOL_MODULES = ("openai", "anthropic", "audio")


def model_arg(argv):
    """The value of --model in an argv list, or None."""
    for i, arg in enumerate(argv):
        if arg == "--model" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--model="):
            return arg.split("=", 1)[1]
    return None


def make_pinned(original, pinned, http_error):
    """get_cached_model that serves only `pinned` as a text model and refuses the rest."""
    def pinned_get_cached_model(model_path, *args, **kwargs):
        if model_path != pinned or kwargs.get("model_kind", "auto") not in ALLOWED_KINDS:
            raise http_error(status_code=409, detail=REFUSAL)
        return original(model_path, *args, **kwargs)
    pinned_get_cached_model.codec_pinned = True
    return pinned_get_cached_model


def install(package, app_module, pinned, http_error, protocol_modules=()):
    """Pin `package.get_cached_model` to `pinned`. Returns "" when the pin is active,
    else a reason code, with the package left exactly as it was."""
    if not pinned:
        return "no_model"
    original = getattr(package, "get_cached_model", None)
    via = getattr(getattr(app_module, "_protocol_deps", None), "get_cached_model", None)
    if not callable(original) or not callable(via):
        return "internals_changed"
    # The routes hold deps.get_cached_model; it must be the lambda that looks the
    # package attribute up on every call, or a patch of the package would not reach them.
    for mod in protocol_modules:
        held = getattr(mod, "get_cached_model", via)
        if held is not via:
            return "internals_changed"
    seen = []

    def probe(*args, **kwargs):
        seen.append(args[:1])
        return None, None, None

    package.get_cached_model = probe
    try:
        via(_PROBE_ID)
    except Exception:
        seen.append("error")
    finally:
        package.get_cached_model = original
    if seen != [(_PROBE_ID,)]:
        return "internals_changed"
    package.get_cached_model = make_pinned(original, pinned, http_error)
    return ""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    reason = "import_failed"
    try:
        import importlib

        import mlx_vlm.server as package
        from fastapi import HTTPException
        # The module, not `mlx_vlm.server.app`'s attribute: the package re-exports the
        # FastAPI object under that name.
        app_module = importlib.import_module("mlx_vlm.server.app")
        mods = []
        for name in _PROTOCOL_MODULES:
            try:
                mods.append(importlib.import_module(f"mlx_vlm.server.{name}"))
            except ImportError:
                pass
        reason = install(package, app_module, model_arg(argv), HTTPException, mods)
    except Exception as e:  # never refuse to start over the pin
        reason = f"import_failed ({type(e).__name__})"
    if reason:
        print(f"mlx_pinned_server: model pin NOT active ({reason}); running mlx_vlm unpinned",
              file=sys.stderr, flush=True)
    else:
        print("mlx_pinned_server: model pinned; a request naming another model gets 409",
              file=sys.stderr, flush=True)
    from mlx_vlm.server.cli import main as cli_main
    sys.argv = ["mlx_vlm.server"] + argv
    cli_main()


if __name__ == "__main__":
    main()
