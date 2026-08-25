"""L0 발화 분할 — 붙여넣은 대화를 Utterance 목록으로 정규화한다.

입력 어댑터의 경계가 여기다. 붙여넣기든 슬랙 연동이든 지메일 연동이든,
결과가 Utterance 목록이기만 하면 그 뒤 파이프라인은 출처를 모른다.
연동을 추가한다는 것은 이 폴더에 파일을 하나 더 놓는 일이다.
"""

from core.domain import Channel, Utterance

# 콜론이 줄 앞쪽에 있을 때만 화자로 인식한다. URL(https://)이나 시각(14:30)에
# 섞인 콜론과 구분하기 위한 기준이다.
_MAX_SPEAKER_LENGTH = 20


def to_utterances(raw_text: str, channel: Channel) -> list[Utterance]:
    """'화자: 내용' 형식의 줄로 이루어진 대화를 발화 단위로 나눈다.

    화자를 찾지 못한 줄은 통째로 본문으로 둔다. 형식이 어긋난 줄 하나 때문에
    전체 분석이 멈추지 않게 하기 위해서다.
    """
    lines = [line.strip() for line in raw_text.split("\n")]
    lines = [line for line in lines if line]

    utterances: list[Utterance] = []
    for index, line in enumerate(lines):
        colon = line.find(":")
        has_speaker = 0 < colon < _MAX_SPEAKER_LENGTH

        utterances.append(
            Utterance(
                index=index,
                channel=channel,
                speaker=line[:colon].strip() if has_speaker else "알수없음",
                text=line[colon + 1 :].strip() if has_speaker else line,
            )
        )

    return utterances
