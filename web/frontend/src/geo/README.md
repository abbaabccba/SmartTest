# Global 背景地理素材

`global-boundaries.json` 是本地展示素材，不代表部署范围或业务查询结果。

- 陆地：Natural Earth 1:110m，world-atlas 2.0.2 `countries-110m.json` 的 land。
- 常驻国境线：同一1:110m数据的 countries，以临时 topojson-client `mesh(topology, countries, (a,b) => a !== b)` 提取共享国境；沿海轮廓由 land 保持。159条线、2807坐标，不引入全部50m国家面重绘。
- 国家/地区：Natural Earth 1:50m，world-atlas 2.0.2 `countries-50m.json`；保留办公城市/客户总部对应区域。香港单独地区轮廓，其他非美区域采用国家轮廓。
- 美国 California (06)、Washington (53)、Arkansas (05)：US Census 2017 cartographic boundaries，由 us-atlas 3.0.1 `states-10m.json` 提供未投影经纬度。
- 提取：临时 topojson-client 3.1.0 `feature()`，只保留 geometry/id，坐标四舍五入至0.001度。不手写/替换边界。转换工具不进入项目依赖。

下载与许可：
- https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json
- https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json
- https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/states-10m.json
- https://github.com/topojson/world-atlas （分发代码 ISC，Natural Earth 数据 public domain）
- https://www.naturalearthdata.com/about/terms-of-use/
- https://github.com/topojson/us-atlas （分发代码 ISC，US Census 数据 public domain）

Office 名单：https://www.amlogic.com/about/ 。Customer 表示公开关联客户的总部代表城市，不表示产品部署位置：Google Mountain View、Amazon Seattle、Walmart Bentonville、Deutsche Telekom Bonn、Sky Isleworth。
关联来源：[2026半年报](https://static.cninfo.com.cn/finalpage/2026-08-20/1225482822.PDF)、[2025半年报](https://static.cninfo.com.cn/finalpage/2025-08-13/1224461921.PDF)，及 [Amlogic 官网新闻](https://www.amlogic.com/news/) 2026-09-14 IBC2026 Deutsche Telekom 发布（[原文转载](https://natlawreview.com/press-releases/ibc-2026-amlogic-x-deutsche-telekom-elevate-global-stb-experiences-edge-ai)）。坐标是城市中心的展示近似值，不是办公室建筑地址。区域等级：美国三州为 state，香港为 region，其他为 country；界线仅作相关区域示意。

绘制：d3-geo 3.1.1（ISC），https://github.com/d3/d3-geo/blob/main/LICENSE 。正射投影、真实自转和半球裁剪由库处理；页面不请求外部地图。

## 共享地球仪（2026-10-02）

`global-detail.json` 是离线展示数据，登录页与 Global Operator 共享同一绘制器。登录页按需加载绘制模块，PCB 首屏不下载该模块和地理数据。国境、行政界线及所有已收录行政中心点在正面常驻；文字优先显示业务地点、首都，再作行政中心空间避让。文字没有可用空间时不强行叠加，不影响点位存在。

- 行政线来源：[Natural Earth 10m Admin1 lines v5.1.0](https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_1_states_provinces_lines.zip)，public domain；[官方覆盖说明](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-1-states-provinces/)、[许可](https://www.naturalearthdata.com/about/terms-of-use/)。使用临时 `mapshaper@0.6.113` 对解压后的 `.shp` 执行 `-dissolve -simplify dp interval=12000 -o format=geojson precision=0.001`，合并线段、12km 制图概化，提取几何为 MultiLineString。保留 6075 条线、52912 坐标；简化过程报告一处无法修复的交叉。小于显示尺度的细节会被概化，不用于精确地图测量。
- 首都/一级行政中心来源：© GeoNames，[cities500.zip](https://download.geonames.org/export/dump/cities500.zip)，2026-10-02 下载，[字段说明](https://download.geonames.org/export/dump/readme.txt)、[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)、[GeoNames 条款](https://www.geonames.org/export/)。该下载除人口门槛外亦包含行政中心；从 TSV 筛选 `PPLC`（首都）和 `PPLA`（一级行政中心），使用 ASCII 名称、经纬度、国家代码、admin1 code，经纬度取三位小数，按首都优先/名称排序。共 3705 点，其中 PPLC 241、PPLA 3464，涉及 245 个国家/地区代码。点格式为 `[name, longitude, latitude, level, countryCode, admin1Code]`，level 0=首都、1=一级行政中心。这些是城市坐标，不是行政区质心。GeoNames 要求署名，本段和 `public/licenses/globe-data.md` 随项目保留。
- **覆盖限制**：NE 不覆盖部分微型国家、部分争议地区等，且一级区划口径与各国法律层级可能不完全一致；GeoNames 是社区聚合数据，不保证准确性、时效性或完整性。241 是数据记录数，不是主权国家数量；同一国家可能收录多种首都。两源不作行政区一对一配对，不声称全球每一区划都有中心点。中国大陆代码 CN 当前含 31 个中心（含首都），美国 US 51 个、德国 DE 16 个、法国 FR 13 个、印度 IN 36 个。未收录内容不伪造、不用区域中心代替省会。
- Office 使用 Coco 确认的 20 城市清单；Customer 使用确认的 18 个具名对象，不推导 Office 业务归属。北京/上海/深圳每 10 秒各随机选择一个非同城 Customer，一轮仅三条弧线；信号从 Office 沿大圆路线向客户运动。同城无法产生可见弧线，仍保留客户点，不挪动城市坐标。
- JBL 与 Harman Kardon 使用母公司 HARMAN 总部 Stamford 作为品牌代表点，两品牌保留独立名字并共用真实城市坐标；Skyworth 使用其中国地区总部 Shenzhen，而非香港法人注册地址。Reliance Jio 使用 Navi Mumbai 企业办公所在地，而非注册地 Ahmedabad。

新增客户城市的官方依据（检索于 2026-10-02）：

| 客户 | 展示城市 | 官方来源 |
|---|---|---|
| Xiaomi | Beijing | https://ir.mi.com/investor-resources/ir-contacts |
| TCL | Huizhou | https://www.tcl.com/content/dam/brandsite/region/china/pdf/report2024en-05-28.pdf |
| Skyworth | Shenzhen | https://investor.skyworth.com/en/contact.php |
| Haier | Qingdao | https://www.haier.com/global/investor-relationship/ |
| ZTE | Shenzhen | https://www.zte.com.cn/china/about/corporate_information.html |
| Alibaba | Hangzhou | https://www.alibabagroup.com/en-US/global-location |
| Baidu | Beijing | https://home.baidu.com/home/index/contact_us |
| China Mobile | Beijing | https://www.10086.cn/aboutus/xxgk/gkml/lxfs/ |
| China Telecom | Beijing | https://www.chinatelecom-h.com/en/global/home.php |
| China Unicom | Beijing | https://www.chinaunicom.com.hk/en/about/corpinfo.php |
| JBL、Harman Kardon | Stamford | https://www.harman.com/company |
| Reliance Jio | Navi Mumbai | https://www.jio.com/about/investor-relations/ |

数据生成仅用上述现成制图库转换，不新增运行时 GIS 依赖。更新素材时使用同样字段筛选并重新记录统计与版本，不将源站未来变动静默并入仓库。随构建分发的署名许可见 `public/licenses/globe-data.md`（构建后 `/licenses/globe-data.md`），本目录文档保存详细来源。

总部来源：[Google](https://about.google/company-info/locations/)、[Amazon](https://www.amazon.jobs/content/en/locations)、[Walmart](https://corporate.walmart.com/about/newhomeoffice/explore-the-new-campus/tour)、[Deutsche Telekom](https://www.telekom.com/de/allgemein/impressum)、[Sky](https://skygroup.sky/en-gb/about/our-governance)。

低分辨率海岸线为制图概化数据，例如 Istanbul 中心可落在50m海岸线外数公里；展示点保持真实城市近似坐标，不因概化轮廓修改位置。

七大洲/五大洋采用英文地理名称与大陆/海域内部近似展示锚点；锚点随正射投影旋转，仅正面且落入裁切视口时常驻显示，不参与地点轮显或闪烁。Pacific Ocean使用跨日期线两个锚点，当前可见范围同名最多显示一次。紧凑surface继续不显示名称。
