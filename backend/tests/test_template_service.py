"""Tests for template_service — stack scaffolding loading and substitution."""
import pytest

from app.services.template_service import load_stack_templates, slugify

_EXPECTED_PATHS = [
    "backend/requirements.txt",
    "backend/Dockerfile",
    "backend/app/__init__.py",
    "backend/app/routes/__init__.py",
    "frontend/package.json",
    "frontend/Dockerfile",
    "frontend/vite.config.js",
    "frontend/tailwind.config.js",
    "frontend/postcss.config.js",
    "frontend/index.html",
    "frontend/src/main.jsx",
    "frontend/src/index.css",
    "frontend/src/lib/utils.js",
    "frontend/src/components/ui/button.jsx",
    "frontend/src/components/ui/card.jsx",
    "frontend/src/components/ui/input.jsx",
    "frontend/src/components/ui/label.jsx",
    "frontend/src/components/ui/textarea.jsx",
    "frontend/src/components/ui/badge.jsx",
    "frontend/src/components/ui/alert.jsx",
    "frontend/src/components/ui/avatar.jsx",
    "frontend/src/components/ui/separator.jsx",
    "frontend/src/components/ui/scroll-area.jsx",
    "frontend/src/components/ui/skeleton.jsx",
    "frontend/src/components/ui/tooltip.jsx",
    "docker-compose.yml",
    ".env.example",
    "SETUP.md",
    "setup.sh",
    "start.sh",
    "setup.bat",
    "start.bat",
]


def test_load_python_postgres_templates_returns_all_expected_files():
    templates = load_stack_templates("python-postgres", "MyApp", "my-app")
    for path in _EXPECTED_PATHS:
        assert path in templates, f"Missing expected scaffolding file: {path}"


def test_total_file_count():
    templates = load_stack_templates("python-postgres", "MyApp", "my-app")
    assert len(templates) == len(_EXPECTED_PATHS), (
        f"Expected {len(_EXPECTED_PATHS)} files, got {len(templates)}: "
        f"{sorted(templates.keys())}"
    )


def test_app_name_substituted_in_index_html():
    templates = load_stack_templates("python-postgres", "My Test App", "my-test-app")
    assert "My Test App" in templates["frontend/index.html"]


def test_app_slug_substituted_in_package_json():
    templates = load_stack_templates("python-postgres", "My Test App", "my-test-app")
    assert "my-test-app" in templates["frontend/package.json"]


def test_app_name_substituted_in_setup_md():
    templates = load_stack_templates("python-postgres", "My Test App", "my-test-app")
    assert "My Test App" in templates["SETUP.md"]


def test_app_slug_substituted_in_env_example():
    templates = load_stack_templates("python-postgres", "My Test App", "my-test-app")
    assert "my-test-app" in templates[".env.example"]


def test_no_unresolved_placeholders_after_substitution():
    templates = load_stack_templates("python-postgres", "My App", "my-app")
    for path, content in templates.items():
        assert "{{app_name}}" not in content, f"Unresolved {{{{app_name}}}} in {path}"
        assert "{{app_slug}}" not in content, f"Unresolved {{{{app_slug}}}} in {path}"


def test_unknown_stack_raises():
    with pytest.raises(ValueError, match="Unknown stack"):
        load_stack_templates("nonexistent-stack", "App", "app")


def test_slugify_handles_spaces():
    assert slugify("Collaborative Notes") == "collaborative-notes"


def test_slugify_handles_special_chars():
    assert slugify("My App!") == "my-app"


def test_slugify_handles_numbers():
    assert slugify("App 2.0") == "app-2-0"


def test_slugify_empty_fallback():
    assert slugify("!!!") == "app"


def test_requirements_txt_has_fastapi():
    templates = load_stack_templates("python-postgres", "App", "app")
    assert "fastapi" in templates["backend/requirements.txt"]


def test_backend_dockerfile_uses_python_312():
    templates = load_stack_templates("python-postgres", "App", "app")
    assert "python:3.12" in templates["backend/Dockerfile"]


def test_requirements_txt_uses_psycopg2_binary_2_9_10():
    templates = load_stack_templates("python-postgres", "App", "app")
    assert "psycopg2-binary==2.9.10" in templates["backend/requirements.txt"]


def test_template_includes_setup_scripts():
    templates = load_stack_templates("python-postgres", "App", "app")
    for script in ("setup.sh", "start.sh", "setup.bat", "start.bat"):
        assert script in templates, f"Missing setup script: {script}"


def test_setup_sh_substitutes_app_name_placeholder():
    templates = load_stack_templates("python-postgres", "MyGreatApp", "my-great-app")
    assert "MyGreatApp" in templates["setup.sh"]
    assert "{{app_name}}" not in templates["setup.sh"]
