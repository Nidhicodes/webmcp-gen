"""Live test: a captured session's cookies survive into a new executor."""

import pytest

from webmcp_gen.execute import WebExecutor


@pytest.mark.timeout(60)
class TestSessionRoundTrip:
    @pytest.mark.asyncio
    async def test_storage_state_carries_into_executor(self):
        # Seed a cookie via one executor, export state, load into a second one.
        url = "https://books.toscrape.com"
        async with WebExecutor(url) as ex1:
            await ex1._page.context.add_cookies([{
                "name": "wmtest", "value": "hello",
                "domain": "books.toscrape.com", "path": "/",
            }])
            state = await ex1.save_session()

        assert any(c["name"] == "wmtest" for c in state.get("cookies", []))

        # New executor with the saved state should have the cookie
        async with WebExecutor(url, storage_state=state) as ex2:
            cookies = await ex2._page.context.cookies()
            assert any(c["name"] == "wmtest" and c["value"] == "hello"
                       for c in cookies)
