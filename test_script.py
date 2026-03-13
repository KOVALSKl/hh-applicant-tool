from __future__ import annotations

import hh_applicant_tool.main
import pytest


pytestmark = pytest.mark.online_smoke


def test_hh_api_me_online_smoke(pytestconfig) -> None:
    if not pytestconfig.getoption("--run-online-smoke"):
        pytest.skip("online smoke disabled: pass --run-online-smoke to enable")

    # Проверка внешнего интеграционного контура HH API.
    tool = hh_applicant_tool.main.HHApplicantTool(["-vv"])
    response = tool.api_client.get("/me")
    assert response is not None
