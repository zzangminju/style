import json
import os
import time
import threading
import urllib.request
import urllib.error
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"

# ── 남용 방지 설정 ──────────────────────────────────────────
MAX_ITEM_LEN = 200        # 가진 아이템 최대 글자 수
MAX_SITUATION_LEN = 100   # 상황 최대 글자 수
RATE_LIMIT = 5            # 허용 호출 횟수
RATE_WINDOW = 60          # 위 횟수를 세는 시간(초)

# IP별 요청 시각 기록. 주의: 서버리스에선 인스턴스마다 메모리가 분리되어
# 완벽히 공유되지 않는다. 연속 남용은 막지만, 완벽한 제한은 외부 저장소(KV) 필요.
_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def check_length(item, situation):
    """입력이 너무 길면 안내 메시지를, 정상이면 None을 반환한다."""
    if len(item) > MAX_ITEM_LEN:
        return f"가진 아이템은 {MAX_ITEM_LEN}자 이내로 입력해 주세요."
    if len(situation) > MAX_SITUATION_LEN:
        return f"상황 설명은 {MAX_SITUATION_LEN}자 이내로 입력해 주세요."
    return None


def rate_limit_check(ip):
    """IP의 최근 호출을 세어 제한 초과 여부를 반환한다.

    반환: (허용 여부, 다시 시도까지 남은 초)
    sliding window: 창(RATE_WINDOW)보다 오래된 기록은 버리고 남은 개수로 판단.
    """
    now = time.time()
    with _hits_lock:
        dq = _hits[ip]
        while dq and now - dq[0] >= RATE_WINDOW:
            dq.popleft()
        if len(dq) >= RATE_LIMIT:
            retry_after = min(RATE_WINDOW, int(RATE_WINDOW - (now - dq[0])) + 1)
            return False, retry_after
        dq.append(now)
        if not dq:
            _hits.pop(ip, None)  # 빈 기록 정리 (메모리 관리)
        return True, 0


def get_client_ip(headers, fallback=""):
    """프록시(Vercel) 뒤의 실제 클라이언트 IP를 헤더에서 찾는다."""
    xff = headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    xri = headers.get("x-real-ip")
    if xri:
        return xri.strip()
    return fallback


def build_prompt(item, situation):
    """사용자 입력을 AI에게 보낼 지시문으로 조립한다.

    반드시 아래 6개 키를 가진 JSON 하나만 반환하도록 요청한다.
    """
    return (
        f"너는 친절한 퍼스널 스타일리스트야. "
        f"사용자가 가진 옷: '{item}'. 가는 자리: '{situation}'.\n\n"
        f"먼저 이 옷이 그 자리(격식/계절/목적)에 어울리는지 판단해. "
        f"만약 명백히 어울리지 않으면(예: 면접에 수영복, 결혼식에 트레이닝복) "
        f'"caution"에 왜 어울리지 않는지와 어떤 옷이 더 적절한지 부드럽게 알려줘. '
        f'어울리면 "caution"은 빈 문자열 ""로 둬.\n'
        f"그리고 어느 경우든 그 자리에 어울리는 코디를 제안해. "
        f"어울리는 아이템은 그대로 살리고, 부족하거나 부적절한 항목은 무엇으로 바꾸거나 더하면 좋을지 제안해.\n\n"
        f"반드시 아래 형식의 JSON 하나만 출력해. 설명 문장이나 마크다운(```)은 절대 쓰지 마.\n"
        f"{{\n"
        f'  "caution": "옷이 상황에 안 어울릴 때만 이유와 대안 (한 문장, 어울리면 빈 문자열 \\"\\")",\n'
        f'  "top": "상의 추천 (한 문장, 한국어)",\n'
        f'  "bottom": "하의 추천 (한 문장, 한국어)",\n'
        f'  "outer": "아우터 추천 (한 문장, 필요 없으면 빈 문자열 \\"\\")",\n'
        f'  "shoes": "신발 추천 (한 문장, 한국어)",\n'
        f'  "point": "포인트/액세서리 제안 (한 문장, 한국어)",\n'
        f'  "summary": "전체 코디를 아우르는 한 줄 요약 (한국어)"\n'
        f"}}"
    )


# 코디 결과에 담기는 항목들 (caution은 옷이 상황에 안 맞을 때만 채워짐)
OUTFIT_KEYS = ["caution", "top", "bottom", "outer", "shoes", "point", "summary"]


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
    def _send(self, status, payload, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
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

        # (2) 입력 길이 제한
        length_error = check_length(item, situation)
        if length_error:
            return self._send(400, {"error": length_error})

        # (1) IP 기반 호출 횟수 제한
        client_ip = get_client_ip(self.headers, self.client_address[0])
        allowed, retry_after = rate_limit_check(client_ip)
        if not allowed:
            return self._send(
                429,
                {"error": f"요청이 너무 많아요. {retry_after}초 후 다시 시도해 주세요.",
                 "retryAfter": retry_after},
                extra_headers={"Retry-After": str(retry_after)},
            )

        try:
            result = ask_openai(item, situation)
            return self._send(200, {"result": result})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            return self._send(502, {"error": "AI 호출 실패", "detail": detail})
        except Exception as e:
            return self._send(500, {"error": str(e)})
