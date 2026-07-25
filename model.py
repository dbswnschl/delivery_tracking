from .setup import *
from sqlalchemy import and_, desc, func, not_, or_
from datetime import datetime
class ModelItem(ModelBase):
    P = P
    __tablename__ = 'delivery_tracking'
    __table_args__ = {'mysql_collate': 'utf8_general_ci'}
    __bind_key__ = P.package_name
    id = db.Column(db.Integer, primary_key=True)
    created_time = db.Column(db.DateTime)
    site_name = db.Column(db.String)
    track_no = db.Column(db.String)
    title = db.Column(db.String)
    status = db.Column(db.String)
    datetime = db.Column(db.DateTime)
    alarm_status = db.Column(db.Boolean)
    tracking_location = db.Column(db.String)

    def __init__(self):
        self.created_time = datetime.now()
        self.alarm_status = False
    @classmethod
    def update(cls, data):
        ret = {}

        if 'datetime' in data:
            if type(data['datetime']) == str:
                try:
                    data['datetime'] = datetime.strptime(data['datetime'], '%Y-%m-%d %H:%M:%S')
                except Exception as e:
                    data['datetime'] = datetime.strptime(data['datetime'], '%Y-%m-%d %H:%M')

        if 'id' in data:
            already_item = cls.get_by_id(data['id'])
            if 'status' not in data and 'title' in data:
                already_item[0].title = data['title']
                already_item[0].save()
                return {'code' : 'rename'}

        else:
            already_item = cls.get_by_track_no(data['track_no'], data.get('site_name'))

        # 사이트 파싱 실패 등으로 상태값을 얻지 못한 경우(None) DB를 건드리지 않고 건너뛴다.
        # (기존에는 None 상태가 그대로 흘러가 '완료' in data['status'] 에서 TypeError 발생)
        if data.get('status') is None:
            return {'code': 'skip'}

        if already_item:
            db_item = already_item[0]
            is_terminal = ('완료' in data['status']) or ('고객님께 물품을 전달' in data['status'])

            if is_terminal:
                # 이미 완료 처리(알림까지 발송)된 건이면 재알림하지 않는다.
                # 기존 코드는 '이전 상태 == 이번 상태'일 때만 완료로 판단했는데,
                # 완료 문구가 처음 나타나는 시점엔 이전 상태와 다르므로 이 조건을 못 타
                # 1) 상태변경 알림 -> 2) 다음 폴링에서 완료 알림 이 두 번 발송되는 문제가 있었다.
                # 완료 문구가 감지되면(이전 상태와 같은지 여부와 무관하게) 즉시 완료 처리한다.
                if db_item.alarm_status and db_item.status == data['status']:
                    return {'code': 'skip'}

                db_item.site_name = data['site_name']
                db_item.status = data['status']
                db_item.datetime = data['datetime']
                db_item.tracking_location = data.get('tracking_location', '')
                db_item.alarm_status = True
                db_item.save()

                tracking_nos_raw = P.ModelSetting.get(f'tracking_no_{db_item.site_name}')
                if tracking_nos_raw:
                    tracking_nos = [t for t in tracking_nos_raw.replace(' ', '').split(',') if t]
                    if data['track_no'] in tracking_nos:
                        tracking_nos.remove(data['track_no'])
                        P.ModelSetting.set(f'tracking_no_{db_item.site_name}', ','.join(tracking_nos))
                else:
                    # 설정에 해당 택배사의 송장번호 키 자체가 없는 경우(예: 과거 버전에서 site_name이
                    # 바뀌어 DB에는 옛 이름이 남아있는 경우). 여기서 P.ModelSetting.set()을 호출하면
                    # 프레임워크 내부에서 존재하지 않는 setting에 값을 쓰려다 죽는다(entity.value on None).
                    # 알림/DB 갱신은 정상 진행하고, 설정에서의 자동 제거만 건너뛴다.
                    P.logger.warning(f'설정 키 tracking_no_{db_item.site_name} 가 없어 자동 제거를 건너뜁니다. (site_name 불일치 가능성)')

                return {'code': 'remove', 'title': db_item.title}

            if db_item.status == data['status']:
                return {'code' : 'skip'}

            db_item.site_name = data['site_name']
            # db_item.title = data['title']  # 사용자가 이름 변경한 제목을 스크랩 결과로 덮어쓰지 않기 위해 유지
            db_item.status = data['status']
            db_item.datetime = data['datetime']
            db_item.alarm_status = data.get('alarm_status', False)
            db_item.tracking_location = data.get('tracking_location', '')
            db_item.save()
            ret['ret'] = 'success'
            ret['msg'] = '업데이트 하였습니다.'
            ret['code'] = 'update'
            ret['title'] = db_item.title
        else:
            db_item = ModelItem()
            db_item.site_name = data['site_name']
            db_item.title = data['title']
            db_item.track_no = data['track_no']
            db_item.status = data['status']
            db_item.datetime = data['datetime']
            db_item.alarm_status = data.get('alarm_status', False)
            db_item.tracking_location = data.get('tracking_location', '')
            db_item.save()
            ret['ret'] = 'success'
            ret['msg'] = '업데이트 하였습니다.'
            ret['code'] = 'insert'
            ret['title'] = db_item.title
        return ret

    @classmethod
    def mark_complete(cls, id):
        """목록의 '배송완료' 버튼에서 호출.

        택배사 사이트가 아직 배송중으로 보여주고 있어도(또는 정규식이 못 맞춰서)
        사용자가 실제로는 이미 받았다는 걸 알 때 수동으로 완료 처리한다.
        자동 완료 감지(update() 안의 is_terminal 분기)와 동일하게 상태 문구를 바꾸고,
        더 이상 스케줄러가 이 송장번호를 폴링하지 않도록 설정에서도 제거한다.
        """
        try:
            with F.app.app_context():
                already_item = cls.get_by_id(id)
                if not already_item:
                    return {'ret': 'error', 'msg': '대상을 찾을 수 없습니다.'}

                db_item = already_item[0]
                db_item.status = '배송이 완료되었습니다. (수동 처리)'
                db_item.alarm_status = True
                db_item.save()

                tracking_nos_raw = P.ModelSetting.get(f'tracking_no_{db_item.site_name}')
                if tracking_nos_raw:
                    tracking_nos = [t for t in tracking_nos_raw.replace(' ', '').split(',') if t]
                    if db_item.track_no in tracking_nos:
                        tracking_nos.remove(db_item.track_no)
                        P.ModelSetting.set(f'tracking_no_{db_item.site_name}', ','.join(tracking_nos))
                else:
                    P.logger.warning(f'설정 키 tracking_no_{db_item.site_name} 가 없어 자동 제거를 건너뜁니다. (site_name 불일치 가능성)')

                return {'ret': 'success', 'msg': '배송 완료로 처리했습니다.'}
        except Exception as e:
            P.logger.error(f'Exception:{str(e)}')
            P.logger.error(traceback.format_exc())
            return {'ret': 'error', 'msg': str(e)}

    @classmethod
    def get_list(cls):
        ret = super().get_list(by_dict=True)
        return ret
    @classmethod
    def get_alarm_target_list(cls):
        try:
            with F.app.app_context():
                query = F.db.session.query(cls).filter_by(alarm_status=False)
                return query.all()

        except Exception as e:
            cls.P.logger.error(f'Exception:{str(e)}')
            cls.P.logger.error(traceback.format_exc())
    @classmethod
    def get_by_id(cls, id):
        try:
            with F.app.app_context():
                query = F.db.session.query(cls).filter_by(id=id)
                query = query.order_by(desc(cls.created_time))
                return query.all()

        except Exception as e:
            cls.P.logger.error(f'Exception:{str(e)}')
            cls.P.logger.error(traceback.format_exc())
        return None
    @classmethod
    def get_by_track_no(cls, track_no, site_name=None):
        # site_name도 함께 필터링하여, 서로 다른 택배사가 우연히 같은 형식의
        # 운송장번호를 사용하는 경우 다른 건과 뒤섞이지 않도록 한다.
        try:
            with F.app.app_context():
                query = F.db.session.query(cls).filter_by(track_no=track_no)
                if site_name:
                    query = query.filter_by(site_name=site_name)
                query = query.order_by(desc(cls.created_time))
                return query.all()

        except Exception as e:
            cls.P.logger.error(f'Exception:{str(e)}')
            cls.P.logger.error(traceback.format_exc())
        return None
    @classmethod
    def make_query(cls, req, order='desc', search='', option1='all', option2='all'):
        with F.app.app_context():
            query = cls.make_query_search(
                F.db.session.query(cls), search, cls.title)

            if option1 != 'all':
                query = query.filter(cls.site_name == option1)

            if option2 != 'all':
                query = query.filter(cls.track_no == option2)

            if order == 'desc':
                query = query.order_by(desc(cls.created_time))
            else:
                query = query.order_by(cls.created_time)

            return query
    @classmethod
    def get_track_no(cls, site_name):
        return P.ModelSetting.get(f'tracking_no_{site_name}')
