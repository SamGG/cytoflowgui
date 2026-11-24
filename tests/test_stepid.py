from datetime import datetime, timedelta, timezone

import pytest

from cytoflowgui.services import stepid


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_origin_and_example():
    assert stepid.from_datetime(stepid.ORIGIN) == "AAAAAAA"
    assert stepid.from_datetime(utc(2026, 11, 23, 14, 5, 9)) == "AACJWTH"   # 1,087,509 s
    assert stepid.decode("AACJWTH") == 1_087_509


def test_roundtrip_and_resolution():
    t = utc(2030, 1, 2, 3, 4, 5)
    assert stepid.to_datetime(stepid.from_datetime(t)) == t
    assert stepid.next_id("AAAAAAA") == "AAAAAAB"
    assert stepid.next_id("AAAAAAZ") == "AAAAABA"
    assert stepid.from_datetime(t + timedelta(milliseconds=900)) == stepid.from_datetime(t)


def test_chronological_sorting():
    ids = [stepid.from_datetime(stepid.ORIGIN + timedelta(seconds=s)) for s in (0, 1, 25, 26, 700, 10**6, 10**9)]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


def test_capacity_is_about_254_years():
    assert stepid.CAPACITY == 26 ** 7
    assert 254 < stepid.CAPACITY / (365.25 * 86400) < 255
    assert stepid.encode(stepid.CAPACITY - 1) == "ZZZZZZZ"
    with pytest.raises(stepid.StepIdError):
        stepid.encode(stepid.CAPACITY)


def test_utc_independent_of_timezone():
    paris = timezone(timedelta(hours=1))
    assert stepid.from_datetime(datetime(2026, 11, 23, 15, 5, 9, tzinfo=paris)) == "AACJWTH"
    with pytest.raises(stepid.StepIdError):
        stepid.from_datetime(datetime(2026, 11, 23, 14, 5, 9))        # naive datetime refused


def test_clock_before_origin():
    with pytest.raises(stepid.StepIdError):
        stepid.from_datetime(utc(2026, 11, 10, 23, 59, 59))


def test_validation_and_names():
    assert stepid.is_valid("aacjwth") and not stepid.is_valid("AACJWT") and not stepid.is_valid("AACJWT1")
    assert stepid.parse_folder_name("AACJWTH_split_fcs") == "AACJWTH"
    assert stepid.parse_folder_name("aacjwth") == "AACJWTH"
    assert stepid.parse_folder_name("AACJWTHX_split") is None
    assert stepid.parse_folder_name("data") is None
    assert stepid.folder_name("aacjwth", None, "split_fcs") == "AACJWTH_split_fcs"
    assert stepid.folder_name("AACJWTH", "T cells: run #2", "split_fcs") == "AACJWTH_T_cells_run_2"
    assert stepid.folder_name("AACJWTH", "   ", "split_fcs") == "AACJWTH_split_fcs"
    assert len(stepid.sanitize_label("x" * 100, "d")) == stepid.LABEL_MAX
