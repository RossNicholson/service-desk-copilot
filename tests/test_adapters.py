"""Every ticket adapter passes the same contract (see tests/adapter_contract.py)."""

import httpx
import pytest
from pydantic import SecretStr

from copilot.tickets import MemoryAdapter
from copilot.tickets.zammad import ZammadAdapter, html_to_text
from tests.adapter_contract import AdapterContract
from tests.fake_zammad import FakeZammad


class TestMemoryAdapter(AdapterContract):
    @pytest.fixture
    def adapter(self):
        return MemoryAdapter()


class TestZammadAdapterOffline(AdapterContract):
    @pytest.fixture
    def adapter(self):
        fake = FakeZammad()
        return ZammadAdapter(
            "http://zammad.test", token=SecretStr("t0ken"), transport=fake.transport()
        )


@pytest.mark.compose
class TestZammadAdapterLive(AdapterContract):
    @pytest.fixture
    def adapter(self):
        from scripts.bootstrap_zammad import ROOT, load_env

        env = load_env(ROOT / ".env")
        return ZammadAdapter(
            env.get("ZAMMAD_URL", "http://localhost:8080"),
            basic=(env["ZAMMAD_ADMIN_EMAIL"], env["ZAMMAD_ADMIN_PASSWORD"]),
        )


def test_the_token_is_sent_as_a_header_and_never_in_the_url():
    seen = []

    def handler(request):
        seen.append(request)
        return FakeZammad().handle(request)

    adapter = ZammadAdapter(
        "http://zammad.test", token=SecretStr("t0ken"), transport=httpx.MockTransport(handler)
    )
    adapter.list_tickets()
    assert seen[0].headers["authorization"] == "Token token=t0ken"
    assert "t0ken" not in str(seen[0].url)
    assert "t0ken" not in repr(adapter.__dict__)


def test_html_bodies_become_text():
    assert html_to_text("<p>Hello&nbsp;there</p><div>Line two<br>three</div>") == (
        "Hello\xa0there\nLine two\nthree"
    )


def test_a_token_or_password_is_required():
    with pytest.raises(ValueError):
        ZammadAdapter("http://zammad.test")
