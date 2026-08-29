"""
FRED(연준 경제데이터)에서 최신 값 + 최근 10년 월별 이력을 가져와
regime-dashboard.html의 config.indicators[label] 항목(value/asOf/history)을
통째로 갱신한다.

자동 갱신 대상 (5개, FRED에 정확히 대응하는 시리즈가 있는 지표만):
  - 10Y-2Y 금리차   : T10Y2Y
  - 美 CPI 전년비    : CPIAUCSL (units=pc1 → 전년동월비 %)
  - 美 10년 국채금리 : DGS10
  - 하이일드 스프레드 : BAMLH0A0HYM2
  - VIX             : VIXCLS

자동 갱신 제외 (수동 유지 — value/asOf/history 모두 사람이 직접 채워야 함):
  - 美 제조업 PMI (ISM, 무료 공개 API 없음)
  - 달러인덱스 DXY (ICE DXY와 스케일이 같은 무료 시리즈 없음)
"""

import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

FRED_API_KEY = os.environ.get("FRED_API_KEY")
HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "regime-dashboard.html")
HISTORY_YEARS = 10

SERIES = {
    "10Y-2Y 금리차": {"series_id": "T10Y2Y", "units": None},
    "美 CPI 전년비": {"series_id": "CPIAUCSL", "units": "pc1"},
    "美 10년 국채금리": {"series_id": "DGS10", "units": None},
    "하이일드 스프레드": {"series_id": "BAMLH0A0HYM2", "units": None},
    "VIX": {"series_id": "VIXCLS", "units": None},
}


def fred_url(series_id, units=None, **params):
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
    )
    if units:
        url += f"&units={units}"
    for k, v in params.items():
        url += f"&{k}={v}"
    return url


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_latest(series_id, units=None):
    data = fetch_json(fred_url(series_id, units, sort_order="desc", limit=10))
    for obs in data.get("observations", []):
        if obs["value"] not in (".", "", None):
            return float(obs["value"]), obs["date"]
    raise RuntimeError(f"{series_id}: 유효한 최신 값이 없습니다")


def fetch_history(series_id, units=None, years=HISTORY_YEARS):
    """최근 N년치를 월말(end-of-period) 기준 월별로 리샘플링해서 가져온다."""
    start = (date.today() - timedelta(days=365 * years + 31)).isoformat()
    data = fetch_json(
        fred_url(
            series_id,
            units,
            observation_start=start,
            frequency="m",
            aggregation_method="eop",
            sort_order="asc",
            limit=300,
        )
    )
    points = []
    for obs in data.get("observations", []):
        if obs["value"] in (".", "", None):
            continue
        points.append((obs["date"][:7], round(float(obs["value"]), 2)))
    return points


def find_indicator_block(html, label):
    """indicators 배열 안에서 해당 label을 가진 객체 { ... } 의 [start, end) 범위를 찾는다.
    중괄호 깊이를 세어 매칭하므로 history 안의 중첩 객체({ d, v })가 있어도 안전하다.
    """
    key = f'label: "{label}"'
    idx = html.index(key)
    start = html.rfind("{", 0, idx)
    depth = 0
    i = start
    while i < len(html):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    raise RuntimeError(f"{label}: 블록의 닫는 괄호를 찾지 못했습니다")


def format_history(points):
    if not points:
        return "history: []"
    lines = ",\n".join(f'      {{ d: "{d}", v: {v} }}' for d, v in points)
    return "history: [\n" + lines + "\n    ]"


def main():
    if not FRED_API_KEY:
        print("FRED_API_KEY 환경변수가 없습니다.", file=sys.stderr)
        sys.exit(1)

    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    updated = []
    for label, cfg in SERIES.items():
        try:
            value, vdate = fetch_latest(cfg["series_id"], cfg["units"])
            history = fetch_history(cfg["series_id"], cfg["units"])
        except Exception as e:
            print(f"[경고] {label} 데이터 조회 실패: {e}", file=sys.stderr)
            continue

        try:
            start, end = find_indicator_block(html, label)
        except Exception as e:
            print(f"[경고] {label}: {e}", file=sys.stderr)
            continue

        block = html[start:end]
        rounded = round(value, 2)
        block, n1 = re.subn(r"value:\s*[-\d.]+", f"value: {rounded}", block, count=1)
        block, n2 = re.subn(r'asOf:\s*"[^"]*"', f'asOf: "{vdate}"', block, count=1)
        block, n3 = re.subn(
            r"history:\s*\[[\s\S]*?\]", format_history(history), block, count=1
        )
        if not (n1 and n2 and n3):
            print(
                f"[경고] {label}: 필드 치환 실패 (value={n1}, asOf={n2}, history={n3})",
                file=sys.stderr,
            )
            continue

        html = html[:start] + block + html[end:]
        updated.append(f"{label} → {rounded} (FRED {vdate} 기준, 이력 {len(history)}개월)")

    # 기준 시점(meta.asOf/source/캡션 날짜) 갱신
    # 주의: indicators[].asOf 도 같은 키 이름을 쓰지만 "YYYY-MM-DD" 형식(날짜만)이고
    # meta.asOf 는 항상 "...기준" 이 붙어 있으므로, "기준"이 포함된 값만 골라 교체해서
    # 위에서 이미 넣어둔 지표별 날짜를 덮어쓰지 않도록 한다.
    now = datetime.now(timezone.utc)
    as_of_ym = f"{now.year}.{now.month}"
    html = re.sub(r'asOf:\s*"[^"]*기준"', f'asOf: "{as_of_ym} 기준"', html)
    # caption: "현재(YYYY.M) ..." 형태의 config 필드만 한정해서 날짜만 교체
    # (JS 코드 쪽 '현재(' + dateMatch[1] + ')' 같은 로직 문자열은 건드리지 않도록
    #  caption: 뒤 큰따옴표 문자열 안의 현재(...) 만 매칭한다)
    html = re.sub(
        r'(caption:\s*"현재\()[^)]+(\))',
        r"\g<1>" + as_of_ym + r"\g<2>",
        html,
    )
    html = re.sub(
        r"(·\s*)\d{4}\.\d{1,2}(\s*기준)",
        r"\g<1>" + as_of_ym + r"\g<2>",
        html,
    )

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    if updated:
        print("갱신 완료:\n" + "\n".join(updated))
    else:
        print("갱신된 지표가 없습니다 (모두 실패했거나 패턴 불일치)")


if __name__ == "__main__":
    main()
