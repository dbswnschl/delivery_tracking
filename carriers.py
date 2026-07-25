# -*- coding: utf-8 -*-
"""
택배사별 스크래핑 로직.

기존에는 mod_basic.py 최상단의 거대한 site_map 딕셔너리 하나에 URL/요청 방식/파싱 규칙이
전부 뒤섞여 있었고, 파싱 함수들(get_summary_info/get_tracking_info/process_tracking_response/
process_datetime 등)이 그 딕셔너리를 조건문으로 해석하는 구조였다. 택배사 하나를 추가하거나
정규식 하나를 고치려 해도 전체 흐름을 다 봐야 했다.

여기서는 택배사마다 다음 두 메서드를 갖는 클래스로 분리했다.
    - fetch_summary(scraper, track_no) -> dict | None
        보내는분/받는분/품목명/(있다면) 상태 등 '요약 정보'
    - fetch_tracking(scraper, track_no) -> dict | None
        가장 최근 배송 이벤트 1건 (tracking_status/datetime/tracking_location)

공통 로직(JSON API 방식, HTML 정규식 방식)은 각각 베이스 클래스에 있고,
실제 택배사 클래스는 URL/정규식/키 매핑 같은 '데이터'만 클래스 속성으로 선언하면 된다.
"""
import re
import json
from datetime import datetime

from .setup import *  # noqa: F401,F403  (P, traceback 등 플러그인 공통 객체 사용)


class CarrierBase:
    name = None

    def fetch_summary(self, scraper, track_no):
        raise NotImplementedError

    def fetch_tracking(self, scraper, track_no):
        raise NotImplementedError

    def track(self, scraper, track_no):
        """요약 정보 + 최신 배송 상태를 한 번에 가져온다.

        요약/추적 정보 중 하나만 실패하고 다른 하나가 성공하면, 성공한 쪽 정보로
        상태가 정상적으로 갱신될 수 있으므로 여기서는 조용히 넘어간다.
        둘 다 실패해서 이번 조회에서 얻은 정보가 하나도 없을 때만 경고를 한 번 남긴다.
        (이 메서드는 모든 택배사가 공통으로 쓰므로, 여기 한 곳만 고치면 6개 택배사
        전부에 동일하게 적용된다.)
        """
        info, info_error = None, None
        try:
            info = self.fetch_summary(scraper, track_no)
        except Exception as e:
            info_error = str(e)
            P.logger.debug(traceback.format_exc())

        tracking, tracking_error = None, None
        try:
            tracking = self.fetch_tracking(scraper, track_no)
        except Exception as e:
            tracking_error = str(e)
            P.logger.debug(traceback.format_exc())

        if info is None and tracking is None:
            reason = info_error or tracking_error
            if reason:
                P.logger.warning(f'[{self.name}] 배송 정보를 가져오지 못했습니다. ({reason}) (track_no={track_no})')
            else:
                P.logger.warning(
                    f'[{self.name}] 배송 정보를 가져오지 못했습니다. '
                    f'사이트 응답 형식이 바뀌었거나 일시적인 오류일 수 있습니다. (track_no={track_no})'
                )

        return info, tracking


# ---------------------------------------------------------------------------
# JSON API 방식 (대한통운, 경동택배)
# ---------------------------------------------------------------------------
class JsonApiCarrierTracker(CarrierBase):
    """요약/추적 정보를 각각 별도의 JSON API 호출로 가져오는 택배사."""

    summary_config = None
    tracking_config = None

    def _request_json(self, scraper, config, track_no):
        method = config.get('method', 'get')
        if method == 'get':
            response = scraper.get(config['url'].format(track_no=track_no))
        else:
            req_data = json.dumps(config['data']).replace('{track_no}', track_no)
            req_data = json.loads(req_data)
            response = scraper.post(config['url'], data=req_data)
        return response.json()

    @staticmethod
    def _resolve_result_key(response_data, result_key):
        """'data.svcOutList' 같은 점(.) 표기 key를 응답 dict에서 찾아 매핑."""
        info = {}
        for key, value in result_key.items():
            if '.' in key:
                key_split = key.split('.')
                if key_split[0] in response_data and response_data[key_split[0]] is not None:
                    info[value] = response_data[key_split[0]].get(key_split[1], '')
                else:
                    info[value] = ''
            else:
                info[value] = response_data.get(key, '')
        return info

    def fetch_summary(self, scraper, track_no):
        if not self.summary_config:
            return None
        response_data = self._request_json(scraper, self.summary_config, track_no)
        result_key = self.summary_config.get('result_key')
        if not result_key:
            return response_data
        info = self._resolve_result_key(response_data, result_key)
        if info.get('datetime'):
            info['datetime'] = info['datetime'].split('.')[0]
        return info

    def fetch_tracking(self, scraper, track_no):
        response_data = self._request_json(scraper, self.tracking_config, track_no)
        result_key = self.tracking_config.get('result_key')
        tracking = {}

        if result_key:
            for key, value in result_key.items():
                items = None
                if '.' in key:
                    key_split = key.split('.')
                    if key_split[0] in response_data and response_data[key_split[0]] is not None:
                        items = response_data[key_split[0]].get(key_split[1])
                else:
                    items = response_data.get(key)
                tracking = items[-1] if items else {}

        datetime_key = self.tracking_config.get('datetime_key')
        if datetime_key:
            if isinstance(datetime_key, str):
                if tracking.get(datetime_key):
                    tracking['datetime'] = tracking[datetime_key].split('.')[0]
            elif isinstance(datetime_key, dict):
                date_v = tracking.get(datetime_key['date'])
                time_v = tracking.get(datetime_key['time'])
                if date_v and time_v:
                    tracking['datetime'] = f"{date_v} {time_v.split('.')[0]}"

        status_key = self.tracking_config.get('status_key')
        tracking['tracking_status'] = tracking.get(status_key) if status_key else tracking.get('tracking_status')

        location_key = self.tracking_config.get('tracking_location_key')
        tracking['tracking_location'] = tracking.get(location_key, '') if location_key else tracking.get('tracking_location', '')
        if tracking.get('tracking_location'):
            tracking['tracking_location'] = re.sub(r'<[^>]*>', '', tracking['tracking_location'].strip())

        return tracking


class CjTracker(JsonApiCarrierTracker):
    name = '대한통운'
    summary_config = {
        'url': 'https://trace.cjlogistics.com/next/rest/selectTrackingWaybil.do',
        'method': 'post',
        'data': {'wblNo': '{track_no}'},
        'result_key': {
            'data.acprNm': 'to',
            'data.sndrNm': 'from',
            'data.repGoodsNm': 'item_name',
        },
    }
    tracking_config = {
        'url': 'https://trace.cjlogistics.com/next/rest/selectTrackingDetailList.do',
        'method': 'post',
        'data': {'wblNo': '{track_no}'},
        'result_key': {'data.svcOutList': 'tracking_list'},
        'datetime_key': {'date': 'workDt', 'time': 'workHms'},
        'status_key': 'crgStDnm',
        'tracking_location_key': 'branNm',
    }


class KyungdongTracker(JsonApiCarrierTracker):
    name = '경동택배'
    summary_config = {
        'url': 'https://kdexp.com/service/delivery/ajax_basic.do?barcode={track_no}',
        'result_key': {
            'info.branch_start': 'from',
            'info.branch_end': 'to',
            'info.prod': 'item_name',
            'info.pd_dt': 'datetime',
        },
    }
    tracking_config = {
        'url': 'https://kdexp.com/service/delivery/ajax_basic.do?barcode={track_no}',
        'result_key': {'items': 'tracking_list'},
        'datetime_key': 'reg_date',
        'status_key': 'stat',
        'tracking_location_key': 'location',
    }


# ---------------------------------------------------------------------------
# HTML 정규식 방식 (한진택배, 우체국택배, 롯데택배, 로젠택배)
# ---------------------------------------------------------------------------
class HtmlRegexCarrierTracker(CarrierBase):
    """단일 HTML 페이지를 받아 정규식으로 요약/추적 정보를 뽑아내는 택배사.

    fetch_summary()와 fetch_tracking()이 별도로 호출되어도 같은 track_no에 대해서는
    HTML을 한 번만 받아오도록 인스턴스 단위로 캐시한다. (mod_basic.py에서
    택배 1건마다 트래커 인스턴스를 새로 만들어 쓰므로 인스턴스 캐시로 충분하다.)
    """

    url_template = None
    regex_info = None
    regex_tracking = None
    datetime_key = None
    time_format = None
    order = 'asc'

    def __init__(self):
        self._html_cache = None

    def _get_html(self, scraper, track_no):
        if self._html_cache is None:
            response = scraper.get(self.url_template.format(track_no=track_no))
            self._html_cache = response.text
        return self._html_cache

    def _apply_datetime(self, d, track_no):
        if not d:
            return
        if self.datetime_key:
            date_v = d.get(self.datetime_key['date'])
            time_v = d.get(self.datetime_key['time'])
            if date_v and time_v:
                d['datetime'] = f'{date_v} {time_v}'
        if self.time_format and d.get('datetime'):
            try:
                d['datetime'] = datetime.strptime(d['datetime'], self.time_format)
            except Exception:
                try:
                    d['datetime'] = datetime.strptime(d['datetime'].split('.')[0], '%Y-%m-%d %H:%M')
                except Exception:
                    d['datetime'] = datetime.now()

    def fetch_summary(self, scraper, track_no):
        if not self.regex_info:
            return None
        html_text = self._get_html(scraper, track_no)
        m = re.search(self.regex_info, html_text)
        if not m:
            # 여기서는 경고를 남기지 않는다. regex_tracking 쪽이 성공하면 정상적으로
            # 배송 상태가 갱신되는 경우가 많아서, 이 시점에 매번 경고를 남기면
            # "실제로는 문제없는" 케이스에서도 계속 경고가 뜨게 된다.
            # 정말로 정보를 하나도 못 가져온 경우(둘 다 실패)의 경고는
            # mod_basic.py::scrap_items에서 한 번만 남긴다.
            return None
        info = m.groupdict()
        self._apply_datetime(info, track_no)
        return info

    def fetch_tracking(self, scraper, track_no):
        if not self.regex_tracking:
            return None
        html_text = self._get_html(scraper, track_no)
        matches = list(re.finditer(self.regex_tracking, html_text))
        if not matches:
            # fetch_summary와 마찬가지로 여기서는 경고를 남기지 않는다.
            return None
        tracking = matches[0].groupdict() if self.order == 'desc' else matches[-1].groupdict()
        self._apply_datetime(tracking, track_no)
        if tracking.get('tracking_location'):
            tracking['tracking_location'] = re.sub(r'<[^>]*>', '', tracking['tracking_location'].strip())
        if tracking.get('tracking_status'):
            # 한진택배 등 일부 사이트는 상태 문구를 <strong>배송완료</strong>하였습니다. 처럼
            # 태그로 감싸서 내려준다. tracking_location과 동일하게 태그를 벗겨내지 않으면
            # 목록/알림에 '완료'라는 글자는 정상 포함되어 있어도(감지는 됨) 화면에 태그가
            # 그대로 노출된다.
            tracking['tracking_status'] = re.sub(r'<[^>]*>', '', tracking['tracking_status'].strip())
        return tracking


class HanjinTracker(HtmlRegexCarrierTracker):
    name = '한진택배'
    url_template = 'https://www.hanjin.com/kor/CMS/DeliveryMgr/WaybillResult.do?mCode=MN038&schLang=KR&wblnumText2={track_no}'
    regex_info = r'<p class=\"cal-sec\"><span class=\"date\">(?P<date>[\d\-]{10})</span>\s<span class=\"time\">(?P<time>[\d\:]{5})</span></p>[\W\S]+?<strong>(?P<status>\w+)</strong>[\W\S]+?data-label=\"상품명\">(?P<item_name>.*)</td>\s+<td data-label=\"보내는 분\">(?P<from>.+)</td>\s+<td data-label=\"받는 분\">(?P<to>.+)'
    regex_tracking = r'<td class=\"w-org\">(?P<tracking_location>[^<]+)</td>[\W\S]+?<span class=\"stateDesc\">(?P<tracking_status>.+)</span>'
    datetime_key = {'date': 'date', 'time': 'time'}
    time_format = '%Y.%m.%d %H:%M'
    order = 'asc'


class EpostTracker(HtmlRegexCarrierTracker):
    name = '우체국택배'
    url_template = 'https://service.epost.go.kr/trace.RetrieveDomRigiTraceList.comm?displayHeader=N&sid1={track_no}'
    regex_info = r'<tbody>\s+<tr>[\W\S]+?<td>(?P<from>[^<]+)[\W\S]+?<td>(?P<to>[^<]+)[\W\S]+?<td>(?P<item_name>[^<]+)</td>[\W\S]+?(?P<status>[^<]+)</td>\s+</tr>'
    regex_tracking = r'<tr>[\W\S]+?<td>(?P<date>[\d\.]{10})</td>\s+<td>(?P<time>[\d\:]{5})</td>\s+<td>(?P<tracking_location>[\W\S]+?)</td>\s+<td>\s+<span class=\"evtnm\">(?P<tracking_status>[^<]+)</span>'
    datetime_key = {'date': 'date', 'time': 'time'}
    time_format = '%Y.%m.%d %H:%M'
    order = 'asc'


class LotteTracker(HtmlRegexCarrierTracker):
    name = '롯데택배'
    url_template = 'https://www.lotteglogis.com/mobile/reservation/tracking/linkView?InvNo={track_no}'
    regex_info = r'<th scope=\"row\">발송지</th>\s+<td>(?P<from>.+)</td>\s+</tr>\s+<tr>\s+<th scope=\"row\">도착지</th>\s+<td>(?P<to>.+)</td>\s+</tr>\s+<tr>\s+<th scope=\"row\">배달결과</th>\s+<td>(?P<status>.+)</td>'
    regex_tracking = r'<tr>\s+<td>(?P<tracking_status>[^<]+)</td>\s+<td>\s+(?P<date>[\w\-]{10})&nbsp;(?P<time>[\d\:]{5})\s+</td>\s+<td>\s+(?P<tracking_location>.+)\s+</td>\s+<td class=\"left01\">'
    datetime_key = {'date': 'date', 'time': 'time'}
    time_format = '%Y-%m-%d %H:%M'
    order = 'desc'


class LogenTracker(HtmlRegexCarrierTracker):
    name = '로젠택배'
    url_template = 'https://www.ilogen.com/m/personal/trace/{track_no}'
    regex_info = r'<th>보내시는 분</th>\s+<td>(?P<from>.+)</td>\s+</tr>\s+<tr>\s+<th>받으시는 분</th>\s+<td>(?P<to>.+)</td>\s+</tr>\s+<tr>\s+<th>상품명</th>\s+<td>(?P<item_name>.+)</td>[\W\S]+<th>최종처리시간</th>\s+<td>(?P<date>[\d\.]{10})\s+(?P<time>[\d\:]{5})\s+(?P<status>.+)</td>'
    regex_tracking = r'<tr>\s+<td>(?P<date>[\d\.]{10})\s+(?P<time>[\d\:]{5})</td>\s+<td>\s+(?P<tracking_status>.+)\s+</td>\s+<td>[\w\<\>\=\"\#\/\:\-\s]+?</tr>\s+</tbody>\s+</table>[\W\S]+?<th>최종처리 사업장</th>\s+<td>(?P<tracking_location>.+)</td>'
    datetime_key = {'date': 'date', 'time': 'time'}
    time_format = '%Y.%m.%d %H:%M'
    order = 'asc'


# site_name(문자열) -> 트래커 클래스. 순서는 기존 site_map과 동일하게 유지.
CARRIERS = {
    '대한통운': CjTracker,
    '경동택배': KyungdongTracker,
    '한진택배': HanjinTracker,
    '우체국택배': EpostTracker,
    '롯데택배': LotteTracker,
    '로젠택배': LogenTracker,
}
