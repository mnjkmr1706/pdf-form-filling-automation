"""Bare-assert tests for the mapping cache. Run with:
    .venv-1/bin/python -m tests.test_mapping_cache
"""
from claims_parser.widget_models import Widget, WidgetCatalog, WidgetRect


def _w(name, t="text", page=1, rect=(10.0, 10.0, 100.0, 30.0), on_value=None, xref=1, choices=None):
    return Widget(
        field_name=name,
        widget_type=t,
        rect=WidgetRect(page=page, x0=rect[0], y0=rect[1], x1=rect[2], y1=rect[3]),
        xref=xref,
        tu_label=None,
        choice_values=choices,
        on_value=on_value,
    )


def _cat(widgets, page_count=1, sizes=((612.0, 792.0),)):
    return WidgetCatalog(
        file_name="x.pdf",
        page_count=page_count,
        page_sizes_pt=list(sizes),
        widgets=widgets,
    )


def test_fingerprint_is_deterministic():
    from claims_parser.mapping_cache import compute_form_fingerprint
    c = _cat([_w("a"), _w("b", t="checkbox", on_value="Yes")])
    assert compute_form_fingerprint(c) == compute_form_fingerprint(c)


def test_fingerprint_ignores_widget_order():
    from claims_parser.mapping_cache import compute_form_fingerprint
    a = _w("a", xref=1)
    b = _w("b", t="checkbox", on_value="Yes", xref=2)
    assert compute_form_fingerprint(_cat([a, b])) == compute_form_fingerprint(_cat([b, a]))


def test_fingerprint_ignores_xref():
    from claims_parser.mapping_cache import compute_form_fingerprint
    fp1 = compute_form_fingerprint(_cat([_w("a", xref=10), _w("b", xref=11)]))
    fp2 = compute_form_fingerprint(_cat([_w("a", xref=999), _w("b", xref=1000)]))
    assert fp1 == fp2


def test_fingerprint_ignores_tu_label():
    from claims_parser.mapping_cache import compute_form_fingerprint
    w1 = _w("a"); w1.tu_label = "Patient Name"
    w2 = _w("a"); w2.tu_label = "Member Name"
    assert compute_form_fingerprint(_cat([w1])) == compute_form_fingerprint(_cat([w2]))


def test_fingerprint_tolerates_subpoint_rect_jitter():
    from claims_parser.mapping_cache import compute_form_fingerprint
    a = _w("a", rect=(10.0, 10.0, 100.0, 30.0))
    b = _w("a", rect=(10.2, 10.1, 100.3, 30.4))
    assert compute_form_fingerprint(_cat([a])) == compute_form_fingerprint(_cat([b]))


def test_fingerprint_detects_rect_move_over_half_point():
    from claims_parser.mapping_cache import compute_form_fingerprint
    a = _w("a", rect=(10.0, 10.0, 100.0, 30.0))
    b = _w("a", rect=(12.0, 10.0, 102.0, 30.0))
    assert compute_form_fingerprint(_cat([a])) != compute_form_fingerprint(_cat([b]))


def test_fingerprint_detects_added_widget():
    from claims_parser.mapping_cache import compute_form_fingerprint
    one = _cat([_w("a")])
    two = _cat([_w("a"), _w("b")])
    assert compute_form_fingerprint(one) != compute_form_fingerprint(two)


def test_fingerprint_detects_renamed_field():
    from claims_parser.mapping_cache import compute_form_fingerprint
    assert compute_form_fingerprint(_cat([_w("a")])) != compute_form_fingerprint(_cat([_w("a_renamed")]))


def test_fingerprint_detects_on_value_change():
    from claims_parser.mapping_cache import compute_form_fingerprint
    a = _w("a", t="checkbox", on_value="Yes")
    b = _w("a", t="checkbox", on_value="On")
    assert compute_form_fingerprint(_cat([a])) != compute_form_fingerprint(_cat([b]))


def test_fingerprint_detects_page_count_change():
    from claims_parser.mapping_cache import compute_form_fingerprint
    one_page = _cat([_w("a", page=1)], page_count=1, sizes=((612.0, 792.0),))
    two_page = _cat([_w("a", page=1)], page_count=2, sizes=((612.0, 792.0), (612.0, 792.0)))
    assert compute_form_fingerprint(one_page) != compute_form_fingerprint(two_page)


def test_store_then_lookup_roundtrip():
    import tempfile, os
    from claims_parser.mapping_cache import (
        compute_form_fingerprint, store, lookup, cache_dir,
    )
    from claims_parser.mapping_models import WidgetMapping
    from claims_parser.schema_models import FormSchema

    cat = _cat([_w("a"), _w("b", t="checkbox", on_value="Yes", xref=2)])
    fp = compute_form_fingerprint(cat)
    mapping = WidgetMapping(
        file_name="x.pdf", model="gpt-5-mini", chunk_count=1,
        bindings=[], unmapped_widget_field_names=[],
    )
    schema = FormSchema(form_title="x", sections=[], fields=[])

    with tempfile.TemporaryDirectory() as td:
        os.environ["PDF_PARSER_MAPPINGS_DIR"] = td
        try:
            assert lookup(cat) is None
            store(fp, mapping, schema, source_pdf_basename="x.pdf")
            cached = lookup(cat)
            assert cached is not None
            assert cached.form_fingerprint == fp
            assert cached.source_pdf_basename == "x.pdf"
            assert cached.mapping.file_name == "x.pdf"
            index_path = cache_dir() / "index.json"
            assert index_path.exists()
        finally:
            os.environ.pop("PDF_PARSER_MAPPINGS_DIR", None)


def _binding(field_name, xref, on_value=None, semantic_id="x", page=1):
    from claims_parser.mapping_models import WidgetBinding
    return WidgetBinding(
        widget_field_name=field_name,
        widget_xref=xref,
        semantic_field_id=semantic_id,
        label_text="L",
        label_source="text_line",
        label_polygon=None,
        label_page=page,
        confidence=0.9,
        reasoning="r",
        option_label=None,
        on_value=on_value,
    )


def test_rebind_updates_xrefs_by_field_name():
    from claims_parser.mapping_cache import rebind_to_current_xrefs
    from claims_parser.mapping_models import WidgetMapping
    from claims_parser.schema_models import FormSchema, FormField

    cat = _cat([_w("a", xref=999), _w("b", xref=1000)])
    mapping = WidgetMapping(
        file_name="x.pdf", model="m", chunk_count=1,
        bindings=[_binding("a", xref=1), _binding("b", xref=2)],
        unmapped_widget_field_names=[],
    )
    schema = FormSchema(form_title="x", sections=[], fields=[
        FormField(
            field_id="x", label="L", section=None,
            field_type="text", source_text="L", required=False,
            format_hint=None, options=None,
            widget_bindings=[_binding("a", xref=1)],
        ),
        FormField(
            field_id="y", label="L2", section=None,
            field_type="text", source_text="L2", required=False,
            format_hint=None, options=None,
            widget_bindings=[_binding("b", xref=2)],
        ),
    ])

    rebound = rebind_to_current_xrefs(mapping, schema, cat)
    assert rebound is not None
    new_mapping, new_schema = rebound
    xrefs = {b.widget_field_name: b.widget_xref for b in new_mapping.bindings}
    assert xrefs == {"a": 999, "b": 1000}
    field_xrefs = {f.field_id: f.widget_bindings[0].widget_xref for f in new_schema.fields}
    assert field_xrefs == {"x": 999, "y": 1000}


def test_rebind_disambiguates_radio_siblings_by_on_value():
    from claims_parser.mapping_cache import rebind_to_current_xrefs
    from claims_parser.mapping_models import WidgetMapping
    from claims_parser.schema_models import FormSchema, FormField

    cat = _cat([
        _w("radio1", t="radio", on_value="Yes", xref=501),
        _w("radio1", t="radio", on_value="No", xref=502),
    ])
    mapping = WidgetMapping(
        file_name="x.pdf", model="m", chunk_count=1,
        bindings=[
            _binding("radio1", xref=1, on_value="Yes"),
            _binding("radio1", xref=2, on_value="No"),
        ],
        unmapped_widget_field_names=[],
    )
    schema = FormSchema(form_title="x", sections=[], fields=[
        FormField(
            field_id="q", label="L", section=None,
            field_type="radio_group", source_text="L", required=False,
            format_hint=None, options=["Yes", "No"],
            widget_bindings=[
                _binding("radio1", xref=1, on_value="Yes"),
                _binding("radio1", xref=2, on_value="No"),
            ],
        ),
    ])
    rebound = rebind_to_current_xrefs(mapping, schema, cat)
    assert rebound is not None
    new_mapping, _ = rebound
    by_on = {b.on_value: b.widget_xref for b in new_mapping.bindings}
    assert by_on == {"Yes": 501, "No": 502}


def test_rebind_returns_none_when_field_missing():
    from claims_parser.mapping_cache import rebind_to_current_xrefs
    from claims_parser.mapping_models import WidgetMapping
    from claims_parser.schema_models import FormSchema

    cat = _cat([_w("a", xref=999)])
    mapping = WidgetMapping(
        file_name="x.pdf", model="m", chunk_count=1,
        bindings=[_binding("a", xref=1), _binding("missing", xref=2)],
        unmapped_widget_field_names=[],
    )
    schema = FormSchema(form_title="x", sections=[], fields=[])
    assert rebind_to_current_xrefs(mapping, schema, cat) is None


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
