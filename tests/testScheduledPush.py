"""定时推送测试 — cron 注册、多会话、去重、内容核对。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from astrbot.api.message_components import Plain

from .. import arxiv_client
from .testHelpers import makePlugin, paper, papers


class TestCronRegistration:
    """测试 cron 任务注册。"""

    @pytest.mark.asyncio
    async def test_register_cronJob(self):
        """插件初始化时注册 cron 任务，参数正确。"""
        plugin = makePlugin({
            "send_config": {
                "push_time": "09:00",
                "push_timezone": "Asia/Shanghai",
                "target_sessions": ["test_session"],
            },
        })

        await plugin._register_cron_job()

        plugin.context.cron_manager.add_basic_job.assert_called_once()
        kw = plugin.context.cron_manager.add_basic_job.call_args.kwargs
        assert kw["name"] == "arxiv_daily_push"
        assert kw["cron_expression"] == "0 9 * * *"
        assert kw["timezone"] == "Asia/Shanghai"
        assert kw["enabled"] is True

    @pytest.mark.asyncio
    async def test_register_cronJob_customTime(self):
        """自定义推送时间 14:30。"""
        plugin = makePlugin({
            "send_config": {
                "push_time": "14:30",
                "push_timezone": "Asia/Shanghai",
                "target_sessions": ["s"],
            },
        })

        await plugin._register_cron_job()

        kw = plugin.context.cron_manager.add_basic_job.call_args.kwargs
        assert kw["cron_expression"] == "30 14 * * *"


class TestScheduledPush:
    """测试定时推送核心逻辑。"""

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_latest_papers", new_callable=AsyncMock)
    async def test_multipleSessions(self, mockLatest: AsyncMock):
        """向所有目标会话发送。"""
        mockLatest.return_value = papers(2)
        plugin = makePlugin({
            "send_config": {"target_sessions": ["s1", "s2"], "use_forward": False},
        })
        plugin.context.send_message = AsyncMock()
        plugin._history = MagicMock()
        plugin._history.is_sent = MagicMock(return_value=False)
        plugin._history.mark_sent_batch = MagicMock()

        await plugin._scheduled_push()

        assert plugin.context.send_message.call_count >= 2

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_latest_papers", new_callable=AsyncMock)
    async def test_skipsAlreadySent(self, mockLatest: AsyncMock):
        """已发送论文不重复推送。"""
        mockLatest.return_value = papers(3)
        plugin = makePlugin({
            "send_config": {"target_sessions": ["s1"], "use_forward": False},
        })
        plugin.context.send_message = AsyncMock()
        plugin._history = MagicMock()
        plugin._history.is_sent = MagicMock(
            side_effect=lambda session, pid: pid == "2501.00002"
        )
        plugin._history.mark_sent_batch = MagicMock()

        await plugin._scheduled_push()

        markCall = plugin._history.mark_sent_batch.call_args
        sentIds = markCall[0][1] if markCall else []
        assert "2501.00002" not in sentIds

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_latest_papers", new_callable=AsyncMock)
    async def test_noTargetSessions_skips(self, mockLatest: AsyncMock):
        """未配置目标会话时不推送。"""
        mockLatest.return_value = papers(2)
        plugin = makePlugin({"send_config": {"target_sessions": [], "use_forward": False}})
        plugin.context.send_message = AsyncMock()

        await plugin._scheduled_push()

        plugin.context.send_message.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_latest_papers", new_callable=AsyncMock)
    async def test_contentVerification(self, mockLatest: AsyncMock):
        """推送内容核对：标题、作者、链接完整。"""
        mockLatest.return_value = [
            paper(
                title="Graph Neural Networks for Reasoning",
                authors=["Alice Smith", "Bob Jones", "Charlie"],
                abs_url="https://arxiv.org/abs/2501.00001",
            )
        ]
        plugin = makePlugin({
            "send_config": {
                "target_sessions": ["s1"], "use_forward": False,
                "send_abstract": True, "abstract_as_image": False,
            },
        })
        plugin.context.llm_generate = AsyncMock()
        plugin.context.send_message = AsyncMock()
        plugin._history = MagicMock()
        plugin._history.is_sent = MagicMock(return_value=False)
        plugin._history.mark_sent_batch = MagicMock()

        await plugin._scheduled_push()

        allText = ""
        for call in plugin.context.send_message.call_args_list:
            msg = call[0][1]
            if hasattr(msg, "chain"):
                for comp in msg.chain:
                    if isinstance(comp, Plain):
                        allText += comp.text

        assert "Graph Neural Networks for Reasoning" in allText
        assert "Alice Smith" in allText
        assert "arxiv.org/abs/2501.00001" in allText

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_latest_papers", new_callable=AsyncMock)
    async def test_apiError_graceful(self, mockLatest: AsyncMock):
        """API 错误时不崩溃。"""
        mockLatest.side_effect = RuntimeError("arXiv API down")
        plugin = makePlugin({
            "send_config": {"target_sessions": ["s1"], "use_forward": False},
        })
        plugin.context.send_message = AsyncMock()

        await plugin._scheduled_push()

        plugin.context.send_message.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_latest_papers", new_callable=AsyncMock)
    async def test_markSentAfterSuccess(self, mockLatest: AsyncMock):
        """推送成功后标记已发送。"""
        mockLatest.return_value = papers(2)
        plugin = makePlugin({
            "send_config": {"target_sessions": ["sx"], "use_forward": False},
        })
        plugin.context.send_message = AsyncMock()
        plugin._history = MagicMock()
        plugin._history.is_sent = MagicMock(return_value=False)
        plugin._history.mark_sent_batch = MagicMock()

        await plugin._scheduled_push()

        plugin._history.mark_sent_batch.assert_called()
        call = plugin._history.mark_sent_batch.call_args
        assert call[0][0] == "sx"
        assert call[0][1] == ["2501.00001", "2501.00002"]
