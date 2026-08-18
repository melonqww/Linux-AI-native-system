import webbrowser
from dataclasses import dataclass
from urllib.parse import quote_plus, urlparse
from uuid import uuid4


@dataclass(frozen=True)
class BrowserOpenPlan:
    plan_id: str
    url: str
    approval_required: bool


class BrowserService:
    SEARCH_ENGINES = {
        "duckduckgo": "https://duckduckgo.com/?q={query}",
        "google": "https://www.google.com/search?q={query}",
    }

    def plan_open(self, url: str) -> BrowserOpenPlan:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("only credential-free HTTP(S) URLs are allowed")
        return BrowserOpenPlan(str(uuid4()), parsed.geturl(), approval_required=False)

    def plan_search(self, query: str, engine: str = "duckduckgo") -> BrowserOpenPlan:
        query = query.strip()
        if not query or len(query) > 2_000:
            raise ValueError("search query must contain from 1 to 2000 characters")
        template = self.SEARCH_ENGINES.get(engine)
        if template is None:
            raise ValueError("unsupported search engine")
        return self.plan_open(template.format(query=quote_plus(query)))

    def open(self, plan: BrowserOpenPlan) -> bool:
        return bool(webbrowser.open(plan.url, new=2, autoraise=True))
