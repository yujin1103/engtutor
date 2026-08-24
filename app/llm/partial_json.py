"""아직 완성되지 않은 JSON 에서 문자열 필드를 최대한 뽑아낸다.

왜 필요한가
-----------
지금은 JSON 전체가 완성될 때까지 기다렸다가 한 번에 보여준다. 그동안 화면이 비어 있다.
동시 사용자가 늘어 턴당 8초가 되면 8초를 빈 화면으로 보내는 셈이다.

스키마의 첫 필드가 `reply` 이므로 **`reply` 가 가장 먼저 완성된다.**
생성 중인 버퍼에서 `reply` 를 계속 긁어내면 첫 글자를 1초 안에 띄울 수 있다.
같은 GPU, 같은 모델로 체감 지연만 줄이는 방법이다.

json.loads 를 쓸 수 없다 — 버퍼가 아직 유효한 JSON 이 아니기 때문이다.
"""

from __future__ import annotations

_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    '"': '"',
    "\\": "\\",
    "/": "/",
}
_WS = " \t\r\n"


def extract_string(buffer: str, field: str) -> tuple[str, bool]:
    """`buffer` 에서 `field` 의 문자열 값을 생성된 만큼 돌려준다.

    Returns:
        (지금까지의 값, 닫는 따옴표까지 나왔는지)

    아직 필드가 안 나왔으면 ("", False). 이스케이프가 잘린 지점에서는
    그 앞까지만 돌려준다 — 다음 청크가 오면 다시 부르면 된다.
    """
    key = f'"{field}"'
    i = buffer.find(key)
    if i < 0:
        return "", False
    i += len(key)

    while i < len(buffer) and buffer[i] in _WS:
        i += 1
    if i >= len(buffer) or buffer[i] != ":":
        return "", False
    i += 1

    while i < len(buffer) and buffer[i] in _WS:
        i += 1
    if i >= len(buffer) or buffer[i] != '"':
        return "", False
    i += 1

    out: list[str] = []
    while i < len(buffer):
        ch = buffer[i]
        if ch == "\\":
            if i + 1 >= len(buffer):
                break  # 이스케이프가 아직 안 왔다
            nxt = buffer[i + 1]
            if nxt == "u":
                if i + 6 > len(buffer):
                    break  # \uXXXX 가 잘렸다
                try:
                    out.append(chr(int(buffer[i + 2 : i + 6], 16)))
                except ValueError:
                    break
                i += 6
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if ch == '"':
            return "".join(out), True
        out.append(ch)
        i += 1

    return "".join(out), False
