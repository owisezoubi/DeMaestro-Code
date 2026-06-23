from app.pipeline.checklist_runner import check_seed_item, check_page_item
from app.pipeline.checklist import SeedItem, PageItem


def test_seed_check_passes_at_gate_when_seed_py_references_model():
    item = SeedItem(
        id="seed.User", type="seed", model="User",
        count=5, sample_data=[],
    )
    files = {
        "backend/app/seed.py": (
            "def seed_demo_data(db):\n"
            "    db.add(User(email='a@b.c', password_hash='x'))\n"
            "    db.commit()\n"
        ),
    }
    result = check_seed_item(item, boot_log="", files=files)
    assert result.status == "pass"


def test_seed_check_fails_at_gate_when_seed_py_lacks_model():
    item = SeedItem(
        id="seed.Notification", type="seed", model="Notification",
        count=4, sample_data=[],
    )
    files = {
        "backend/app/seed.py": (
            "def seed_demo_data(db):\n"
            "    db.add(User(email='a@b.c'))\n"
        ),
    }
    result = check_seed_item(item, boot_log="", files=files)
    assert result.status == "fail"
    assert "Notification" in result.reason


def test_seed_check_uses_boot_log_when_present():
    item = SeedItem(
        id="seed.User", type="seed", model="User",
        count=5, sample_data=[],
    )
    result = check_seed_item(
        item,
        boot_log="[seed] done -- 5 users, 8 plants inserted\n",
        files={},
    )
    assert result.status == "pass"


def test_page_check_finds_file_with_alt_naming():
    item = PageItem(
        id="page.MyPlantsPage", type="page",
        file_path="frontend/src/pages/MyPlantsPage.jsx",
        route="/my-plants", requires_auth=True,
        nav_label="My Plants",
    )
    files = {
        "frontend/src/pages/MyPlants.jsx": (
            "export default function MyPlants() { return <div /> }\n"
        ),
        "frontend/src/App.jsx": (
            '<Route path="/my-plants" element={<MyPlants />} />\n'
        ),
    }
    result = check_page_item(item, files)
    assert result.status == "pass"


def test_page_check_finds_file_with_added_page_suffix():
    item = PageItem(
        id="page.Profile", type="page",
        file_path="frontend/src/pages/Profile.jsx",
        route="/profile", requires_auth=True,
        nav_label="Profile",
    )
    files = {
        "frontend/src/pages/ProfilePage.jsx": (
            "export default function ProfilePage() { return <div /> }\n"
        ),
        "frontend/src/App.jsx": (
            '<Route path="/profile" element={<ProfilePage />} />\n'
        ),
    }
    result = check_page_item(item, files)
    assert result.status == "pass"


def test_page_check_fails_when_no_file_matches():
    item = PageItem(
        id="page.Missing", type="page",
        file_path="frontend/src/pages/MissingPage.jsx",
        route="/missing", requires_auth=False,
        nav_label=None,
    )
    result = check_page_item(item, {})
    assert result.status == "fail"
