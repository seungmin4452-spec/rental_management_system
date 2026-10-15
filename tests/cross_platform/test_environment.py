from rentdesk.platform.environment import get_environment_info


def test_get_environment_info_returns_populated_fields():
    info = get_environment_info()

    assert info.os_name in ("macOS", "Windows", "Linux", "Unknown")
    assert info.machine != ""
    assert info.python_version.startswith("3.12")
