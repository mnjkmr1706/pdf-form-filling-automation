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
