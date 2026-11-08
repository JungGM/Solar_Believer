"""
K-VPP Insight Pipeline Utilities
재시도 지원 HTTP 세션, 로깅, WGS84<->기상청 격자 변환, 날짜 청크 유틸리티
"""

import math
import time
import logging
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def setup_logger(name: str = "KVPPCollector", level: int = logging.INFO) -> logging.Logger:
    """통일된 포맷의 로거 생성"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(level)
        ch = logging.StreamHandler()
        ch.setLevel(level)
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        ch.setFormatter(formatter)
        logger.addHandler(ch)
    return logger


class RobustSession:
    """
    공공데이터포털 및 기상청 API 호출을 위한 안정성 세션
    - User-Agent 브라우저 위장 (공공데이터포털 WAF의 403 Forbidden 차단 우회)
    - 429, 500, 502, 503, 504 자동 재시도
    - 인코딩 키 처리
    """

    def __init__(
        self,
        retries: int = 3,
        backoff_factor: float = 1.0,
        timeout: int = 25,
        rate_limit_delay: float = 0.4
    ):
        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay
        self.logger = setup_logger("RobustSession")

        self.session = requests.Session()
        # WAF 403 차단 방지를 위한 브라우저 표준 헤더
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
        })

        retry_strategy = Retry(
            total=retries,
            backoff_factor=backoff_factor,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        max_429_retries: int = 4
    ) -> Dict[str, Any]:
        """
        API GET 요청 수행 후 JSON 파싱
        429 Too Many Requests 발생 시 WAF 쿨다운 시간(4초~12초)을 두고 자동 재시도
        """
        params = dict(params or {})

        for attempt in range(1, max_429_retries + 1):
            if self.rate_limit_delay > 0:
                time.sleep(self.rate_limit_delay)

            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout
                )

                # 1. 일일 트래픽 소진 여부 우선 확인
                resp_text = response.text or ""
                if "LIMITED_NUMBER_OF_SERVICE_REQUESTS" in resp_text or "일일 서비스 요청제한" in resp_text or '"returnReasonCode": "22"' in resp_text or '"returnReasonCode":"22"' in resp_text:
                    self.logger.error(
                        "🛑 [공공데이터포털 일일 쿼터 소진] 오늘 치 API 요청 횟수(1,000회)를 모두 사용했습니다. "
                        "(해결: 공공데이터포털 마이페이지에서 '트래픽 증장' 버튼을 누르면 10만 회로 즉시 무료 증설됩니다)"
                    )
                    raise PermissionError("공공데이터포털 일일 쿼터(1,000회) 소진")

                # 2. 순간 요청 속도 초과에 의한 429일 경우만 쿨다운 대기
                if response.status_code == 429:
                    cooldown = 4.0 * attempt
                    self.logger.warning(
                        f"⚠️ [순간 요청 초과 감지] 공공데이터 서버 쿨다운 대기 ({cooldown:.0f}초 후 재시도 {attempt}/{max_429_retries})..."
                    )
                    time.sleep(cooldown)
                    continue

                response.raise_for_status()

                try:
                    data = response.json()
                    if isinstance(data, dict):
                        header = (
                            data.get("response", {}).get("header", {})
                            or data.get("header", {})
                        )
                        res_code = str(header.get("resultCode", "00"))
                        if res_code not in ["00", "0", "NORMAL_SERVICE", "INFO-000"]:
                            res_msg = header.get("resultMsg", "Unknown Error")
                            self.logger.warning(f"API Warning ({res_code}): {res_msg} | URL: {url}")
                    return data
                except ValueError:
                    return {"error": "Non-JSON response", "raw": response.text[:300]}

            except PermissionError:
                raise
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 429:
                    cooldown = 4.0 * attempt
                    self.logger.warning(
                        f"⚠️ [429 HTTPError] 서버 쿨다운 대기 ({cooldown:.0f}초 후 재시도 {attempt}/{max_429_retries})..."
                    )
                    time.sleep(cooldown)
                    continue
                elif e.response is not None and e.response.status_code == 403:
                    self.logger.error(f"403 Forbidden 발생: {url} | 응답: {e.response.text[:200]}")
                    raise
                else:
                    self.logger.error(f"HTTP 에러: {e}")
                    raise
            except Exception as e:
                err_str = str(e)
                if "429" in err_str:
                    cooldown = 4.0 * attempt
                    self.logger.warning(
                        f"⚠️ [429 연결 에러] 서버 쿨다운 대기 ({cooldown:.0f}초 후 재시도 {attempt}/{max_429_retries})..."
                    )
                    time.sleep(cooldown)
                    continue
                if attempt == max_429_retries:
                    self.logger.error(f"요청 실패: {url} | {e}")
                    raise
                time.sleep(1.0 * attempt)

        raise RuntimeError(f"최대 재시도({max_429_retries}회) 초과: {url}")


class GeoConverter:
    """WGS84 위경도 <-> 기상청 격자좌표(nx, ny) 변환"""
    RE = 6371.00877
    GRID = 5.0
    SLAT1 = 30.0
    SLAT2 = 60.0
    OLON = 126.0
    OLAT = 38.0
    XO = 43
    YO = 136

    @classmethod
    def to_grid(cls, lat: float, lon: float) -> Tuple[int, int]:
        deg2rad = math.pi / 180.0
        re = cls.RE / cls.GRID
        slat1 = cls.SLAT1 * deg2rad
        slat2 = cls.SLAT2 * deg2rad
        olon = cls.OLON * deg2rad
        olat = cls.OLAT * deg2rad

        sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
        sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
        sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
        sf = (math.pow(sf, sn) * math.cos(slat1)) / sn
        ro = math.tan(math.pi * 0.25 + olat * 0.5)
        ro = (re * sf) / math.pow(ro, sn)

        ra = math.tan(math.pi * 0.25 + lat * deg2rad * 0.5)
        ra = (re * sf) / math.pow(ra, sn)
        theta = lon * deg2rad - olon
        if theta > math.pi:
            theta -= 2.0 * math.pi
        if theta < -math.pi:
            theta += 2.0 * math.pi
        theta *= sn

        nx = int(math.floor(ra * math.sin(theta) + cls.XO + 0.5))
        ny = int(math.floor(ro - ra * math.cos(theta) + cls.YO + 0.5))
        return nx, ny


class DateRangeChunker:
    """날짜 범위 청크 분할기"""

    @staticmethod
    def split(start_date: str, end_date: str, chunk_days: int = 30) -> List[Tuple[str, str]]:
        s_clean = start_date.replace("-", "")
        e_clean = end_date.replace("-", "")
        s_dt = datetime.strptime(s_clean, "%Y%m%d")
        e_dt = datetime.strptime(e_clean, "%Y%m%d")

        if s_dt > e_dt:
            raise ValueError(f"시작일({start_date})이 종료일({end_date})보다 늦을 수 없습니다.")

        chunks = []
        cur_dt = s_dt
        while cur_dt <= e_dt:
            next_dt = min(cur_dt + timedelta(days=chunk_days - 1), e_dt)
            chunks.append((cur_dt.strftime("%Y%m%d"), next_dt.strftime("%Y%m%d")))
            cur_dt = next_dt + timedelta(days=1)
        return chunks
