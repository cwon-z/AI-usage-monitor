"""Offline contract/formula checks, not Android or the actual KWGT runtime."""

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

WIDGET = Path(__file__).resolve().parents[1] / "widgets" / "kwgt"


def load(name):
    spec = importlib.util.spec_from_file_location(name, WIDGET / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder, checker = load("build_widget"), load("check_kode")
NOW = 1788998400
SERVER = "https://homelab.example.ts.net"


def test_shipped_r7_matches_reproducible_build(tmp_path):
    rebuilt = builder.build(tmp_path / "r7.kwgt", variant="oneui_3x2", revision=7)
    assert rebuilt.read_bytes() == (WIDGET / "AI_Usage_OneUI_3x2_R7.kwgt").read_bytes()


def payload():
    return {
        "claude": {
            "status": "ok",
            "stale": False,
            "session": 37,
            "weekly": 61,
            "session_reset_in": "2h 11m",
            "weekly_reset_in": "3d 7h",
            "collected_at": "2026-09-10T00:00:00Z",
        },
        "openai": {
            "status": "ok",
            "stale": False,
            "five_hour": 18,
            "weekly": 26,
            "five_hour_reset_in": "4h 13m",
            "weekly_reset_in": "5d 2h",
            "collected_at": "2026-09-10T00:00:00Z",
        },
        "stale": False,
    }


def context(data=None, **changes):
    values = {
        "server": SERVER,
        "token": "example-widget-token-not-real",
        "bound": SERVER,
        "checked": str(NOW),
        "attempt": str(NOW - 1),
        "data": json.dumps(data or payload()),
    }
    values.update(changes)
    return checker.Context(builder.globals_spec(), values, now=NOW)


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "0"),
        (100, "100"),
        (121, "121"),
        (None, "-1"),
        ("null", "-1"),
        ("", "-1"),
        (-2, "-1"),
        ([], "-1"),
        ({}, "-1"),
    ],
)
def test_missing_is_not_zero(value, expected):
    data = payload()
    data["openai"]["five_hour"] = value
    assert context(data).global_value("g5") == expected


@pytest.mark.parametrize(
    "server,valid",
    [
        (SERVER, "1"),
        (SERVER + "/", "1"),
        ("https://10.0.0.2:8443", "1"),
        ("", "0"),
        ("http://homelab.local", "0"),
        ("https://user:pass@host", "0"),
        (SERVER + "/api/v1/usage/compact", "0"),
        (SERVER + "?token=secret", "0"),
    ],
)
def test_only_https_origin_is_accepted(server, valid):
    assert context(server=server).global_value("ready") == valid


@pytest.mark.parametrize(
    "token", ["", "short", "x" * 16 + "\nBad: injected", "x" * 16 + "$df(S)$"]
)
def test_invalid_tokens_do_not_trigger_fetch(token):
    ctx = context(token=token)
    assert ctx.global_value("ready") == "0"
    assert ctx.render(builder.flow_spec()["a"][0]["params"]["formula"]) == "STOP"


def test_never_collected_and_server_change_hide_values():
    assert context(checked="0").global_value("cs") == "-1"
    assert context(bound="https://old-server.example").global_value("cs") == "-1"


def test_connection_states():
    assert context().global_value("state") == "UPDATED"
    assert context(token="").global_value("state") == "SETUP"
    assert (
        context(checked=str(NOW - 10), attempt=str(NOW)).global_value("state")
        == "SYNCING"
    )
    assert (
        context(checked=str(NOW - 100), attempt=str(NOW - 60)).global_value("state")
        == "OFFLINE"
    )
    assert (
        context(checked=str(NOW - 1801), attempt="0").global_value("state") == "STALE"
    )
    data = payload()
    data["stale"] = True
    assert context(data).global_value("state") == "STALE"


@pytest.mark.parametrize(
    "changes",
    [{}, {"data": "{}", "checked": "0"}, {"token": ""}, {"checked": str(NOW - 3600)}],
)
def test_all_display_formulas_parse_and_evaluate(changes):
    ctx = context(**changes)
    for module in builder.layout():
        if module["internal_type"] == "TextModule":
            ctx.render(module["text_expression"])
        for formula in module.get("internal_formulas", {}).values():
            ctx.render(formula)
    for key in builder.globals_spec():
        ctx.global_value(key)


def test_bad_response_cannot_overwrite_cache():
    flow = builder.flow_spec()
    stop = next(
        a
        for a in flow["a"]
        if a["type"] == "A_STOP_IF" and "tc(json" in a["params"]["formula"]
    )
    for body in ({}, {"detail": "Unauthorized"}, {"claude": {"status": "ok"}}):
        ctx = context()
        ctx.local["#response"] = json.dumps(body)
        assert ctx.render(stop["params"]["formula"]) == "STOP"
    ctx.local["#response"] = json.dumps(payload())
    assert ctx.render(stop["params"]["formula"]) == ""


def test_flow_uses_header_only_get_and_validates_before_commit():
    actions = builder.flow_spec()["a"]
    http = next(a for a in actions if a["type"] == "A_WGET")["params"]
    assert http["method"] == "GET"
    assert "token" not in http["uri"]
    assert http["headers"].startswith("Authorization: Bearer $gv(token)$")
    assert not http["ignore_ssl_errors"] and not http["ignore_http_errors"]
    ctx = context()
    ctx.local["#origin"] = SERVER + "/"
    assert ctx.render(http["uri"]) == SERVER + "/api/v1/usage/compact"
    code_check = next(
        i for i, a in enumerate(actions) if "#code" in a["params"].get("formula", "")
    )
    data_write = next(
        i for i, a in enumerate(actions) if a["params"].get("global") == "data"
    )
    assert code_check < data_write
    for code in (401, 403, 429, 500, 503):
        ctx.local["#code"] = code
        assert ctx.render(actions[code_check]["params"]["formula"]) == "STOP"


def test_archive_round_trip_and_no_secrets(tmp_path):
    first = builder.build(tmp_path / "first.kwgt")
    second = builder.build(tmp_path / "second.kwgt")
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.testzip() is None
        preset = json.loads(archive.read("preset.json"))
        assert preset == builder.make_preset()
        assert preset["preset_root"]["globals_list"]["token"]["value"] == ""
        assert preset["preset_root"]["globals_list"]["server"]["value"] == ""
        assert preset["preset_root"]["globals_list"]["data"]["value"] == "{}"
        assert all(
            ".." not in name and not name.startswith("/") for name in archive.namelist()
        )


def test_id_references_and_schedule():
    root = builder.make_preset()["preset_root"]
    flow = root["internal_flows"][0]
    ids = [flow["id"]] + [a["id"] for a in flow["a"] + flow["t"]]
    assert len(ids) == len(set(ids))
    assert root["internal_events"][0]["flow_id"] == flow["id"]
    assert {t["type"] for t in flow["t"]} == {"T_ONCE", "T_CRON", "T_MANUAL"}
    assert (
        next(t for t in flow["t"] if t["type"] == "T_CRON")["params"]["cron_string"]
        == "*/10 * * * *"
    )


def test_compact_dimensions_and_original_unchanged(tmp_path):
    compact = builder.make_preset("oneui_3x2")
    assert (compact["preset_info"]["width"], compact["preset_info"]["height"]) == (
        720,
        480,
    )
    assert compact["preset_info"]["title"] == "AI Usage — One UI 3x2"
    root = compact["preset_root"]
    assert (
        root["internal_flows"] == builder.make_preset()["preset_root"]["internal_flows"]
    )
    assert (
        "/480" in root["viewgroup_items"][0]["internal_formulas"]["config_scale_value"]
    )
    archive_path = builder.build(tmp_path / "compact.kwgt", variant="oneui_3x2")
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read("preset.json")) == compact
    with zipfile.ZipFile(builder.build(tmp_path / "original.kwgt")) as archive:
        assert json.loads(archive.read("preset.json")) == builder.make_preset()


@pytest.mark.parametrize("dark", [0, 1])
@pytest.mark.parametrize(
    "state", ["live", "setup", "null", "zero", "overage", "stale", "offline"]
)
def test_compact_formulas_and_bar_bounds(dark, state):
    base = context()
    data = payload()
    if state == "setup":
        base.values = {}
    if state in ("null", "zero", "overage"):
        data["openai"]["five_hour"] = {"null": None, "zero": 0, "overage": 121}[state]
    if state == "stale":
        data["claude"]["stale"] = True
        data["stale"] = True
    if state == "offline":
        base.values.update(checked=str(NOW - 100), attempt=str(NOW - 60))
    if state != "setup":
        base.values["data"] = json.dumps(data)
    ctx = checker.Context(
        builder.globals_spec("oneui_3x2"),
        base.values,
        now=NOW,
        system={"darkmode": dark, "rheight": 480},
    )
    assert ctx.global_value("bg") == ("#FF1C1C1E" if dark else "#FFFCFCFC")
    for module in builder.compact_layout():
        if module["internal_type"] == "TextModule":
            label = ctx.render(module["text_expression"])
            assert len(label) < 55
            if state == "stale" and module["internal_title"] == "Claude title":
                assert label == "Claude · stale"
            if module["internal_title"] == "Codex primary percent":
                expected = {
                    "null": "—",
                    "zero": "0%",
                    "overage": "121%",
                    "setup": "—",
                }.get(state, "18%")
                assert label == expected
        for key, formula in module.get("internal_formulas", {}).items():
            result = ctx.render(formula)
            if key == "shape_width":
                assert 1 <= float(result) <= 288
        if module["internal_type"] == "ShapeModule":
            assert module["position_offset_y"] + module["shape_height"] <= 480


@pytest.mark.parametrize("variant", ["midnight", "oneui_3x2"])
def test_r2_always_visible_structure_and_static_scale(variant, tmp_path):
    preset = builder.make_compat_preset(variant)
    root = preset["preset_root"]
    card = root["viewgroup_items"][0]
    assert card["config_scale_value"] == 50
    assert "internal_formulas" not in card
    assert "internal_toggles" not in card
    assert root["internal_flows"] == [builder.flow_spec()]
    for module in card["viewgroup_items"]:
        if module["internal_title"] in ("Card background", "Title"):
            assert "internal_formulas" not in module
            assert "internal_globals" not in module
            assert module["paint_color"].startswith("#FF")
            assert "$" not in module.get("text_expression", "")
    for item in root["globals_list"].values():
        if item["type"] == "COLOR":
            assert "global_formula" not in item
    path = builder.build(tmp_path / "r2.kwgt", variant=variant, revision=2)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read("preset.json")) == preset


def test_render_probe_has_no_runtime_dependencies(tmp_path):
    preset = builder.make_render_test()
    serialized = json.dumps(preset)
    assert "$" not in serialized
    assert "kfile://" not in serialized
    assert "internal_flows" not in serialized
    assert "globals_list" not in serialized
    assert "internal_formulas" not in serialized
    assert "Widget render OK" in serialized
    path = builder.build(tmp_path / "probe.kwgt", variant="render_test", revision=2)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == ["preset.json"]


@pytest.mark.parametrize("variant", ["midnight", "oneui_3x2"])
def test_r3_uses_group_padding_not_root_offsets(variant, tmp_path):
    previous = builder.make_compat_preset(variant)
    preset = builder.make_positioned_preset(variant)
    root = preset["preset_root"]
    assert preset["preset_info"]["title"].endswith(" R3")
    assert root["internal_flows"] == previous["preset_root"]["internal_flows"]
    old_items = previous["preset_root"]["viewgroup_items"][0]["viewgroup_items"]
    items = root["viewgroup_items"][0]["viewgroup_items"]
    positions = []
    for old, module in zip(old_items, items, strict=True):
        assert "position_offset_x" not in module
        assert "position_offset_y" not in module
        side = "right" if module["position_anchor"] == "TOPRIGHT" else "left"
        assert module[f"position_padding_{side}"] == old["position_offset_x"]
        assert module["position_padding_top"] == old["position_offset_y"]
        positions.append(builder.overlap_position(module, 720))
    assert len(set(positions)) > 15
    path = builder.build(tmp_path / "r3.kwgt", variant=variant, revision=3)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read("preset.json")) == preset


def test_reference_renderer_reproduces_ignored_offset_defect():
    # Kustom's documented group semantics: root offsets cannot move children.
    module = {
        "position_anchor": "TOPLEFT",
        "position_offset_x": 200,
        "position_offset_y": 300,
    }
    assert builder.overlap_position(module, 720) == (0, 0)
    module.update(position_padding_left=36, position_padding_top=100)
    assert builder.overlap_position(module, 720) == (36, 100)
    module.update(position_anchor="TOPRIGHT", position_padding_right=36)
    assert builder.overlap_position(module, 720) == (684, 100)
    old = builder.make_compat_preset("oneui_3x2")
    items = old["preset_root"]["viewgroup_items"][0]["viewgroup_items"]
    assert {builder.overlap_position(item, 720)[1] for item in items} == {0}


@pytest.mark.parametrize(
    "theme,system_dark,expected",
    [
        ("auto", 0, "#FFFCFCFC"),
        ("auto", 1, "#FF1C1C1E"),
        ("dark", 0, "#FF1C1C1E"),
        ("light", 1, "#FFFCFCFC"),
    ],
)
def test_r4_theme_changes_background_and_labels(theme, system_dark, expected):
    preset = builder.make_themed_preset()
    root = preset["preset_root"]
    ctx = checker.Context(
        root["globals_list"],
        {"theme": theme},
        now=NOW,
        system={"darkmode": system_dark},
    )
    assert ctx.global_value("bg") == expected
    assert ctx.global_value("ink") == (
        "#FFF5F5F7" if expected == "#FF1C1C1E" else "#FF171719"
    )
    items = {
        m["internal_title"]: m for m in root["viewgroup_items"][0]["viewgroup_items"]
    }
    assert items["Card background"]["internal_globals"]["paint_color"] == "bg"
    assert items["Title"]["internal_globals"]["paint_color"] == "ink"
    assert items["Tap hint"]["internal_globals"]["paint_color"] == "muted"
    for module in items.values():
        assert "position_offset_x" not in module
        assert "position_offset_y" not in module


def test_r4_keeps_fetching_and_ships_no_credentials(tmp_path):
    preset = builder.make_themed_preset()
    root = preset["preset_root"]
    assert root["internal_flows"] == [builder.native_flow_spec()]
    assert root["globals_list"]["server"]["value"] == ""
    assert root["globals_list"]["token"]["value"] == ""
    assert root["globals_list"]["data"]["value"] == "{}"
    assert (
        checker.Context(root["globals_list"], {}, now=NOW).global_value("state")
        == "SETUP"
    )
    path = builder.build(tmp_path / "r4.kwgt", variant="oneui_3x2", revision=4)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read("preset.json")) == preset


def test_r4_flow_parameters_match_native_string_serializer():
    flow = builder.native_flow_spec()
    for task in flow["a"] + flow["t"]:
        assert all(isinstance(value, str) for value in task.get("params", {}).values())
    request = next(a for a in flow["a"] if a["type"] == "A_WGET")["params"]
    assert request["ignore_ssl_errors"] == "false"
    assert request["ignore_http_errors"] == "false"
    assert (
        request["headers"]
        == "Authorization: Bearer $gv(token)$\nAccept: application/json"
    )
    response = next(a for a in flow["a"] if a["params"].get("local_var") == "response")[
        "params"
    ]
    assert response["fail_if_empty"] == "true"


@pytest.mark.parametrize(
    "width,height", [(360, 240), (360, 280), (360, 320), (420, 240), (720, 600)]
)
def test_r5_fills_available_viewport_without_group_scaling(width, height):
    preset = builder.make_fullspace_preset()
    root = preset["preset_root"]
    ctx = checker.Context(
        root["globals_list"], {}, now=NOW, system={"rwidth": width, "rheight": height}
    )
    card = root["viewgroup_items"][0]
    assert root["config_scale_value"] == card["config_scale_value"] == 100
    assert "internal_formulas" not in card
    background = card["viewgroup_items"][0]
    assert float(ctx.render(background["internal_formulas"]["shape_width"])) == width
    assert float(ctx.render(background["internal_formulas"]["shape_height"])) == height
    for module in card["viewgroup_items"]:
        resolved = dict(module)
        for key, formula in module.get("internal_formulas", {}).items():
            if key.startswith("position_padding_") or key in (
                "shape_width",
                "shape_height",
                "text_size",
            ):
                resolved[key] = float(ctx.render(formula))
        x, y = builder.overlap_position(resolved, width)
        assert 0 <= x <= width
        assert 0 <= y < height
        if module["internal_type"] == "ShapeModule":
            assert x + resolved["shape_width"] <= width + 0.001
            assert y + resolved["shape_height"] <= height + 0.001
        else:
            assert y + resolved["text_size"] <= height + 0.001


def test_r5_squircle_path_and_safe_viewport_fallback(tmp_path):
    preset = builder.make_fullspace_preset()
    root = preset["preset_root"]
    assert root["internal_flows"] == [builder.native_flow_spec()]
    background = root["viewgroup_items"][0]["viewgroup_items"][0]
    assert background["shape_type"] == "PATH"
    assert background["shape_path_scale"] == "AUTO"
    assert background["shape_path"].startswith("M ")
    assert background["shape_path"].endswith(" Z")
    points = builder.squircle_outline()
    assert min(x for x, _ in points) == min(y for _, y in points) == 0
    assert max(x for x, _ in points) == max(y for _, y in points) == 100
    # First quarter follows a superellipse, not a circular rounded corner.
    for x, y in points[:97]:
        assert ((x - 92) / 8) ** 4 + ((y - 12) / 12) ** 4 == pytest.approx(
            1, abs=0.00001
        )
    ctx = checker.Context(
        root["globals_list"], {}, now=NOW, system={"rwidth": 0, "rheight": 0}
    )
    assert ctx.global_value("vw") == "720"
    assert ctx.global_value("vh") == "480"
    path = builder.build(tmp_path / "r5.kwgt", variant="oneui_3x2", revision=5)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read("preset.json")) == preset


def test_r6_reference_rows_preserve_truthful_data_and_error_states(tmp_path):
    preset = builder.make_reference_preset()
    root = preset["preset_root"]
    assert root["internal_flows"] == [builder.native_flow_spec()]
    assert root["globals_list"]["server"]["value"] == ""
    assert root["globals_list"]["token"]["value"] == ""
    assert "theme" not in root["globals_list"]
    items = {
        m["internal_title"]: m for m in root["viewgroup_items"][0]["viewgroup_items"]
    }
    ctx = checker.Context(root["globals_list"], {}, now=NOW)
    assert (
        ctx.render(items["Connection and fetch age"]["text_expression"]) == "설정 필요"
    )
    assert ctx.render(items["Claude percent"]["text_expression"]) == "—"
    assert ctx.render(items["Codex percent"]["text_expression"]) == "—"
    assert "gv(gw)" in items["Codex percent"]["text_expression"]
    assert "weekly_reset_in" in items["Codex reset"]["text_expression"]
    data = payload()
    values = {
        "server": SERVER,
        "token": "example-widget-token-not-real",
        "bound": SERVER,
        "checked": str(NOW),
        "attempt": str(NOW - 1),
        "data": json.dumps(data),
    }
    ctx = checker.Context(root["globals_list"], values, now=NOW)
    assert ctx.render(items["Claude percent"]["text_expression"]) == "37%"
    assert ctx.render(items["Codex percent"]["text_expression"]) == "26%"
    assert (
        ctx.render(items["Connection and fetch age"]["text_expression"])
        == "업데이트 0분 전"
    )
    assert "5h / 20h" not in json.dumps(preset)
    values.update(checked="0", attempt=str(NOW - 60))
    ctx = checker.Context(root["globals_list"], values, now=NOW)
    assert (
        ctx.render(items["Connection and fetch age"]["text_expression"]) == "연결 오류"
    )
    path = builder.build(tmp_path / "reference.kwgt", variant="oneui_3x2", revision=6)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read("preset.json")) == preset
        for item in items.values():
            for field in ("text_family", "bitmap_bitmap"):
                if field in item:
                    assert (
                        item[field].removeprefix("kfile://org.kustom.provider/")
                        in archive.namelist()
                    )
        assert "licenses/OFL.txt" in archive.namelist()
        assert "licenses/ATTRIBUTION.md" in archive.namelist()


@pytest.mark.parametrize("width,height", [(360, 240), (360, 300), (420, 240)])
def test_r6_responsive_icons_bars_and_padding(width, height):
    preset = builder.make_reference_preset()
    root = preset["preset_root"]
    ctx = checker.Context(
        root["globals_list"], {}, now=NOW, system={"rwidth": width, "rheight": height}
    )
    for item in root["viewgroup_items"][0]["viewgroup_items"]:
        assert "position_offset_x" not in item
        resolved = dict(item)
        for key, formula in item.get("internal_formulas", {}).items():
            if key.startswith("position_padding_") or key in (
                "shape_width",
                "shape_height",
                "text_size",
                "bitmap_width",
                "bitmap_height",
            ):
                resolved[key] = float(ctx.render(formula))
        x, y = builder.overlap_position(resolved, width)
        assert 0 <= x <= width
        assert 0 <= y < height
        if item["internal_type"] == "BitmapModule":
            assert resolved["bitmap_width"] == resolved["bitmap_height"]
            assert x + resolved["bitmap_width"] <= width
            assert y + resolved["bitmap_height"] <= height


def test_r7_vector_marks_have_no_bitmap_resource_dependency(tmp_path):
    preset = builder.make_panel_preset()
    root = preset["preset_root"]
    items = {
        m["internal_title"]: m for m in root["viewgroup_items"][0]["viewgroup_items"]
    }
    marks = json.loads((WIDGET / "assets" / "provider_paths.json").read_text())
    for title, mark in (("Claude logo", "claude"), ("Codex logo", "openai")):
        module = items[title]
        assert module["internal_type"] == "ShapeModule"
        assert module["shape_type"] == "PATH"
        assert module["shape_path"] == marks[mark]
        assert module["shape_path_scale"] == "AUTO"
        assert not any(key.startswith("bitmap_") for key in module)
        assert "kfile://" not in json.dumps(module)
        assert "C" in module["shape_path"]
    path = builder.build(tmp_path / "r7.kwgt", variant="oneui_3x2", revision=7)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert not any(name.startswith("bitmaps/") for name in archive.namelist())
        assert "fonts/NotoSansKR-Regular.ttf" in archive.namelist()
        assert "licenses/ATTRIBUTION.md" in archive.namelist()
        assert json.loads(archive.read("preset.json")) == preset


def test_r7_all_four_quotas_and_null_stale_states():
    root = builder.make_panel_preset()["preset_root"]
    assert root["internal_flows"] == [builder.native_flow_spec()]
    items = {
        m["internal_title"]: m for m in root["viewgroup_items"][0]["viewgroup_items"]
    }
    values = {
        "server": SERVER,
        "token": "example-widget-token-not-real",
        "bound": SERVER,
        "checked": str(NOW),
        "attempt": str(NOW - 1),
        "data": json.dumps(payload()),
    }
    ctx = checker.Context(root["globals_list"], values, now=NOW)
    for title, expected in (
        ("Claude percent", "37%"),
        ("Codex percent", "26%"),
        ("Panel Claude percent", "61%"),
        ("Panel Codex percent", "18%"),
    ):
        assert ctx.render(items[title]["text_expression"]) == expected
    data = payload()
    data["claude"].update(weekly=None, stale=True)
    values["data"] = json.dumps(data)
    ctx = checker.Context(root["globals_list"], values, now=NOW)
    assert ctx.render(items["Panel Claude percent"]["text_expression"]) == "—"
    assert ctx.render(items["Panel Claude reset"]["text_expression"]) == "데이터 없음"
    assert ctx.render(items["Claude title"]["text_expression"]) == "Claude · 지연"


@pytest.mark.parametrize(
    "width,height", [(360, 240), (360, 300), (360, 390), (420, 240), (720, 760)]
)
def test_r7_row_spacing_is_proportional_to_type_not_viewport_height(width, height):
    root = builder.make_panel_preset()["preset_root"]
    ctx = checker.Context(
        root["globals_list"], {}, now=NOW, system={"rwidth": width, "rheight": height}
    )
    unit = float(ctx.global_value("unit"))
    items = {}
    for module in root["viewgroup_items"][0]["viewgroup_items"]:
        resolved = dict(module)
        for key, formula in module.get("internal_formulas", {}).items():
            if key.startswith("position_padding_") or key in (
                "shape_width",
                "shape_height",
                "shape_corners",
                "text_size",
            ):
                resolved[key] = float(ctx.render(formula))
        x, y = builder.overlap_position(resolved, width)
        assert 0 <= x <= width
        assert 0 <= y < height
        if module["internal_type"] == "ShapeModule":
            assert x + resolved["shape_width"] <= width + 0.001
            assert y + resolved["shape_height"] <= height + 0.001
        else:
            assert y + resolved["text_size"] <= height + 0.001
        items[module["internal_title"]] = resolved
    for name in ("Claude", "Codex"):
        delta = (
            items[name + " reset"]["position_padding_top"]
            - items[name + " title"]["position_padding_top"]
        )
        assert delta == pytest.approx(50 * unit)
        logo = items[name + " logo"]
        assert logo["shape_width"] == logo["shape_height"] == pytest.approx(78 * unit)
    bg = items["Card background"]
    assert bg["shape_width"] == width
    assert bg["shape_height"] == height
    panel = items["Secondary quota panel"]
    assert (
        panel["position_padding_top"]
        > items["Codex track"]["position_padding_top"]
        + items["Codex track"]["shape_height"]
    )
    assert panel["position_padding_top"] + panel["shape_height"] == pytest.approx(
        height - 28 * unit
    )
