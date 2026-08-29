"""
FRED(연준 경제데이터)에서 최신 지표를 가져와 regime-dashboard.html의
config.indicators 값을 자동으로 갱신한다.

자동 갱신 대상 (5개, FRED에 정확히 대응하는 시리즈가 있는 지표만):
  - 10Y-2Y 금리차   : T10Y2Y
  - 美 CPI 전년비    : CPIAUCSL (units=pc1 → 전년동월비 %)
  - 美 10년 국채금리 : DGS10
  - 하이일드 스프레드 : BAMLH0A0HYM2
  - VIX             : VIXCLS

자동 갱신 제외 (수동 유지):
  - 美 제조업 PMI (ISM, 무료 공개 API 없음)
  - 달러인덱스 DXY (ICE DXY와 스케일이 같은 무료 시리즈 없음)
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

FRED_API_KEY = os.environ.get("FRED_API_KEY")
HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "regime-dashboard.html")

SERIES = {
    "10Y-2Y 금리차": {"series_id": "T10Y2Y", "units": None},
    "美 CPI 전년비": {"series_id": "CPIAUCSL", "units": "pc1"},
    "美 10년 국채금리": {"series_id": "DGS10", "units": None},
    "하이일드 스프레드": {"series_id": "BAMLH0A0HYM2", "units": None},
    "VIX": {"series_id": "VIXCLS", "units": None},
}


def fetch_latest(series_id, units=None):
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
        "&sort_order=desc&limit=10"
    )
    if units:
        url += f"&units={units}"
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    for obs in data.get("observations", []):
        if obs["value"] not in (".", "", None):
            return float(obs["value"]), obs["date"]
    raise RuntimeError(f"{series_id}: 유효한 최신 값이 없습니다")


def main():
    if not FRED_API_KEY:
        print("FRED_API_KEY 환경변수가 없습니다.", file=sys.stderr)
        sys.exit(1)

    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    updated = []
    for label, cfg in SERIES.items():
        try:
            value, date = fetch_latest(cfg["series_id"], cfg["units"])
        except Exception as e:
            print(f"[경고] {label} 갱신 실패: {e}", file=sys.stderr)
            continue

        rounded = round(value, 2)
        # value와 그 뒤에 이어지는 asOf: "YYYY-MM-DD" 를 함께 갱신한다
        # (asOf는 FRED가 실제로 응답한 관측일 — "오늘" 이 아니라 그 지표의 기준일)
        pattern = re.compile(
            r'(label:\s*"' + re.escape(label) + r'".*?value:\s*)[-\d.]+'
            r'(,\s*asOf:\s*")[^"]*(")'
        )
        new_html, n = pattern.subn(
            lambda m: m.group(1) + str(rounded) + m.group(2) + date + m.group(3),
            html,
        )
        if n == 0:
            print(f"[경고] {label} 패턴을 찾지 못했습니다", file=sys.stderr)
            continue
        html = new_html
        updated.append(f"{label} → {rounded} (FRED {date} 기준)")

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
