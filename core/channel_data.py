"""외부 채널 데이터를 표현하는 순수 값 객체와 Gmail 그룹핑 규칙."""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field


class EmailAddress(BaseModel):
    name: str
    address: str


class RawEmail(BaseModel):
    id: str
    threadId: str
    sentAt: str
    from_: EmailAddress = Field(alias="from")
    to: list[EmailAddress]
    cc: list[EmailAddress]
    subject: str
    body: str

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class SenderGroup(BaseModel):
    address: str
    name: str
    count: int
    latestAt: str
    emails: list[RawEmail]


class CompanyGroup(BaseModel):
    domain: str
    count: int
    latestAt: str
    senders: list[SenderGroup]


class SlackChannel(BaseModel):
    id: str
    name: str
    isPrivate: bool
    isMember: bool


class SlackFile(BaseModel):
    """화면에 공개해도 되는 Slack 파일 메타데이터.

    원본 ``url_private``은 담지 않는다. 파일 본문은 ``fileId``로 서버에
    요청하고, 서버가 Slack ``files.info``로 실제 주소를 다시 조회한다.
    """

    fileId: str
    name: str
    isImage: bool


class SlackMessage(BaseModel):
    id: str
    userId: str
    userName: str
    text: str
    sentAt: str
    replyCount: int
    files: list[SlackFile]


def _email_domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower()


def email_counterparty(
    email: RawEmail,
    my_addresses: Sequence[str],
) -> EmailAddress | None:
    """받은 메일이면 보낸 사람, 보낸 메일이면 첫 외부 수신자를 돌려준다."""

    mine = {address.lower() for address in my_addresses}
    if email.from_.address.lower() not in mine:
        return email.from_
    return next(
        (recipient for recipient in email.to if recipient.address.lower() not in mine),
        None,
    )


def group_gmail_by_company(
    emails: Sequence[RawEmail],
    my_addresses: Sequence[str],
) -> list[CompanyGroup]:
    """메일을 회사 도메인, 상대 주소 순서로 묶고 최근 대화부터 정렬한다."""

    sender_emails: dict[str, list[RawEmail]] = {}
    sender_names: dict[str, str] = {}

    for email in emails:
        who = email_counterparty(email, my_addresses)
        if who is None:
            continue
        address = who.address.lower()
        sender_emails.setdefault(address, []).append(email)
        sender_names.setdefault(address, who.name or address)

    senders_by_domain: dict[str, list[SenderGroup]] = {}
    for address, grouped_emails in sender_emails.items():
        ordered_emails = sorted(grouped_emails, key=lambda item: item.sentAt, reverse=True)
        sender = SenderGroup(
            address=address,
            name=sender_names[address],
            count=len(ordered_emails),
            latestAt=ordered_emails[0].sentAt,
            emails=ordered_emails,
        )
        senders_by_domain.setdefault(_email_domain(address), []).append(sender)

    companies: list[CompanyGroup] = []
    for domain, grouped_senders in senders_by_domain.items():
        ordered_senders = sorted(
            grouped_senders,
            key=lambda item: item.latestAt,
            reverse=True,
        )
        companies.append(
            CompanyGroup(
                domain=domain,
                count=sum(sender.count for sender in ordered_senders),
                latestAt=ordered_senders[0].latestAt,
                senders=ordered_senders,
            )
        )

    return sorted(companies, key=lambda item: item.latestAt, reverse=True)
