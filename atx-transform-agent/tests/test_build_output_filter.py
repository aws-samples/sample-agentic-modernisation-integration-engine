"""bin/ and obj/ next to a .NET project are build output; elsewhere they are source."""

from pathlib import Path

from services.file_comparison import _compare_directories, is_build_output
from services.plan_context_defaults import default_configuration_for


def _write(root: Path, rel: str, text: str = "x") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_dotnet_bin_obj_are_build_output(tmp_path):
    _write(tmp_path, "Api/Api.csproj")
    assert is_build_output(tmp_path, _write(tmp_path, "Api/bin/Debug/net10.0/Api.dll"))
    assert is_build_output(tmp_path, _write(tmp_path, "Api/obj/project.assets.json"))
    assert is_build_output(tmp_path, _write(tmp_path, ".git/HEAD"))
    assert not is_build_output(tmp_path, _write(tmp_path, "Api/Program.cs"))


def test_bin_without_project_file_is_kept(tmp_path):
    assert not is_build_output(tmp_path, _write(tmp_path, "bin/run.sh"))
    assert not is_build_output(tmp_path, _write(tmp_path, "tools/obj/readme.md"))


def test_diff_skips_build_output(tmp_path):
    orig, new = tmp_path / "o", tmp_path / "n"
    _write(orig, "Api/Api.csproj", "old")
    _write(new, "Api/Api.csproj", "new")
    _write(new, "Api/bin/Api.dll")
    _write(new, "Api/obj/x.json")
    names = {e["filename"]: e["status"] for e in _compare_directories(orig, new)}
    assert names == {"Api/Api.csproj": "modified"}


def test_dotnet_modernization_defaults_to_net10():
    assert "NET 10" in default_configuration_for("AWS/dotnet-modernization")
