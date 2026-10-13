import json
from pathlib import Path

from core.product_lines import PRODUCT_LINES, WIRELESS_CONNECTION


def test_confirmed_manual_qa_roster_has_unique_accounts_and_exact_line_memberships():
    personnel = json.loads((Path(__file__).resolve().parents[3] / 'config' / 'personnel.json').read_text(encoding='utf-8'))
    qa = personnel['amlogic']['departments']['FAE-QA']['employees']
    by_account = {person['account']: person for person in qa if person['account']}
    assert len(by_account) == sum(bool(person['account']) for person in qa)
    expected = {
        PRODUCT_LINES[0].name: 'lingling.yu,fan.xu,chuanyang.hu,shouneng.qiu,chao.lu,zhaoqun.wang',
        PRODUCT_LINES[1].name: 'chen.chen,weiting.feng,zhuhui.zhang,nannan.meng,taoqing.miao,shuangshuang.feng',
        PRODUCT_LINES[2].name: 'jianfan.ai,meiling.zhu,bo.ren,qin.zhang,xiaofeng.li,changwen.dai,zhewu.tao,tracy.chen,zanbo.huang,xing.fan,xuejiao.li,xiaoshuang.ni,zhijie.yang,tianxiang.xie,maoguo.xie,shuangxiao.hu,linguo.bu,cong.zhang,haolin.li,mingdong.wang,yunzhu.zhang,xiaoli.peng,menghui.liu,yanyan.deng,chunyan.liu,xinying.yang,jianhua.huang,jianhui.peng,zhangxian.chen,qianyi.liu,jinbo.du,zhendong.zhou,binbin.gao,jie.xiong,yimin.zhou,zeqin.lai,lianhong.wang,linqi.chen,jiaxuan.ren,wencan.xue,yan.wu,haobo.ren,zonghao.ma,bing.song,qiaowei.tian',
        PRODUCT_LINES[3].name: 'junjie.li,xiangqun.li,kai.ni,shaochun.chen,chenghua.liu,jinhuan.yi,mao.ma,kang.jiang,wenjie.liu,jian.zhong,leping.lei,yanqing.tang',
        WIRELESS_CONNECTION.name: 'zijie.chen,yonghua.yan,ling.chen,yuxiang.song,meng.wang1,junbin.lin,yifeng.xu,mengnan.hu,bo.meng,hanpeng.su,yu.zhang1,chao.li',
    }
    for line, accounts in expected.items():
        actual = {person['account'] for person in qa if any(item['product_line_id'] == line for item in person['assignments'])}
        assert actual == set(accounts.split(','))
    assert by_account['linguo.bu']['display_name'] == 'Linguo Bu'
    assert by_account['tianxiang.xie']['display_name'] == 'Tianxiang Xie'
    assert 'lingguo.bu' not in by_account and 'tianwei.xie' not in by_account
    assert by_account['xiaoshuang.ni']['display_name'] == 'Xiaoshuang Ni'
    assert by_account['zonghao.ma']['assignments'] == [{'product_line_id': PRODUCT_LINES[2].name, 'responsibilities': []}]
    assert not {'shaojun.chen', 'shouneng.chou', 'zongwu.ma', 'mennan.hu'} & set(by_account)
    assert 'jiajia.mu' not in by_account
    assert personnel['amlogic']['departments']['FAE-SW']['employees'][0]['assignments'][0]['product_line_id'] == PRODUCT_LINES[1].name
