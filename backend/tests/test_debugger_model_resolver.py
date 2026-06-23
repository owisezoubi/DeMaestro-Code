from app.ai.claude.agents.debugger import _resolve_model_name
import structlog

log = structlog.get_logger("test")

MODELS_CONTENT = '''
from app.database import Base
from sqlalchemy.orm import Mapped, mapped_column

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)

class MenuItem(Base):
    __tablename__ = "menu_items"
    id: Mapped[int] = mapped_column(primary_key=True)

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)

class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
'''


def test_exact_match():
    assert _resolve_model_name("MenuItem", MODELS_CONTENT, log) == "MenuItem"


def test_plural_to_singular():
    assert _resolve_model_name("Orders", MODELS_CONTENT, log) == "Order"


def test_substring_resolution():
    # "Item" appears inside "MenuItem"
    assert _resolve_model_name("Item", MODELS_CONTENT, log) == "MenuItem"


def test_fuzzy_match():
    # "MenuItm" is a typo of MenuItem (similarity > 0.80)
    assert _resolve_model_name("MenuItm", MODELS_CONTENT, log) == "MenuItem"


def test_no_match_returns_none():
    assert _resolve_model_name(
        "Spaceship", MODELS_CONTENT, log,
    ) is None


def test_ignores_non_base_classes():
    content = '''
class NotAModel:
    pass

class User(Base):
    pass
'''
    assert _resolve_model_name("User", content, log) == "User"
    assert _resolve_model_name("NotAModel", content, log) is None


def test_returns_none_for_unparseable():
    assert _resolve_model_name(
        "Foo", "def broken(:", log,
    ) is None
