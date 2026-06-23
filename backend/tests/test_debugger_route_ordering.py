from app.ai.claude.agents.debugger import _reorder_routes_in_content


def test_reorder_moves_literal_past_mixed_methods():
    content = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/{plant_id}')\n"
        "def get_plant(plant_id: int): return {}\n"
        "\n"
        "@router.put('/{plant_id}')\n"
        "def update_plant(plant_id: int): return {}\n"
        "\n"
        "@router.delete('/{plant_id}')\n"
        "def delete_plant(plant_id: int): return {}\n"
        "\n"
        "@router.get('/favorites')\n"
        "def list_favorites(): return []\n"
    )
    new, swaps = _reorder_routes_in_content(content)
    assert swaps >= 1
    # /favorites must now appear before /{plant_id}
    pos_favorites = new.find("@router.get('/favorites')")
    pos_plant_id = new.find("@router.get('/{plant_id}')")
    assert pos_favorites < pos_plant_id
    # Other routes still present.
    assert "update_plant" in new
    assert "delete_plant" in new


def test_reorder_idempotent_when_already_correct():
    content = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/favorites')\n"
        "def list_favorites(): return []\n"
        "\n"
        "@router.get('/{plant_id}')\n"
        "def get_plant(plant_id: int): return {}\n"
    )
    new, swaps = _reorder_routes_in_content(content)
    assert swaps == 0
