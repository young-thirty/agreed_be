import unittest
from types import SimpleNamespace

from app.api.projects import (
    _attach_to_ticket,
    _decision_reply_inputs,
    _should_analyze_message,
    _status_rank,
)
from app.api.tickets import _analysis_payload


class InboundGateTest(unittest.TestCase):
    def test_only_received_non_empty_body_is_analyzed(self):
        self.assertTrue(_should_analyze_message("RECEIVED", "기능을 추가해 주세요"))
        self.assertFalse(_should_analyze_message("SENT", "진행하겠습니다"))
        self.assertFalse(_should_analyze_message("RECEIVED", "   "))


class ProjectStatusTest(unittest.TestCase):
    def test_rejected_project_has_stable_sort_rank(self):
        self.assertGreater(_status_rank("REJECTED"), _status_rank("COMPLETED"))


class ReplyDecisionContextTest(unittest.TestCase):
    def test_human_decision_and_values_are_given_to_reply_ai(self):
        decision = SimpleNamespace(
            handling="ignore",
            values={"amount": "300000", "empty": ""},
        )
        result = _decision_reply_inputs(["납기를 확인합니다."], decision)

        self.assertIn("반영하지 않기로", result[0])
        self.assertIn("납기를 확인합니다.", result)
        self.assertIn("사람이 확정한 amount: 300000", result)
        self.assertFalse(any("empty" in item for item in result))


class TicketSolutionInvalidationTest(unittest.IsolatedAsyncioTestCase):
    async def test_follow_up_message_invalidates_cached_solution(self):
        class Ticket:
            sourceMessageIds = []
            requestEvidence = []
            solution = object()
            updatedAt = None

            async def save(self):
                return None

        ticket = Ticket()
        message = SimpleNamespace(id="message-2")
        item = SimpleNamespace(requestQuote="추가 요청입니다")

        await _attach_to_ticket(ticket, message, item)

        self.assertIsNone(ticket.solution)
        self.assertEqual(ticket.sourceMessageIds, ["message-2"])
        self.assertEqual(ticket.requestEvidence[0]["quote"], "추가 요청입니다")


class TicketAnalysisPayloadTest(unittest.TestCase):
    def test_github_link_without_development_result_does_not_crash(self):
        solution = SimpleNamespace(
            developmentStatus=None,
            impactAnalysis=None,
            feasibility=None,
            replyDraft="",
            adviceMessage="확인이 필요합니다.",
            adviceReason="개발 현황을 확인하지 못했습니다.",
        )
        ticket = SimpleNamespace(
            id="ticket-1",
            ticketCode="TCK-01",
            solution=solution,
            aiDecisionStatus="OUT_OF_SCOPE_COORDINATION_REQUIRED",
            requestEvidence=[],
            documentEvidence=[],
            summaryTitle="카카오 로그인",
            currentSummary="",
            decisionReason="",
            category="기능 요청",
            requirement="카카오 로그인 추가",
        )

        payload = _analysis_payload(
            ticket,
            None,
            repo_full_name="acme/server",
        )

        self.assertEqual(payload["devContext"]["repoFullName"], "acme/server")
        self.assertEqual(payload["devContext"]["subject"], "카카오 로그인")
        self.assertFalse(payload["devContext"]["checked"])
        self.assertEqual(payload["devContext"]["items"], [])


if __name__ == "__main__":
    unittest.main()
