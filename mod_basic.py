from .setup import *
from .model import ModelItem
from .carriers import CARRIERS
import cloudscraper
import re
import traceback
from tool import ToolNotify
from datetime import datetime


class ModuleBasic(PluginModuleBase):
    def __init__(self, P):
        super(ModuleBasic, self).__init__(P, name='basic', first_menu='setting', scheduler_desc="택배 발송 알리미")
        self.db_default = {
            f'db_version' : '1.2',
            f'{self.name}_auto_start': 'False',
            f'{self.name}_interval': '1',
            f'{self.name}_db_delete_day': '7',
            f'{self.name}_db_auto_delete': 'False',
            f'{P.package_name}_item_last_list_option': '',
            f'notify_mode': 'always',
            f'use_delivery_tracking_alarm': 'True',
            f'use_site_대한통운' : 'True',
            f'use_site_경동택배' : 'True',
            f'use_site_한진택배' : 'True',
            f'use_site_우체국택배' : 'True',
            f'use_site_롯데택배' : 'True',
            f'use_site_로젠택배' : 'True',
            f'tracking_no_대한통운' : '',
            f'tracking_no_경동택배' : '',
            f'tracking_no_한진택배' : '',
            f'tracking_no_우체국택배' : '',
            f'tracking_no_롯데택배' : '',
            f'tracking_no_로젠택배' : '',
            'alarm_message_template' : '',
            'alarm_message_template_start' : '',
            'alarm_message_template_end' : ''
        }
        self.web_list_model = ModelItem

    def process_menu(self, sub, req):
        arg = P.ModelSetting.to_dict()
        if sub == 'setting':
            arg['is_include'] = F.scheduler.is_include(
                self.get_scheduler_name())
            arg['is_running'] = F.scheduler.is_running(
                self.get_scheduler_name())
        if sub == 'list':
            arg = self.web_list_model.get_list()
        # 템플릿은 site_map의 key(택배사 이름)만 순회하므로, 트래커 클래스가 담긴
        # CARRIERS 딕셔너리를 넘겨줘도 기존 템플릿을 그대로 쓸 수 있다.
        return render_template(f'{P.package_name}_{self.name}_{sub}.html', arg=arg, site_map=CARRIERS)

    def process_command(self, command, arg1, arg2, arg3, req):
        ret = {'ret': 'success'}
        if command == 'test':
            ret['status'] = 'warn'
            ret['title'] = '테스트'
            ret['data'] = '테스트 내용'
        elif command == 'rename':
            id = arg1
            new_name = arg2
            self.web_list_model.update({'id': id, 'title': new_name})
        elif command == 'complete':
            # 목록에서 '배송완료' 버튼을 눌렀을 때: 사이트가 아직 배송중으로 보여주고 있어도
            # 사용자가 수동으로 완료 처리할 수 있게 한다.
            id = arg1
            result = self.web_list_model.mark_complete(id)
            if result.get('ret') == 'error':
                ret['ret'] = 'error'
                ret['msg'] = result.get('msg', '처리에 실패했습니다.')
        return jsonify(ret)

    def scheduler_function(self):
        self.scrap_items()

    def scrap_items(self):
        try:
            sites = [key.split('_')[-1] for key in list(self.db_default.keys()) if key.startswith('use_site_')]
            use_delivery_sites = [key.split('_')[-1] for key in sites if P.ModelSetting.get(f'use_site_{key}') == 'True']

            for site_name in use_delivery_sites:
                try:
                    tracker_cls = CARRIERS.get(site_name)
                    if tracker_cls is None:
                        continue

                    track_nos_raw = ModelItem.get_track_no(site_name)
                    if not track_nos_raw:
                        continue
                    track_nos = [t for t in track_nos_raw.replace(' ', '').split(',') if t]
                    if not track_nos:
                        continue

                    for track_no in track_nos:
                        try:
                            tracker = tracker_cls()
                            scraper = cloudscraper.create_scraper()
                            info, tracking = tracker.track(scraper, track_no)

                            # 둘 다 완전히 실패(=이번 폴링에서 얻은 실제 정보가 하나도 없음)했다면
                            # 그냥 건너뛴다. 그렇지 않으면 status가 '상태정보없음'이라는 placeholder로
                            # 채워져 실제 배송 상태를 덮어쓰거나 잘못된 알림을 보낼 수 있다.
                            # (한쪽만 실패한 경우는 계속 진행 - 나머지 한쪽 정보로 정상 갱신 가능)
                            if info is None and tracking is None:
                                # 실제 경고 로그는 carriers.py::CarrierBase.track()에서
                                # (모든 택배사 공통으로) 한 번만 남긴다. 여기서는 skip만 한다.
                                continue

                            info = info or {}
                            tracking = tracking or {}

                            if 'item_name' in info or 'item_name' in tracking:
                                item_name = info.get('item_name') or tracking.get('item_name') or track_no
                            else:
                                item_name = track_no
                            if not item_name or item_name.strip() == '':
                                item_name = track_no

                            # tracking_status/status/datetime 등은 값이 없을 때 키 자체가 없는 게 아니라
                            # None으로 채워져 들어올 수 있다. dict.get(key, default)는 키가 존재하면
                            # 값이 None이어도 default로 대체해주지 않으므로 'or' 체인으로 명시적으로 처리한다.
                            update_data = {
                                'site_name': site_name,
                                'title': item_name,
                                'track_no': track_no,
                                'status': tracking.get('tracking_status') or info.get('status') or '상태정보없음',
                                'datetime': info.get('datetime') or tracking.get('datetime') or datetime.now().strftime('%Y-%m-%d %H:%M'),
                                'tracking_location': tracking.get('tracking_location') or ''
                            }
                            update_result = self.web_list_model.update(update_data)
                            if 'code' in update_result:
                                self.process_discord_data(update_data, update_result)
                        except Exception as e:
                            P.logger.error(f'택배 추적 중 오류 발생 - {site_name} / {track_no}: {str(e)}')
                            P.logger.error(traceback.format_exc())
                except Exception as e:
                    P.logger.error(f'사이트 처리 중 오류 발생 - {site_name}: {str(e)}')
                    P.logger.error(traceback.format_exc())
        except Exception as e:
            P.logger.error(f'전체 스크래핑 중 오류 발생: {str(e)}')
            P.logger.error(traceback.format_exc())

    def process_discord_data(self, update_data, update_result):
        code = update_result.get('code') if isinstance(update_result, dict) else update_result
        if P.ModelSetting.get_bool('use_delivery_tracking_alarm') is False or code == 'skip':
            return

        # model.update()가 돌려준(사용자가 이름을 변경했다면 그 이름이 반영된) 제목을 우선 사용한다.
        # 그렇지 않으면 항상 스크래핑 시점의 원본 품목명이 알림에 쓰여, 사용자가 목록에서
        # 이름을 바꿔도 디스코드 메시지에는 반영되지 않는 문제가 있었다.
        display_data = dict(update_data)
        if isinstance(update_result, dict) and update_result.get('title'):
            display_data['title'] = update_result['title']

        msg = ''

        if code == 'remove':
            msg = P.ModelSetting.get('alarm_message_template_end')
            if msg == None or msg.strip() == '':
                msg = '[추적 완료] '
                msg += '배송이 완료되었습니다.'
                msg += f"\n{display_data['title']} - {display_data['track_no']} - {display_data['status']} - {display_data['datetime']} - {display_data['tracking_location']}"
            else:
                msg = msg.format(**display_data)
        elif code == 'insert':
            msg = P.ModelSetting.get('alarm_message_template_start')
            if msg == None or msg.strip() == '':
                msg = '[추적 시작] '
                msg += '배송 추적이 등록되었습니다.'
                msg += f"\n{display_data['title']} - {display_data['track_no']} - {display_data['status']} - {display_data['datetime']} - {display_data['tracking_location']}"
            else:
                msg = msg.format(**display_data)
        elif code == 'update':
            msg = P.ModelSetting.get('alarm_message_template')
            if msg == None or msg.strip() == '':
                msg = '[상태 변경] '
                msg += '배송 상태가 변경되었습니다.'
                msg += f"\n{display_data['title']} - {display_data['track_no']} - {display_data['status']} - {display_data['datetime']} - {display_data['tracking_location']}"
            else:
                msg = msg.format(**display_data)
        # msg에서 <strong> 태그를 *로 변경
        msg = re.sub(r'<strong>(.*?)</strong>', r'*\1*', msg)
        ToolNotify.send_message(msg, message_id=f'bot_delivery_tracking_alarm')
