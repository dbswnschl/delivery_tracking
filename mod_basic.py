from .setup import *
from .model import ModelItem
import requests
import re
import time
import cloudscraper
import html
import os
import json
from tool import ToolNotify
import traceback

site_map = {
    '대한통운' : {
        'url' : 'http://nplus.doortodoor.co.kr/web/detail.jsp?slipno={track_no}',
        'type' : 'html',
        'regex_info' : r'<tr height=22 align=center bgcolor=#F6F6F6>\s+<td>\s&nbsp;(?P<from>[\w\*]+)&nbsp;\s<\/td>\s+<td>\s&nbsp;(?P<to>[\w\*]+)&nbsp;\s<\/td>\s+<td>\s&nbsp;(?P<count>[\w\*]+)&nbsp;\s<\/td>\s+<td>\s&nbsp;(?P<insu>[\w\*]+)&nbsp;\s<\/td>\s+\</tr>',
        'regex_tracking' : r'<tr height=22 bgcolor=#F6F6F6>\s+<td align=center>&nbsp;(?P<date>\d{4}-\d{2}-\d{2})&nbsp;</td>\s+<td align=center>&nbsp;(?P<time>\d+:\d+:\d+)&nbsp;</td>[\S\W]+?<td width=140>&nbsp;(?P<track>\w+)&nbsp;</td>[\S\W]+?<td align=center>&nbsp;(?P<status>\w+)&nbsp;</td>'    
    },
    '경동택배' : {
        'url' : 'https://kdexp.com/service/delivery/ajax_basic.do?barcode={track_no}',
        'type' : 'json'
    },
    '한진택배' : {
        'url' : 'https://www.hanjin.com/kor/CMS/DeliveryMgr/WaybillResult.do?mCode=MN038&schLang=KR&wblnumText2={track_no}',
        'type' : 'html'
    },
    '우체국택배' : {
        'url' : 'https://service.epost.go.kr/trace.RetrieveDomRigiTraceList.comm?displayHeader=N&sid1={track_no}',
        'type' : 'html'
    },
    '롯데택배' : {
        'url' : 'https://www.lotteglogis.com/mobile/reservation/tracking/linkView?InvNo={track_no}',
        'type' : 'html'
    }
}


class ModuleBasic(PluginModuleBase):
    def __init__(self, P):
        super(ModuleBasic, self).__init__(P, name='basic', first_menu='setting', scheduler_desc="택배 발송 알리미")
        self.db_default = {
            f'db_version' : '1.0',
            f'{self.name}_auto_start': 'False',
            f'{self.name}_interval': '1',
            f'{self.name}_db_delete_day': '7',
            f'{self.name}_db_auto_delete': 'False',
            f'{P.package_name}_item_last_list_option': '',
            f'notify_mode': 'always',
            f'use_delivery_sites' : '대한통운'
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
        return render_template(f'{P.package_name}_{self.name}_{sub}.html', arg=arg, site_map=site_map)
    def process_command(self, command, arg1, arg2, arg3, req):
        ret = {'ret': 'success'}
        if command == 'test':
            ret['status'] = 'warn'
            ret['title'] = '테스트'
            ret['data'] = '테스트 내용'
        return jsonify(ret)

    def scheduler_function(self):
        self.scrap_items()
    
    
    def scrap_items(self):
        ret = {
            'status': 'success',
            'data': []
        }
        P.logger.info("scrap_items")
        sess = requests.session()
        use_delivery_sites = P.ModelSetting.get('use_delivery_sites', '').replace(' ','').split(',')
        for site_name in use_delivery_sites:
            if site_name in site_map:
                track_no = ModelItem.get_track_no(site_name)
                site_url = site_map[site_name]['url']
                site_type = site_map[site_name]['type']
                if 'regex_info' in site_map[site_name]:
                    regex_info = site_map[site_name]['regex_info']
                if 'regex_tracking' in site_map[site_name]:
                    regex_tracking = site_map[site_name]['regex_tracking']
                if site_type == 'html':
                    scraper = cloudscraper.create_scraper()
                    response = scraper.get(site_url.format(track_no=track_no))
                    response = response.text
                    if 'regex_info' in site_map[site_name]:
                        info = re.findall(regex_info, response)
                    if 'regex_tracking' in site_map[site_name]:
                        tracking = re.findall(regex_tracking, response)
                    P.logger.info(f'{site_name} {info} {tracking}')