import pytest

from app.parsing.download_models import check_models


def test_real_looking_models_pass(tmp_path):
    (tmp_path / "RapidOcr").mkdir()
    (tmp_path / "RapidOcr" / "det.onnx").write_bytes(b"\x08\x07" * 20_000)
    (tmp_path / "dict.txt").write_text("small text files are fine")

    check_models(tmp_path)


def test_an_error_message_saved_as_a_model_fails_the_build(tmp_path):
    (tmp_path / "det.onnx").write_text('{"Code":10990101007,"Message":"empty file"}')

    with pytest.raises(SystemExit, match="look broken"):
        check_models(tmp_path)
