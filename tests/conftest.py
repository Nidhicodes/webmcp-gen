"""Shared test fixtures for webmcp-gen tests."""

import pytest

from webmcp_gen.extract import PageExtraction, InteractiveElement, FormField


@pytest.fixture
def simple_search_extraction() -> PageExtraction:
    """A simple page with one search form."""
    return PageExtraction(
        url="https://example.com",
        title="Example Search",
        description="A simple search engine",
        elements=[
            InteractiveElement(
                kind="form",
                text="Search",
                action="https://example.com/search",
                method="GET",
                selector="form#search-form",
                context="Search the web",
                aria_label="Search form",
                element_index=0,
                fields=[
                    FormField(
                        tag="input",
                        type="search",
                        name="q",
                        id="search-input",
                        label="Search",
                        placeholder="Search the web...",
                        required=True,
                        selector="#search-input",
                    ),
                    FormField(
                        tag="select",
                        type="select",
                        name="lang",
                        id="lang-select",
                        label="Language",
                        options=["English", "Spanish", "French"],
                        selector="#lang-select",
                    ),
                ],
            ),
        ],
    )


@pytest.fixture
def complex_extraction() -> PageExtraction:
    """A more complex page with forms, buttons, and links."""
    return PageExtraction(
        url="https://example.com",
        title="Example App",
        description="A complex web application",
        elements=[
            # Login form
            InteractiveElement(
                kind="form",
                text="Sign In",
                action="https://example.com/login",
                method="POST",
                selector="form#login",
                context="Account",
                aria_label="Login form",
                element_index=0,
                fields=[
                    FormField(
                        tag="input", type="email", name="email",
                        id="email", label="Email", required=True,
                        selector="#email",
                    ),
                    FormField(
                        tag="input", type="password", name="password",
                        id="password", label="Password", required=True,
                        selector="#password",
                    ),
                    FormField(
                        tag="input", type="checkbox", name="remember",
                        id="remember", label="Remember me",
                        selector="#remember",
                    ),
                ],
            ),
            # Search form
            InteractiveElement(
                kind="form",
                text="Search",
                action="https://example.com/search",
                method="GET",
                selector="form#search",
                context="Find anything",
                element_index=1,
                fields=[
                    FormField(
                        tag="input", type="text", name="q",
                        id="q", label="Search", placeholder="Type to search...",
                        required=True, selector="#q",
                    ),
                ],
            ),
            # Button
            InteractiveElement(
                kind="button",
                text="Dark Mode",
                selector="#dark-mode-toggle",
                element_index=2,
            ),
            # Noise button (should be filtered)
            InteractiveElement(
                kind="button",
                text="Why choose us? We are the best platform for developers worldwide",
                selector="#promo-btn",
                element_index=3,
            ),
            # FAQ button (should be filtered)
            InteractiveElement(
                kind="button",
                text="How does pricing work?",
                selector="#faq-1",
                element_index=4,
            ),
            # Navigation links
            InteractiveElement(
                kind="link", text="Home",
                action="https://example.com/",
                selector="nav a:nth-of-type(1)",
                context="navigation",
                element_index=5,
            ),
            InteractiveElement(
                kind="link", text="Docs",
                action="https://example.com/docs",
                selector="nav a:nth-of-type(2)",
                context="navigation",
                element_index=6,
            ),
            InteractiveElement(
                kind="link", text="Pricing",
                action="https://example.com/pricing",
                selector="nav a:nth-of-type(3)",
                context="navigation",
                element_index=7,
            ),
        ],
    )


@pytest.fixture
def empty_extraction() -> PageExtraction:
    """A page with no interactive elements."""
    return PageExtraction(
        url="https://example.com/static",
        title="Static Page",
        description="Just text content",
        elements=[],
    )
