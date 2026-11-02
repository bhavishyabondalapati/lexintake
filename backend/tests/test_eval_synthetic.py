"""The calibration logic of the synthetic eval, on made-up rows (no database, no API)."""

from scripts.eval_synthetic import FieldRow, Row, calibrate, reviewed, split_halves


def row(doc_id, conf=0.95, ok=True, source=None, **kw):
    fields = [FieldRow("f", "x", "x" if ok else "y", "verified", conf, source, ok)]
    return Row(doc_id, False, "nda", None, "nda", None, "extracted", fields=fields, **kw)


def test_a_low_confidence_field_is_reviewed_only_below_the_threshold():
    r = row("a", conf=0.85)

    assert not reviewed(r, 0.8, 0.9)
    assert reviewed(r, 0.9, 0.9)


def test_ocr_fields_use_the_ocr_threshold():
    r = row("a", conf=0.85, source="ocr")

    assert reviewed(r, 0.8, 0.9)
    assert not reviewed(r, 0.9, 0.8)


def test_reasons_that_do_not_depend_on_thresholds_always_review():
    r = row("a", conf=1.0, reason_codes={"playbook_deviation"})

    assert reviewed(r, 0.5, 0.5)


def test_calibration_lowers_thresholds_while_no_error_escapes():
    # Errors sit at confidence 0.6 and 0.7; right answers at 0.75 and above.
    rows = [row("e1", 0.6, ok=False), row("e2", 0.7, ok=False)]
    rows += [row(f"r{i}", 0.75 + i * 0.02) for i in range(8)]

    (t_field, _), table = calibrate(rows)

    assert 0.7 < t_field <= 0.75  # as low as possible, but above the 0.7 error
    chosen = next(t for t in table if t["field"] == t_field and t["ocr"] == t_field)
    assert chosen["escaped"] == 0 and chosen["reviews"] == 2


def test_the_split_is_fixed_by_the_seed_and_covers_everything_once():
    ids = [f"syn_{i:05d}" for i in range(11)]

    a1, b1 = split_halves(ids)
    a2, b2 = split_halves(list(reversed(ids)))

    assert (a1, b1) == (a2, b2)
    assert a1 | b1 == set(ids) and not a1 & b1
    assert abs(len(a1) - len(b1)) <= 1
