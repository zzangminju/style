import json
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"


def build_prompt(item, situation):
    """사용자 입력을 AI에게 보낼 지시문으로 조립한다."""
    return (
        f"너는 친절한 퍼스널 스타일리스트야. "
        f"사용자가 가진 옷: '{item}'. 가는 자리: '{situation}'. "
        f"이 옷을 활용해서 그 자리에 어울리는 코디를 한국어로 제안해줘. "
        f"유행보다 '어울림'을 기준으로 하고, 부족한 아이템이 있으면 무엇을 더하면 좋을지도 알려줘. "
        f"3~4문장으로 따뜻하고 구체적으로 써줘. 마크다운 기호(*, #)는 쓰지 마."
    )


def ask_openai(item, situation):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("서버에 OPENAI_API_KEY가 설정되어 있지 않습니다.")

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": build_prompt(item, situation)},
        ],
        "temperature": 0.8,
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
    return data["choices"][0]["message"]["content"].strip()


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
