from datetime import datetime
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from app.api.projects import _count_unanswered_messages, _project_card


class ProjectMessageCountTest(TestCase):
    def test_counts_only_messages_without_sent_decision(self):
        messages = [SimpleNamespace(id="message-1"), SimpleNamespace(id="message-2")]
        decisions = [
            SimpleNamespace(sourceMessageId="message-1", sentAt=datetime(2026, 8, 26)),
            SimpleNamespace(sourceMessageId="message-2", sentAt=None),
        ]

        self.assertEqual(_count_unanswered_messages(messages, decisions), 1)

    def test_does_not_double_count_message_with_multiple_requests(self):
        messages = [SimpleNamespace(id="message-1")]
        decisions = [
            SimpleNamespace(sourceMessageId="message-1", sentAt=None),
            SimpleNamespace(sourceMessageId="message-1", sentAt=None),
        ]

        self.assertEqual(_count_unanswered_messages(messages, decisions), 1)

    def test_any_sent_decision_marks_source_message_answered(self):
        messages = [SimpleNamespace(id="message-1")]
        decisions = [
            SimpleNamespace(sourceMessageId="message-1", sentAt=None),
            SimpleNamespace(sourceMessageId="message-1", sentAt=datetime(2026, 8, 26)),
        ]

        self.assertEqual(_count_unanswered_messages(messages, decisions), 0)


class ProjectCardCountTest(IsolatedAsyncioTestCase):
    async def test_exposes_active_tickets_and_unanswered_messages_separately(self):
        project = SimpleNamespace(
            id="project-1",
            clientEmail="client@example.com",
        )
        with (
            patch(
                "app.api.projects._active_ticket_count",
                new=AsyncMock(return_value=2),
            ),
            patch(
                "app.api.projects._unanswered_message_count",
                new=AsyncMock(return_value=5),
            ),
            patch(
                "app.api.projects.public_project",
                return_value={"updatedAt": "2026-08-26T00:00:00Z"},
            ),
            patch(
                "app.api.projects.ProjectSourceLink",
                new=SimpleNamespace(
                    ownerId=None,
                    projectId=None,
                    sourceChannel=None,
                    find_one=AsyncMock(return_value=None),
                ),
            ),
            patch(
                "app.api.projects.SourceMessage",
                new=SimpleNamespace(
                    ownerId=None,
                    projectId=None,
                    direction=None,
                    find_one=AsyncMock(return_value=None),
                ),
            ),
        ):
            card = await _project_card(project, "owner-1")

        self.assertEqual(card["activeTicketCount"], 2)
        self.assertEqual(card["unansweredMessageCount"], 5)
