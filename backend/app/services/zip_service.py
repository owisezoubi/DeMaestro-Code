"""ZIP packaging for generated projects (FR13)."""
import io
import zipfile


def package_project(generated_files: dict[str, str]) -> bytes:
    """Zip generated files in memory, preserving subdirectory structure.

    Returns raw ZIP bytes suitable for upload or download.
    Shell scripts (.sh) are written with executable permissions (rwxr-xr-x).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path, content in sorted(generated_files.items()):
            if file_path.endswith(".sh"):
                zinfo = zipfile.ZipInfo(file_path)
                zinfo.external_attr = 0o755 << 16  # rwxr-xr-x
                zf.writestr(zinfo, content)
            else:
                zf.writestr(file_path, content)
    return buf.getvalue()
