import json
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"


def build_prompt(item, situation):
    """사용자 입력을 AI에게 보낼 지시문으로 조립한다.

    반드시 아래 6개 키를 가진 JSON 하나만 반환하도록 요청한다.
    """
    return (
        f"너는 친절한 퍼스널 스타일리스트야. "
        f"사용자가 가진 옷: '{item}'. 가는 자리: '{situation}'. "
        f"이 옷을 활용해서 그 자리에 어울리는 코디를 제안해줘. "
        f"유행보다 '어울림'을 기준으로 하고, 사용자가 이미 가진 아이템은 그대로 살려. "
        f"부족한 항목이 있으면 무엇을 더하면 좋을지 제안해.\n\n"
        f"반드시 아래 형식의 JSON 하나만 출력해. 설명 문장이나 마크다운(```)은 절대 쓰지 마.\n"
        f"{{\n"
        f'  "top": "상의 추천 (한 문장, 한국어)",\n'
        f'  "bottom": "하의 추천 (한 문장, 한국어)",\n'
        f'  "outer": "아우터 추천 (한 문장, 필요 없으면 빈 문자열 \\"\\")",\n'
        f'  "shoes": "신발 추천 (한 문장, 한국어)",\n'
        f'  "point": "포인트/액세서리 제안 (한 문장, 한국어)",\n'
        f'  "summary": "전체 코디를 아우르는 한 줄 요약 (한국어)"\n'
        f"}}"
    )


# 코디 결과에 반드시 담겨야 하는 항목들
OUTFIT_KEYS = ["top", "bottom", "outer", "shoes", "point", "summary"]


def parse_outfit(content):
    """AI가 돌려준 문자열을 코디 dict로 변환한다.

    AI가 형식을 안 지켰을 때(JSON이 아니거나 항목이 비었을 때)는
    ValueError를 던져서 상위에서 사용자에게 친절한 오류를 보내게 한다.
    """
    text = (content or "").strip()

    obj = None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # 혹시 앞뒤에 설명이 붙었으면 { ... } 부분만 잘라서 재시도
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                obj = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                obj = None

    if not isinstance(obj, dict):
        raise ValueError("AI가 올바른 JSON 형식으로 응답하지 않았어요. 다시 시도해 주세요.")

    # 모든 키를 문자열로 정규화하고, 없는 키는 빈 문자열로 채운다
    outfit = {key: str(obj.get(key, "") or "").strip() for key in OUTFIT_KEYS}

    # 핵심 항목이 하나도 없으면 실패로 간주
    if not (outfit["top"] or outfit["bottom"] or outfit["summary"]):
        raise ValueError("AI 응답에서 코디 정보를 찾지 못했어요. 다시 시도해 주세요.")

    return outfit


def ask_openai(item, situation):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("서버에 OPENAI_API_KEY가 설정되어 있지 않습니다.")

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": build_prompt(item, situation)},
        ],
        "temperature": 0.7,
        # 유효한 JSON 객체만 반환하도록 강제 (형식 오류 확률을 크게 낮춤)
        "response_format": {"type": "json_object"},
    }

    req = urllib.request.Request(
        OPENAI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"].strip()
    return parse_outfit(content)


class handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._send(400, {"error": "요청 본문을 읽을 수 없습니다."})

        item = (body.get("item") or "").strip()
        situation = (body.get("situation") or "").strip()

        if not item or not situation:
            return self._send(400, {"error": "item과 situation을 모두 보내주세요."})

        try:
            result = ask_openai(item, situation)
            return self._send(200, {"result": result})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            return self._send(502, {"error": "AI 호출 실패", "detail": detail})
        except Exception as e:
            return self._send(500, {"error": str(e)})
