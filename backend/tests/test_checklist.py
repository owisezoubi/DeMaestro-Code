"""Unit tests for checklist schema, validation, and per-item check runners."""
import pytest

from app.pipeline.checklist import (
    ChecklistValidationError,
    ModelItem,
    PageItem,
    RouteItem,
    SeedItem,
    StyleItem,
    coerce_checklist,
    validate_checklist,
)
from app.pipeline.checklist_runner import (
    CheckResult,
    check_model_item,
    check_nav_reachability,
    check_page_item,
    check_route_item,
    check_seed_item,
    check_style_item,
    run_checklist,
)


# ============================================================
# Schema validation tests
# ============================================================

def test_validate_checklist_accepts_valid():
    cl = [
        {
            "id": "model.User",
            "type": "model",
            "table_name": "users",
            "class_name": "User",
            "columns": [{"name": "email", "type": "str", "nullable": False}],
        }
    ]
    validate_checklist(cl)  # must not raise


def test_validate_checklist_rejects_missing_id():
    cl = [
        {
            "type": "model",
            "table_name": "users",
            "class_name": "User",
            "columns": [],
        }
    ]
    with pytest.raises(ChecklistValidationError):
        validate_checklist(cl)


def test_validate_checklist_rejects_empty():
    with pytest.raises(Exception):
        validate_checklist([])


def test_validate_checklist_rejects_unknown_type():
    cl = [{"id": "x.Foo", "type": "widget", "name": "Foo"}]
    with pytest.raises(Exception):
        validate_checklist(cl)


def test_validate_route_item():
    cl = [
        {
            "id": "route.GET.api.menu-items",
            "type": "route",
            "method": "GET",
            "path": "/api/menu-items",
            "auth_required": False,
        }
    ]
    validate_checklist(cl)


def test_validate_page_item():
    cl = [
        {
            "id": "page.MenuPage",
            "type": "page",
            "file_path": "frontend/src/pages/MenuPage.jsx",
            "route": "/menu",
            "requires_auth": False,
        }
    ]
    validate_checklist(cl)


def test_validate_seed_item():
    cl = [
        {
            "id": "seed.menu_items",
            "type": "seed",
            "model": "MenuItem",
            "count": 5,
            "sample_data": [{"name": "Margherita", "price": 12.99}],
        }
    ]
    validate_checklist(cl)


def test_validate_style_item():
    cl = [
        {
            "id": "style.primary_color",
            "type": "style",
            "css_var": "--color-primary",
            "value": "#7C3AED",
        }
    ]
    validate_checklist(cl)


# ============================================================
# Coerce tests
# ============================================================

def test_coerce_model_item():
    raw = [
        {
            "id": "model.User",
            "type": "model",
            "table_name": "users",
            "class_name": "User",
            "columns": [{"name": "email", "type": "str", "nullable": False}],
        }
    ]
    items = coerce_checklist(raw)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ModelItem)
    assert item.class_name == "User"


def test_coerce_skips_unknown_types():
    raw = [
        {"id": "x.Foo", "type": "widget"},
        {
            "id": "model.User",
            "type": "model",
            "table_name": "users",
            "class_name": "User",
            "columns": [],
        },
    ]
    items = coerce_checklist(raw)
    assert len(items) == 1


# ============================================================
# check_model_item tests
# ============================================================

def test_check_model_item_pass():
    item = ModelItem(
        id="model.User",
        type="model",
        table_name="users",
        class_name="User",
        columns=[{"name": "email", "type": "str", "nullable": False}],
    )
    files = {
        "backend/app/models.py": (
            "from app.database import Base\n"
            "from sqlalchemy.orm import Mapped, mapped_column\n"
            "from sqlalchemy import String\n\n"
            "class User(Base):\n"
            "    __tablename__ = 'users'\n"
            "    id: Mapped[int]\n"
            "    email: Mapped[str] = mapped_column(String)\n"
        )
    }
    result = check_model_item(item, files)
    assert result.status == "pass"


def test_check_model_item_fail_missing_column():
    item = ModelItem(
        id="model.User",
        type="model",
        table_name="users",
        class_name="User",
        columns=[{"name": "email", "type": "str", "nullable": False}],
    )
    files = {"backend/app/models.py": "class User(Base):\n    pass\n"}
    result = check_model_item(item, files)
    assert result.status == "fail"
    assert "email" in result.reason


def test_check_model_item_fail_missing_class():
    item = ModelItem(
        id="model.Order",
        type="model",
        table_name="orders",
        class_name="Order",
        columns=[{"name": "total", "type": "float", "nullable": False}],
    )
    files = {"backend/app/models.py": "class User(Base):\n    pass\n"}
    result = check_model_item(item, files)
    assert result.status == "fail"
    assert "Order" in result.reason


def test_check_model_item_fail_missing_models_file():
    item = ModelItem(
        id="model.User",
        type="model",
        table_name="users",
        class_name="User",
        columns=[],
    )
    result = check_model_item(item, {})
    assert result.status == "fail"


def test_check_model_item_pass_old_sqlalchemy_style():
    item = ModelItem(
        id="model.User",
        type="model",
        table_name="users",
        class_name="User",
        columns=[{"name": "email", "type": "str", "nullable": False}],
    )
    files = {
        "backend/app/models.py": (
            "class User(Base):\n"
            "    __tablename__ = 'users'\n"
            "    email = Column(String, unique=True)\n"
        )
    }
    result = check_model_item(item, files)
    assert result.status == "pass"


# ============================================================
# check_route_item tests
# ============================================================

def test_check_route_item_pass():
    item = RouteItem(
        id="route.GET.api.menu-items",
        type="route",
        method="GET",
        path="/api/menu-items",
        auth_required=False,
    )
    openapi = {"/api/menu-items": {"get": {}}}
    result = check_route_item(item, openapi, {})
    assert result.status == "pass"


def test_check_route_item_fail_missing_method():
    item = RouteItem(
        id="route.POST.api.orders",
        type="route",
        method="POST",
        path="/api/orders",
        auth_required=True,
    )
    openapi = {"/api/orders": {"get": {}}}
    result = check_route_item(item, openapi, {})
    assert result.status == "fail"
    assert "POST" in result.reason


def test_check_route_item_fail_missing_path():
    item = RouteItem(
        id="route.GET.api.orders",
        type="route",
        method="GET",
        path="/api/orders",
        auth_required=False,
    )
    openapi = {"/api/menu-items": {"get": {}}}
    result = check_route_item(item, openapi, {})
    assert result.status == "fail"


def test_check_route_item_fallback_to_files():
    item = RouteItem(
        id="route.GET.api.orders",
        type="route",
        method="GET",
        path="/api/orders",
        auth_required=False,
    )
    files = {
        "backend/app/routes/orders.py": (
            "router = APIRouter(prefix='/orders')\n"
            "@router.get('/')\n"
            "def list_orders(): pass\n"
        )
    }
    result = check_route_item(item, {}, files)
    assert result.status == "pass"


# ============================================================
# check_page_item tests
# ============================================================

def test_check_page_item_pass():
    item = PageItem(
        id="page.MenuPage",
        type="page",
        file_path="frontend/src/pages/MenuPage.jsx",
        route="/menu",
        requires_auth=False,
        nav_label="Menu",
    )
    files = {
        "frontend/src/pages/MenuPage.jsx": "export default function MenuPage() { return null; }",
        "frontend/src/App.jsx": 'path="/menu"',
    }
    result = check_page_item(item, files)
    assert result.status == "pass"


def test_check_page_item_fail_missing_file():
    item = PageItem(
        id="page.MenuPage",
        type="page",
        file_path="frontend/src/pages/MenuPage.jsx",
        route="/menu",
        requires_auth=False,
    )
    result = check_page_item(item, {})
    assert result.status == "fail"
    assert "MenuPage.jsx" in result.reason


def test_check_page_item_fail_no_default_export():
    item = PageItem(
        id="page.MenuPage",
        type="page",
        file_path="frontend/src/pages/MenuPage.jsx",
        route="/menu",
        requires_auth=False,
    )
    files = {
        "frontend/src/pages/MenuPage.jsx": "function MenuPage() { return null; }",
        "frontend/src/App.jsx": 'path="/menu"',
    }
    result = check_page_item(item, files)
    assert result.status == "fail"
    assert "default export" in result.reason


def test_check_page_item_fail_route_not_wired():
    item = PageItem(
        id="page.MenuPage",
        type="page",
        file_path="frontend/src/pages/MenuPage.jsx",
        route="/menu",
        requires_auth=False,
    )
    files = {
        "frontend/src/pages/MenuPage.jsx": "export default function MenuPage() { return null; }",
        "frontend/src/App.jsx": 'path="/dashboard"',
    }
    result = check_page_item(item, files)
    assert result.status == "fail"
    assert "/menu" in result.reason


# ============================================================
# check_seed_item tests
# ============================================================

def test_check_seed_item_pass():
    item = SeedItem(
        id="seed.menu_items",
        type="seed",
        model="MenuItem",
        count=5,
        sample_data=[{"name": "Margherita", "price": 12.99}],
    )
    boot_log = "[seed] inserted 5 menu items done -- 5 items=..."
    result = check_seed_item(item, boot_log)
    assert result.status == "pass"


def test_check_seed_item_fail_no_boot_log():
    item = SeedItem(
        id="seed.menu_items",
        type="seed",
        model="MenuItem",
        count=5,
        sample_data=[],
    )
    result = check_seed_item(item, "")
    assert result.status == "fail"


def test_check_seed_item_fail_model_not_in_log():
    item = SeedItem(
        id="seed.orders",
        type="seed",
        model="Order",
        count=3,
        sample_data=[],
    )
    boot_log = "[seed] inserted 5 menu items done"
    result = check_seed_item(item, boot_log)
    assert result.status == "fail"


# ============================================================
# check_style_item tests
# ============================================================

def test_check_style_item_pass():
    item = StyleItem(
        id="style.primary",
        type="style",
        css_var="--color-primary",
        value="#7C3AED",
    )
    files = {"frontend/src/index.css": ":root {\n  --color-primary: #7C3AED;\n}\n"}
    result = check_style_item(item, files)
    assert result.status == "pass"


def test_check_style_item_fail_missing():
    item = StyleItem(
        id="style.primary",
        type="style",
        css_var="--color-primary",
        value="#7C3AED",
    )
    files = {"frontend/src/index.css": ":root {\n  --color-secondary: #000;\n}\n"}
    result = check_style_item(item, files)
    assert result.status == "fail"
    assert "--color-primary" in result.reason


# ============================================================
# check_nav_reachability tests
# ============================================================

def test_check_nav_reachability_pass():
    items = [
        PageItem(
            id="page.MenuPage",
            type="page",
            file_path="frontend/src/pages/MenuPage.jsx",
            route="/menu",
            requires_auth=False,
            nav_label="Menu",
        )
    ]
    files = {
        "frontend/src/components/Navbar.jsx": (
            'import { Link } from "react-router-dom";\n'
            "export default function Navbar() {\n"
            '  return <Link to="/menu">Menu</Link>;\n'
            "}\n"
        )
    }
    results = check_nav_reachability(items, files)
    assert len(results) == 1
    assert results[0].status == "pass"


def test_check_nav_reachability_fail():
    items = [
        PageItem(
            id="page.OrdersPage",
            type="page",
            file_path="frontend/src/pages/OrdersPage.jsx",
            route="/orders",
            requires_auth=True,
            nav_label="Orders",
        )
    ]
    files = {
        "frontend/src/components/Navbar.jsx": "export default function Navbar() { return null; }"
    }
    results = check_nav_reachability(items, files)
    assert len(results) == 1
    assert results[0].status == "fail"


def test_check_nav_reachability_skips_no_label():
    items = [
        PageItem(
            id="page.HiddenPage",
            type="page",
            file_path="frontend/src/pages/HiddenPage.jsx",
            route="/hidden",
            requires_auth=True,
            nav_label=None,
        )
    ]
    results = check_nav_reachability(items, {})
    assert results == []


# ============================================================
# run_checklist driver tests
# ============================================================

def test_run_checklist_mixed():
    model_item = ModelItem(
        id="model.User",
        type="model",
        table_name="users",
        class_name="User",
        columns=[{"name": "email", "type": "str", "nullable": False}],
    )
    page_item = PageItem(
        id="page.HomePage",
        type="page",
        file_path="frontend/src/pages/HomePage.jsx",
        route="/",
        requires_auth=False,
    )
    files = {
        "backend/app/models.py": (
            "class User(Base):\n"
            "    email: Mapped[str] = mapped_column(String)\n"
        ),
        "frontend/src/pages/HomePage.jsx": "export default function HomePage() { return null; }",
        "frontend/src/App.jsx": 'path="/"',
    }
    results = run_checklist([model_item, page_item], files, {}, "")
    by_id = {r.item_id: r for r in results}
    assert by_id["model.User"].status == "pass"
    assert by_id["page.HomePage"].status == "pass"


def test_run_checklist_never_raises():
    class BrokenItem:
        type = "model"
        id = "broken"
        class_name = None
        table_name = None
        columns = None
        relationships = []

    results = run_checklist([BrokenItem()], {}, {}, "")
    assert len(results) >= 1
    assert results[0].status == "fail"


def test_run_checklist_idempotent():
    item = ModelItem(
        id="model.User",
        type="model",
        table_name="users",
        class_name="User",
        columns=[{"name": "email", "type": "str", "nullable": False}],
    )
    files = {
        "backend/app/models.py": (
            "class User(Base):\n"
            "    email: Mapped[str] = mapped_column(String)\n"
        )
    }
    results_a = run_checklist([item], files, {}, "")
    results_b = run_checklist([item], files, {}, "")
    assert [r.status for r in results_a] == [r.status for r in results_b]
