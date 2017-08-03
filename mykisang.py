#! /usr/bin/python
#-*- coding: utf-8 -*-

import time
import datetime
import sys
import requests
from PIL import ImageFont

from oled.serial import i2c, spi
from oled.device import sh1106, ssd1306
from oled.render import canvas

now = datetime.datetime.now()
if now.minute < 35:
    now = now - datetime.timedelta(hours = 1)
nowdate = str(now.year) + str('%02d' % now.month) + str('%02d' % now.day)
print(nowdate)
nowhour = str('%02d' % now.hour) + '00'
print(nowhour)

apinxy_json = '&nx=67&ny=101&_type=json' # 대전시 유성구

url = 'http://newsky2.kma.go.kr/service/SecndSrtpdFrcstInfoService2/' + 'ForecastGrib' \
+ '?ServiceKey=' + 'IzVFRF6gbi8IHnpNweJbBF547ybv9GAWm8e9TNSvhD%2BXQyNWZiJcBgiJe%2F2cmAEc2uKyYYq7SL3iO5obSx1Krg%3D%3D' \
+ '&base_date=' + nowdate + '&base_time=' + nowhour + '&nx=67&ny=101&_type=json'

print(url)
r = requests.get(url)
if (r.status_code != requests.codes.ok):
    sys.exit(-1)

weatherCur = int(r.json()['response']['header']['resultCode'])
if (weatherCur != 0):
    sys.stderr.write("Weather forecast Response: %s\n" % r.json()['response']['header']['resultMsg'])
    sys.exit(-1)
print(r.json())

tmp = {}

for data in r.json()['response']['body']['items']['item']:
    tmp[data['category']] = data['obsrValue']

#---------------------------------------------------------------------

gulim14 = ImageFont.truetype('/home/pi/fonts/NGULIM.TTF', 14)

device = sh1106(i2c(port=1, address=0x3c))

with canvas(device) as draw:
    draw.text((0, 0), u"온도 : " + str(tmp['T1H']), font=gulim14, fill='white')
    draw.text((0, 16), u"습도 : " + str(tmp['REH']), font=gulim14, fill='white')

from time import sleep
while True:
    sleep(3600)
