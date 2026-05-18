"""ZIP packaging for generated projects (FR13)."""
import io
import zipfile


def package_project(generated_files: dict[str, str]) -> bytes:
    """Zip generated files in memory, preserving subdirectory structure.

    Returns raw ZIP bytes suitable for upload or download.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path, content in sorted(generated_files.items()):
            zf.writestr(file_path, content)
    return buf.getvalue()
