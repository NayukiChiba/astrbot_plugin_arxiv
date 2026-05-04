"""arXiv API 客户端底层函数测试。"""

from .. import arxiv_client


class TestBuildSearchQuery:
    """测试 _build_search_query 查询构造。"""

    def test_queryOnly(self):
        q = arxiv_client._build_search_query(query="transformer")
        assert q == "all:transformer"

    def test_categoriesOnly(self):
        q = arxiv_client._build_search_query(categories=["cs.AI", "cs.LG"])
        assert "cat:cs.AI" in q
        assert "cat:cs.LG" in q
        assert "OR" in q

    def test_singleCategory_noOr(self):
        q = arxiv_client._build_search_query(categories=["cs.AI"])
        assert q == "cat:cs.AI"
        assert "OR" not in q

    def test_tagsOnly(self):
        q = arxiv_client._build_search_query(tags=["reinforcement", "learning"])
        assert "all:reinforcement" in q
        assert "all:learning" in q
        assert "OR" in q

    def test_queryAndCategories(self):
        q = arxiv_client._build_search_query(
            query="transformer", categories=["cs.AI", "cs.CL"]
        )
        assert "all:transformer" in q
        assert "cat:cs.AI" in q
        assert "cat:cs.CL" in q
        assert q.count("AND") == 1

    def test_all_combined(self):
        q = arxiv_client._build_search_query(
            query="transformer", categories=["cs.AI"], tags=["attention"]
        )
        parts = q.split(" AND ")
        assert len(parts) == 3  # query AND category AND tag

    def test_empty_returnsAll(self):
        q = arxiv_client._build_search_query()
        assert q == "all:*"


class TestExtractArxivId:
    """测试 extractArxivId。"""

    def test_directId(self):
        assert arxiv_client.extractArxivId("2501.12345") == "2501.12345"

    def test_absUrl(self):
        assert (
            arxiv_client.extractArxivId("https://arxiv.org/abs/2501.12345")
            == "2501.12345"
        )

    def test_pdfUrl(self):
        assert (
            arxiv_client.extractArxivId("https://arxiv.org/pdf/2501.12345.pdf")
            == "2501.12345"
        )

    def test_withVersion(self):
        assert (
            arxiv_client.extractArxivId("https://arxiv.org/abs/2501.12345v2")
            == "2501.12345v2"
        )


class TestArxivPaper:
    """测试 ArxivPaper 数据类。"""

    def test_publishedDate_isoFormat(self):
        from ..arxiv_client import ArxivPaper
        p = ArxivPaper(published="2025-03-15T00:00:00Z")
        assert p.published_date == "2025-03-15"

    def test_publishedDate_invalidFormat(self):
        from ..arxiv_client import ArxivPaper
        p = ArxivPaper(published="not a date")
        assert p.published_date == "not a date"
