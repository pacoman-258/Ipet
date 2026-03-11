from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import img2pdf
from jmcomic import JmOption, create_option_by_file, download_album
from mcp.server.fastmcp import FastMCP


SERVER_NAME = "jmmcp"
DEFAULT_SAVE_DIR = Path("downloads/pdf")
DEFAULT_TEMP_ROOT = Path(".tmp")
DEFAULT_IMPL_FALLBACKS = ("api", "html")

mcp = FastMCP(SERVER_NAME)


def _read_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except Exception:
        return default
    return max(minimum, value)


def _read_impl_fallbacks() -> tuple[str, ...]:
    raw = os.getenv("JM_IMPL_FALLBACKS", "")
    values = [item.strip().lower() for item in raw.split(",") if item.strip()]
    allowed = {"api", "html"}
    ordered: list[str] = []
    for item in values or list(DEFAULT_IMPL_FALLBACKS):
        if item in allowed and item not in ordered:
            ordered.append(item)
    return tuple(ordered) if ordered else DEFAULT_IMPL_FALLBACKS


def _normalize_album_id(album_id: Any) -> tuple[str | None, str | None]:
    value = str(album_id).strip()
    if not value:
        return None, "album_id must not be empty"
    if not value.isdigit():
        return None, "album_id must be a numeric string"
    return value, None


def _resolve_output_dir(save_dir: str | None) -> Path:
    base = Path(save_dir) if save_dir else DEFAULT_SAVE_DIR
    return base.expanduser().resolve()


def _allocate_pdf_path(output_dir: Path, album_id: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    candidate = output_dir / f"{album_id}.pdf"
    if not candidate.exists():
        return candidate

    index = 1
    while True:
        candidate = output_dir / f"{album_id} ({index}).pdf"
        if not candidate.exists():
            return candidate
        index += 1


def _build_option(job_dir: Path) -> JmOption:
    option_path = os.getenv("JM_OPTION_PATH")
    if option_path and Path(option_path).expanduser().is_file():
        option_dict = create_option_by_file(str(Path(option_path).expanduser())).deconstruct()
    else:
        option_dict = JmOption.default().deconstruct()

    option_dict["log"] = False
    option_dict["dir_rule"] = {
        "rule": "Bd_Aid_Pid",
        "base_dir": str(job_dir),
    }
    option_dict.setdefault("download", {})
    option_dict["download"]["threading"] = {
        "photo": _read_int_env("JM_PHOTO_THREADS", 1),
        "image": _read_int_env("JM_IMAGE_THREADS", 4),
    }
    option_dict.setdefault("client", {})
    option_dict["client"]["retry_times"] = _read_int_env("JM_RETRY_TIMES", 5)
    option_dict.setdefault("plugins", {}).pop("after_album", None)

    return JmOption.construct(option_dict)


def _build_impl_attempts(base_option: JmOption) -> list[tuple[str, JmOption]]:
    option_dict = base_option.deconstruct()
    client_cfg = option_dict.setdefault("client", {})
    current_impl = str(client_cfg.get("impl") or "").strip().lower()
    attempts: list[str] = []

    if current_impl in {"api", "html"}:
        attempts.append(current_impl)

    for impl in _read_impl_fallbacks():
        if impl not in attempts:
            attempts.append(impl)

    if not attempts:
        attempts.append("api")

    built: list[tuple[str, JmOption]] = []
    for impl in attempts:
        attempt_dict = json.loads(json.dumps(option_dict))
        attempt_dict.setdefault("client", {})
        attempt_dict["client"]["impl"] = impl
        built.append((impl, JmOption.construct(attempt_dict)))
    return built


def _download_album_with_impl_fallback(album_id: str, base_option: JmOption) -> tuple[str, list[str]]:
    errors: list[str] = []
    for impl, option in _build_impl_attempts(base_option):
        try:
            download_album(album_id, option=option, check_exception=True)
            return impl, errors
        except Exception as exc:
            errors.append(f"{impl}: {exc}")
    raise RuntimeError("all impl attempts failed: " + " | ".join(errors))


def _collect_downloaded_images(job_dir: Path) -> list[Path]:
    images: list[Path] = []
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    for path in sorted(job_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in allowed_suffixes:
            images.append(path)
    return images


def _generate_pdf(job_dir: Path, output_path: Path) -> None:
    image_paths = _collect_downloaded_images(job_dir)
    if not image_paths:
        raise RuntimeError("download finished but no images were found")
    with output_path.open("wb") as handle:
        handle.write(img2pdf.convert([str(path) for path in image_paths]))


@mcp.tool(
    name="download_jm_album_pdf",
    description=(
        "Download a JM album and convert it to a PDF file. "
        "Example: when user says 'Download album id 350234', "
        "extract album_id=350234 and call this tool."
    ),
)
def download_jm_album_pdf(album_id: str, save_dir: str | None = None) -> dict[str, Any]:
    normalized_id, error = _normalize_album_id(album_id)
    if error:
        return {
            "ok": False,
            "album_id": str(album_id),
            "pdf_path": None,
            "file_size_bytes": None,
            "filename": None,
            "error": error,
            "suggestion": "Use a numeric string, for example: 350234",
        }

    target_dir = _resolve_output_dir(save_dir)

    try:
        temp_root = DEFAULT_TEMP_ROOT.expanduser().resolve()
        temp_root.mkdir(parents=True, exist_ok=True)

        job_dir = (temp_root / f"jm_{normalized_id}_{uuid4().hex}").resolve()
        job_dir.mkdir(parents=True, exist_ok=False)

        try:
            option = _build_option(job_dir)
            final_pdf_path = _allocate_pdf_path(target_dir, normalized_id)
            used_impl, fallback_errors = _download_album_with_impl_fallback(normalized_id, option)
            _generate_pdf(job_dir, final_pdf_path)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

        final_pdf_path = final_pdf_path.resolve()
        return {
            "ok": True,
            "album_id": normalized_id,
            "pdf_path": str(final_pdf_path),
            "file_size_bytes": final_pdf_path.stat().st_size,
            "filename": final_pdf_path.name,
            "error": None,
            "suggestion": None,
            "impl": used_impl,
            "impl_fallback_errors": fallback_errors,
        }

    except Exception as exc:
        return {
            "ok": False,
            "album_id": normalized_id,
            "pdf_path": None,
            "file_size_bytes": None,
            "filename": None,
            "error": str(exc),
            "impl": None,
            "impl_fallback_errors": None,
            "suggestion": (
                "If anonymous access is blocked, set JM_OPTION_PATH to a jmcomic "
                "option file with cookies/domain/proxy and retry."
            ),
        }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
