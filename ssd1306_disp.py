#! /usr/bin/python
#-*- coding: utf-8 -*- 

"""
Copyright (c) 2016 Nam, Kookjin - All Rights Reserved.
Do not distribute modified versions of this file.
Non-commercial use only. You may not use this work for commercial purposes.
Any question: http://blog.naver.com/kjnam100/220805352857
"""

#import Adafruit_GPIO.SPI as SPI
#import Adafruit_SSD1306
from oled.serial import i2c, spi
from oled.device import sh1106, ssd1306
from oled.render import canvas

import datetime
import os, sys, subprocess
import fcntl
import re

from PIL import Image
import urlparse
import pyudev # usb monitor
import thread

from threading import Event
import time

import fonts
import mpdc
import kisang # 날씨
import tda7439
import alarm
import inet_radio_sub

import remote_sim_cmd

import sysv_ipc # for shared memory

reload(sys)
sys.setdefaultencoding('utf-8')

os.environ["LC_COLLATE"] = "ko_KR.UTF-8"

disp_mode_file = '/var/local/ramdisk/disp_mode'
disp_mode_fifo = "/var/local/ramdisk/disp_mode_fifo"
playlist_name_file = '/var/local/playlist_name'
inet_radio_stat_file = "/var/local/inet_radio_stat"
inet_rs_file = "/var/local/inet_radio_station"
inet_radio_mesg_file = "/var/local/ramdisk/inet_radio_mesg"
#inet_radio_fifo_file = "/var/local/ramdisk/mplayer_fifo"
fm_station_file = "/var/local/radio/radio_station"
fm_tuned_freq_file = "/var/local/radio/tuned_freq"
fm_tuned_status_file = "/var/local/ramdisk/tuned_status"

mplayer_check_cmd = "pgrep -ox mplayer"

inet_radio_cmd = "inet_radio.py "
fm_radio_cmd = "fm_radio "

extra_playlist_name = "Ａll ♪  in USB"

disp_sleep = None
anniversary_len = 0

#=====================================================================
# abortable sleep()
class Sleep(object):
    def __init__(self):
        self.do_not_sleep = False
        self.sleeping = False
        self.event = Event()

    def sleep(self, seconds):
        if (self.do_not_sleep):
            self.do_not_sleep = False
            return
        self.event.clear()
        self.sleeping = True
        self.event.wait(timeout=seconds)
        self.sleeping = False

    def wake(self):
        if self.sleeping:
            self.event.set()
        else:
            self.do_not_sleep = True

#---------------------------------------------------------------------

def my_unicode(mesg, errors='ignore'):
    try:
        return unicode(mesg, errors=errors)
    except:
        return mesg

#
#--------------------------------------------------------------------------------------------
#
inet_rs_lists = []
inet_grs_lists = []
total_inet_grs_num = 0
inet_grs_idx = 0
inet_rs_idx = 0
last_grs_pos = 0
last_rs_pos = []

inet_radio_playing = False
inet_radio_pause = False

# inet radio station 번호 가져오기
# 처음 한번만 읽음.
# /var/local/inet_radio_station 파일이 바뀌면 프로그램 재시작 할 것. key_display -> key_power

def read_inet_rs_info():
    global total_inet_grs_num
    global inet_grs_idx, inet_rs_idx

    try:
        with open(inet_radio_stat_file, 'r') as fd:
            gn_sn = fd.readline().split(',',1)
            inet_grs_idx = int(gn_sn[0])
            inet_rs_idx = int(gn_sn[1])
    except:
        inet_grs_idx = 0
        inet_rs_idx = 0

    total_inet_grs_num = 0
    try:
        with open(inet_rs_file, 'r') as fd:
            info = fd.read().splitlines()
            for item in info:
                if len(item) <= 0 or item[0] == '#': continue # comment of black line

                if item[0] == '[':
                    pos = item.rfind(']')
                    if pos < 0: continue # File 작성 오류
                    inet_grs_lists.append(item[1:pos])
                    last_rs_pos.append(0)
                    rs = {}
                    rs['addr'] = []
                    rs['name'] = []
                    rs['kbps'] = []
                    rs['flag'] = []
                    rs['list'] = []
                    inet_rs_lists.append(rs)
                    total_inet_grs_num += 1
                    rs_num = 0
                    continue

                url_info = urlparse.urlparse(item)
                if url_info.scheme:
                    if total_inet_grs_num == 0:
                        inet_grs_lists.append("인터넷 방송국")
                        last_rs_pos.append(0)
                        rs = {}
                        rs['addr'] = []
                        rs['name'] = []
                        rs['kbps'] = []
                        rs['flag'] = []
                        rs['list'] = []
                        inet_rs_lists.append(rs)
                        total_inet_grs_num += 1
                        rs_num = 0
                    station_info = item.split(None, 1)
                    if len(station_info) > 1:
                        station_info[1] = station_info[1].rstrip()
                        try: # {kbps} 제거
                            if station_info[1][-1] == '}':
                                idx = station_info[1].rindex('{')
                                kbps = int(station_info[1][idx+1:-1])
                                station_info[1] = station_info[1][:idx]
                            else: kbps = 0
                        except: kbps = 0
                        rs['list'].append(station_info[1])
                        rs['name'].append(station_info[1])
                        rs['kbps'].append(kbps)
                        rs['flag'].append(True)
                    else:
                        rs['list'].append(station_info[0])
                        rs['name'].append("Radio Stream " + str(rs_num+1))
                        rs['kbps'].append(0)
                        rs['flag'].append(False)
                    rs['addr'].append(station_info[0])
                    rs_num += 1
    except: pass

    inet_grs_idx %= total_inet_grs_num
    inet_rs_idx %= len(inet_rs_lists[inet_grs_idx]['addr'])

#
#--------------------------------------------------------------------------------------------
#
fm_station_freq_lists = []
fm_station_name_lists = []
total_fm_station_num = 0
def read_fm_station_info():
    global total_fm_station_num, fm_station_num
    global fm_station_freq_lists, fm_station_name_lists

    # inet radio station 번호의 station 이름 가져오기
    try:
        with open(fm_station_file, 'r') as fd:
            info = fd.read().splitlines()
            i = 0
            for item in info:
                if (item[0] == '#'): continue
                station_info = item.split(None, 1)
                # 같은 주파수가 이미 존재하는 경우 건너 뜀
                if station_info[0] in fm_station_freq_lists:
                    continue
                if (len(station_info) > 1):
                    fm_station_name_lists.append(station_info[1])
                else:
                    fm_station_name_lists.append(station_info[0] + " MHz")
                fm_station_freq_lists.append(station_info[0])
                i += 1
            total_fm_station_num = i
    except:
        total_fm_station_num = -1

#--------------------------------------------------------------------------------------------

'''
import logging
import logging.handlers

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)
handler = logging.handlers.SysLogHandler(address = '/dev/log')
formatter = logging.Formatter('%(module)s.%(funcName)s: %(message)s')
handler.setFormatter(formatter)
log.addHandler(handler)
    log.debug(mode)
'''

def save_disp_mode(mode):
    try:
        with open(disp_mode_file, 'r') as fd:
            if mode == fd.readline():
                return
    except IOError: pass

    try:
        with open(disp_mode_file, 'w') as fd:
            fd.write(mode)
    except IOError: return

#--------------------------------------------------------------------------------------------

def read_disp_mode():
    try:
        with open(disp_mode_file, 'r') as fd:
            return fd.readline()
    except IOError:
        return "clock"

#--------------------------------------------------------------------------------------------

disp_mode = 'clock'
disp_mode_once = ''
weather_mode_change = False
weather_mode = 0
wturn = 0
disp_mode_recv_cnt = 0
device_hide = False

def get_disp_mode(disp_mode_tmp):
    global disp_mode, disp_mode_once
    global disp_mode_recv_cnt
    global device_hide

    disp_mode_once = ''

    if (disp_mode_tmp == 'display_on'):
        device.show()
        device_hide = False
        return
    elif (disp_mode_tmp == 'display_off'):
        device.hide()
        device_hide = True
        return

    disp_mode_recv_cnt += 1

    device.show()
    device.contrast(128)
    device_hide = False

    if disp_mode_tmp == "nop":
        return

    try:
        if disp_mode_tmp.startswith('mpd'):
            check_mpd_mode(disp_mode_tmp)
            disp_mode = 'mpd'

        elif disp_mode_tmp.startswith('inet_radio'):
            check_inet_radio_mode(disp_mode_tmp)

        elif (disp_mode_tmp == 'fm'):
            if disp_mode != 'fm': tda7439.get_tda7439('fm')
            disp_mode = disp_mode_tmp

        elif (disp_mode_tmp == 'bluetooth'):
            if disp_mode != 'bluetooth': tda7439.get_tda7439('bluetooth')
            disp_mode = disp_mode_tmp

        elif (disp_mode_tmp == 'aux'):
            if disp_mode != 'aux': tda7439.get_tda7439('aux')
            disp_mode = disp_mode_tmp

        elif disp_mode_tmp.startswith('tda7439'):
            disp_mode = tda7439.check_tda7439_mode(disp_mode, disp_mode_tmp)

        elif disp_mode_tmp.startswith('alarm'):
            alarm.check_alarm_mode(disp_mode_tmp)
            disp_mode = 'alarm'

        elif disp_mode_tmp.startswith('clock'):
            check_clock_mode(disp_mode_tmp)
            disp_mode = 'clock'

        elif disp_mode_tmp.startswith('weather'):
            check_weather_mode(disp_mode_tmp)
            disp_mode = "weather"

        elif disp_mode_tmp.startswith('air'):
            check_air_mode(disp_mode_tmp)
            disp_mode = "air"

        elif disp_mode_tmp.startswith('sleep'):
            check_sleep_mode(disp_mode_tmp)
            disp_mode = "sleep"

        elif (disp_mode_tmp == 'network'):
            disp_mode_once = disp_mode_tmp

        elif disp_mode_tmp.startswith('memory_mpd'):
            check_memory_mpd(disp_mode_tmp)

        elif disp_mode_tmp.startswith('memory_inet_radio'):
            check_memory_inet_radio(disp_mode_tmp)

        elif disp_mode_tmp.startswith('memory_fm'):
            check_memory_fm(disp_mode_tmp)

        else:
            disp_mode = disp_mode_tmp
    except: pass

    disp_sleep.wake()
#
#--------------------------------------------------------------------------------------------
#
mpd_info_mode = False
def check_mpd_mode(disp_mode_tmp):
    global disp_mode, mpd_info_mode
    global poller
    global mpd_info_mode
    global px1, px2, px3
    global wpx1, wpx2, wpx3
    global mpd_spectrum_flag

    if (disp_mode_tmp == 'mpd'):
        if disp_mode != 'mpd': tda7439.get_tda7439('mpd')
        px1 = px2 = px3 = wpx1 = wpx2 = wpx3 = 0
    elif (disp_mode_tmp == 'mpd_display'):
        if mpd_info_mode == 0 and mpd_spectrum_flag == 0:
            mpd_info_mode = not mpd_info_mode
            px3 = wpx3 = 0
        elif mpd_info_mode == 1 and mpd_spectrum_flag == 0:
            mpd_info_mode = not mpd_info_mode
            mpd_spectrum_flag = 1
        else:
            mpd_info_mode = 0
            mpd_spectrum_flag = 0
        poller.set_showStreamInfo(mpd_info_mode)
    elif (disp_mode_tmp == 'mpd_random'):
        if mpd_info_mode:
            subprocess.call("mpc -q single", shell=True)
        else:
            subprocess.call("mpc -q random", shell=True)
    elif (disp_mode_tmp == 'mpd_repeat'):
        if mpd_info_mode:
            subprocess.call("mpc -q consume", shell=True)
        else:
            subprocess.call("mpc -q repeat", shell=True)

#--------------------------------------------------------------------------------------------
def check_inet_radio_mode(disp_mode_tmp):
    global disp_mode
    global inet_radio_pause
    global px1, px2, px3, wpx1, wpx2, wpx3
    global inet_grs_idx, inet_rs_idx
    global inet_radio_mode
    global prev_radio_album_info

    tda7439.get_tda7439('inet_radio')

    if disp_mode_tmp == 'inet_radio':
        px1 = px2 = px3 = wpx1 = wpx2 = wpx3 = 0
        disp_mode = 'inet_radio'
    elif disp_mode_tmp == 'inet_radio_pause':
        inet_radio_pause = not inet_radio_pause
    elif disp_mode_tmp.startswith('inet_radio_play'): # inet_radio_playgn,sn
        inet_radio_pause = False
        try:
            gn_sn = disp_mode_tmp[15:].split(',',1)
            inet_grs_idx = int(gn_sn[0])
            inet_rs_idx = int(gn_sn[1])
        except: pass
    elif (disp_mode_tmp == 'inet_radio_display'):
        inet_radio_mode = (inet_radio_mode + 1) % 3

        # album 정보없는 radio station의 경우 1번 모드(stream display) 건너뜀
        if not prev_radio_album_info and inet_radio_mode == 1:
            inet_radio_mode = 2
        if prev_radio_album_info and inet_radio_mode == 2:
             px3 = wpx3 = 0

#--------------------------------------------------------------------------------------------
def check_sleep_mode(disp_mode_tmp):
    global disp_mode
    global sleep_wait, sleep_mode

    if disp_mode_tmp == 'sleep':
        if disp_mode == 'sleep':
            sleep_wait += 1
    elif disp_mode_tmp == 'sleep_right':
        sleep_wait += 1
    elif disp_mode_tmp == 'sleep_left':
        sleep_wait -= 1
    elif disp_mode_tmp == 'sleep_enter':
        if sleep_wait == 0:
            subprocess.call("ssd1306_intelli_mode.py key_enter", shell=True)
        else:
            sleep_mode = (sleep_mode + 1) % 2 # sleep_mode toggle(power/sleep)  

#--------------------------------------------------------------------------------------------

def check_weather_mode(disp_mode_tmp):
    global disp_mode
    global weather_mode, wturn, weather_mode_change

    if disp_mode_tmp == 'weather':
        wturn = 0
        weather_mode = 0 # 'multi'
        weather_mode_change = False
    elif disp_mode == 'weather':
        if disp_mode_tmp == 'weather_presetup' or disp_mode_tmp == 'weather_right':
            wturn += 1
        elif disp_mode_tmp == 'weather_presetdown' or disp_mode_tmp == 'weather_left':
            wturn -= 1
        elif disp_mode_tmp == 'weather_tuningup':
            wturn = -1
        elif disp_mode_tmp == 'weather_tuningdown':
            wturn = 0
        elif disp_mode_tmp == 'weather_up':
            weather_mode_change = True

#--------------------------------------------------------------------------------------------
air_page = 0
def check_air_mode(disp_mode_tmp):
    global disp_mode
    global air_page

    if disp_mode == 'air':
        air_page = (air_page + 1) % 2

#--------------------------------------------------------------------------------------------

def check_clock_mode(disp_mode_tmp):
    global disp_mode
    global calendar_disp_mode
    global anniversary_disp_pos
    global anniversary_len

    if disp_mode != 'clock' or anniversary_disp_pos == -1:
        anniversary_disp_pos = kisang.get_anniversary_today_pos()

    if disp_mode_tmp == 'clock':
        calendar_disp_mode = 0
    elif disp_mode == 'clock':
        calendar_disp_mode = calendar_disp_mode % CALENDAR_DISP_MODE_NUM
        if disp_mode_tmp == 'clock_presetup':
            calendar_disp_mode += 1
        elif disp_mode_tmp == 'clock_presetdown':
            calendar_disp_mode -= 1
        elif calendar_disp_mode == 4: # 기념일
            if disp_mode_tmp == 'clock_down' or disp_mode_tmp == 'clock_channeldown':
                anniversary_disp_pos += 4
            elif disp_mode_tmp == 'clock_up' or disp_mode_tmp == 'clock_channelup':
                anniversary_disp_pos -= 4
            elif disp_mode_tmp == 'clock_tuningup':
                anniversary_disp_pos = anniversary_len - 1
            elif disp_mode_tmp == 'clock_tuningdown':
                anniversary_disp_pos = 0

            if anniversary_disp_pos >= anniversary_len:
                anniversary_disp_pos = anniversary_len - 1
            elif anniversary_disp_pos < 0:
                anniversary_disp_pos = 0
        elif disp_mode_tmp == 'clock_tuningup':
            calendar_disp_mode += 1
        elif disp_mode_tmp == 'clock_tuningdown':
            calendar_disp_mode -= 1
        elif disp_mode_tmp == 'clock_down':
            subprocess.call("ssd1306_intelli_mode.py key_down", shell=True)

#--------------------------------------------------------------------------------------------

def mpc_play_or_mode(mode):
    global list_sel_p
    global mpd_status
    global memory_mpd_songs_list
    global memory_mpd_songs_from_playlist

    memory_mpd_songs_from_playlist = True
    pos = list_sel_p % len(memory_mpd_songs_list)

    if cur_playlist_name == memory_mpd_songs_list_name and \
        mpd_status['state'] != 'stop' and pos == mpd_status['song_pos']:
        subprocess.call("mpc -q " + mode, shell=True)
        return

    elif cur_playlist_name != memory_mpd_songs_list_name:
        load_mpd_playlist(memory_mpd_songs_list_name)

    subprocess.call("mpc -q play " + str(pos+1), shell=True)
#
#--------------------------------------------------------------------------------------------
#
pds_lock = thread.allocate_lock()
def playlist_del_songs(pos, num):
    global pds_lock

    cmd = "mpc -q del " + str(pos)
    pds_lock.acquire()
    for _ in range(0,num):
        subprocess.call(cmd, shell=True)
    pds_lock.release()
#
#--------------------------------------------------------------------------------------------
#
def load_mpd_playlist(playlist_name):
    global cur_playlist_name
    global pds_lock

    pds_lock.acquire()
    subprocess.call("mpc -q clear", shell=True)
    if playlist_name == extra_playlist_name:
        subprocess.call("mpc listall | grep '^USB/' | mpc add", shell=True)
    else:
        subprocess.call("mpc -q load " + "'" + playlist_name + "' > /dev/null 2>&1", shell=True)
    pds_lock.release()

    try:
        subprocess.call("ssd1306_playlist.py " + "'" + playlist_name  + "'", shell=True)
    except: pass

    cur_playlist_name = playlist_name
#
#--------------------------------------------------------------------------------------------

memory_mpd_songs_list = []
memory_mpd_songs_list_name = ""
memory_mpd_songs_from_playlist = True
prev_usb_songs = "Unknown"
saved_usb_songs = []

def get_playlist_songs(play_flag=True, playlist_name=None, repos=True):
    global list_sel_p
    global cur_playlist_name
    global mpd_playlists
    global prev_usb_songs
    global memory_mpd_songs_list
    global memory_mpd_songs_list_name
    global memory_mpd_songs_from_playlist
    global mpd_status
    global mpd_playlists_songs_pos
    global px1, wpx1
    global pds_lock
    global saved_usb_songs

    if playlist_name is None:
        playlist_name = mpd_playlists[list_sel_p % len(mpd_playlists)]

    if playlist_name == extra_playlist_name:
        try:
            usb_songs = subprocess.check_output("mpc listall | grep '^USB/'", shell=True)
        except: usb_songs = ""
        if prev_usb_songs != usb_songs:
            prev_usb_songs = usb_songs
            if play_flag or mpd_status['state'] == 'stop':
                pds_lock.acquire()
                subprocess.call("mpc -q clear", shell=True)
                subprocess.call("mpc listall | grep '^USB/' | mpc add", shell=True)
                memory_mpd_songs_list = subprocess.check_output("mpc playlist", shell=True).splitlines()
                pds_lock.release()
                cur_playlist_name = playlist_name
            else:
                pds_lock.acquire()
                cur_songs = subprocess.check_output("mpc playlist", shell=True).splitlines()
                subprocess.call("mpc listall | grep '^USB/' | mpc add", shell=True)
                tot_songs = subprocess.check_output("mpc playlist", shell=True).splitlines()
                pds_lock.release()
                memory_mpd_songs_list = tot_songs[len(cur_songs):]
                thread.start_new_thread(playlist_del_songs, (len(cur_songs)+1,len(memory_mpd_songs_list),))
            saved_usb_songs = memory_mpd_songs_list
        else:
            memory_mpd_songs_list = saved_usb_songs
            if playlist_name == cur_playlist_name: memory_mpd_songs_from_playlist = True
            else: memory_mpd_songs_from_playlist = False

    elif playlist_name != cur_playlist_name:
        if play_flag or mpd_status['state'] == 'stop':
            load_mpd_playlist(playlist_name)
            pds_lock.acquire()
            memory_mpd_songs_list = subprocess.check_output("mpc playlist", shell=True).splitlines()
            pds_lock.release()
            memory_mpd_songs_from_playlist = True
            cur_playlist_name = playlist_name
        else:
            pds_lock.acquire()
            cur_songs = subprocess.check_output("mpc playlist", shell=True).splitlines()
            subprocess.call("mpc -q load " + "'" + playlist_name + "' > /dev/null 2>&1", shell=True)
            tot_songs = subprocess.check_output("mpc playlist", shell=True).splitlines()
            memory_mpd_songs_list = tot_songs[len(cur_songs):]
            pds_lock.release()
            thread.start_new_thread(playlist_del_songs, (len(cur_songs)+1,len(memory_mpd_songs_list),))
            memory_mpd_songs_from_playlist = False
    else:
        pds_lock.acquire()
        memory_mpd_songs_list = subprocess.check_output("mpc playlist", shell=True).splitlines()
        pds_lock.release()
        memory_mpd_songs_from_playlist = True

    memory_mpd_songs_list_name = playlist_name

    if play_flag:
        subprocess.call("mpc -q play", shell=True)

    if repos:
        if mpd_status['state'] != 'stop' and cur_playlist_name == playlist_name:
            list_sel_p = mpd_status['song_pos']
        else:
            if playlist_name in mpd_playlists_songs_pos:
                list_sel_p = mpd_playlists_songs_pos[playlist_name]
            else:
                mpd_playlists_songs_pos[playlist_name] = 0
                list_sel_p = 0
        px1 = wpx1 = 0
#
#--------------------------------------------------------------------------------------------
#
def mpc_load_and_play(play_flag=True):
    global list_sel_p
    global cur_playlist_name
    global mpd_playlists
    global prev_usb_songs
    global mpd_status
    global pds_lock

    playlist_name = mpd_playlists[list_sel_p % len(mpd_playlists)]

    if playlist_name != cur_playlist_name:
        pds_lock.acquire()
        subprocess.call("mpc -q clear", shell=True)
        if playlist_name == extra_playlist_name:
            subprocess.call("mpc listall | grep '^USB/' | mpc add", shell=True)
        else:
            subprocess.call("mpc -q load " + "'" + playlist_name + "' > /dev/null 2>&1", shell=True)
        pds_lock.release()
        try:
            subprocess.call("ssd1306_playlist.py " + "'" + playlist_name  + "'", shell=True)
        except: pass
        cur_playlist_name = playlist_name

    elif playlist_name == extra_playlist_name:
        try:
            usb_songs = subprocess.check_output("mpc listall | grep '^USB/'", shell=True)
        except: usb_songs = ""
        if prev_usb_songs != usb_songs:
            global saved_usb_songs
            prev_usb_songs = usb_songs
            pds_lock.acquire()
            subprocess.call("mpc -q clear", shell=True)
            subprocess.call("mpc listall | grep '^USB/' | mpc add", shell=True)
            saved_usb_songs = subprocess.check_output("mpc playlist", shell=True).splitlines()
            pds_lock.release()

    if play_flag or mpd_status['state'] == 'play':
        subprocess.call("mpc -q play", shell=True)
#
#--------------------------------------------------------------------------------------------
#
last_memory_mpd = 0 # 0:memory_mpd, 1:memory_mod_songs
last_memory_inet_radio = 0  # 0:memory_inet_radio, 1:memory_inet_radio_songs
last_memory_mpd_pos = 0

def check_memory_mpd(disp_mode_tmp):
    global disp_mode
    global list_sel_p
    global mpd_playlists, cur_playlist_name
    global cur_playlist_index
    global prev_song
    global px1, wpx1
    global inet_radio_pause
    global last_memory_mpd
    global mpd_status

    if disp_mode_tmp == 'memory_mpd':
        if last_memory_mpd == 0:
            get_mpd_playlists()
            if len(mpd_playlists) <= 0:
                get_playlist_songs(False, cur_playlist_name)
                last_memory_mpd = 1
        else:
            get_playlist_songs(False, cur_playlist_name)

    elif disp_mode == 'memory_mpd':
        if disp_mode_tmp == 'memory_mpd_up': list_sel_p -= 1
        elif disp_mode_tmp == 'memory_mpd_down': list_sel_p += 1
        elif disp_mode_tmp == 'memory_mpd_channelup': list_sel_p -= 3
        elif disp_mode_tmp == 'memory_mpd_channeldown': list_sel_p += 3
        elif disp_mode_tmp == 'memory_mpd_tuningup': list_sel_p = -1
        elif disp_mode_tmp == 'memory_mpd_tuningdown': list_sel_p = 0
        elif disp_mode_tmp == 'memory_mpd_display':
            if mpd_status['state'] != 'stop':
                if last_memory_mpd == 1:
                    if memory_mpd_songs_list_name != cur_playlist_name:
                        get_playlist_songs(False, cur_playlist_name, False)
                    list_sel_p = mpd_status['song_pos']    # 현재 플레이 중인 곡으로 cursor 옮김
                else:
                    list_sel_p %= len(mpd_playlists)
                    if list_sel_p != cur_playlist_index:
                        list_sel_p = cur_playlist_index    # 현재 플레이 중인 playlist로 cursor 옮김
        elif disp_mode_tmp == 'memory_mpd_presetup':
            if last_memory_mpd == 1:
                mpc_play_or_mode("next")
            else:
                list_sel_p %= len(mpd_playlists)
                if mpd_status['state'] != 'stop' and cur_playlist_index == list_sel_p:
                    list_sel_p += 1
                cur_playlist_index = list_sel_p % len(mpd_playlists)
                mpc_load_and_play(True)
        elif disp_mode_tmp == 'memory_mpd_presetdown':
            if last_memory_mpd == 1:
                mpc_play_or_mode("prev")
            else:
                list_sel_p %= len(mpd_playlists)
                if mpd_status['state'] != 'stop' and cur_playlist_index == list_sel_p:
                    list_sel_p -= 1
                cur_playlist_index = list_sel_p % len(mpd_playlists)
                mpc_load_and_play(True)
        elif disp_mode_tmp == 'memory_mpd_right':
            if last_memory_mpd == 1:
                mpc_play_or_mode("toggle")
            else:
                get_playlist_songs(False)
                last_memory_mpd = 1
        elif disp_mode_tmp == 'memory_mpd_left':
            if last_memory_mpd == 1:
                global last_memory_mpd_pos
                get_mpd_playlists()
                last_memory_mpd = 0
                list_sel_p = last_memory_mpd_pos
            else:
                subprocess.call(remote_sim_cmd.memory, shell=True)
        elif (disp_mode_tmp == 'memory_mpd_enter'):
            if last_memory_mpd == 1:
                list_sel_p %= len(memory_mpd_songs_list)
                if cur_playlist_name != memory_mpd_songs_list_name or \
                   mpd_status['state'] == 'stop' or (list_sel_p != mpd_status['song_pos']):
                    if cur_playlist_name != memory_mpd_songs_list_name:
                        load_mpd_playlist(memory_mpd_songs_list_name)
                    num = list_sel_p % len(memory_mpd_songs_list) + 1
                    subprocess.call("mpc -q play " + str(num), shell=True)
            else:
                mpc_load_and_play(True)
            disp_mode = "mpd"
            return
        px1 = wpx1 = 0
    else:   # 무시할 수도 있음 
        get_mpd_playlists()

    if last_memory_mpd == 1:
        if len(memory_mpd_songs_list) > 0:
            mpd_playlists_songs_pos[memory_mpd_songs_list_name] = list_sel_p % len(memory_mpd_songs_list)
        else:
            mpd_playlists_songs_pos[memory_mpd_songs_list_name] = 0
        list_sel_p = mpd_playlists_songs_pos[memory_mpd_songs_list_name]

    disp_mode = "memory_mpd"

#
#--------------------------------------------------------------------------------------------
#
def set_and_play_memory_inet_radio(gn, sn):
    global inet_grs_idx, inet_rs_idx
    global inet_radio_pause

    rs_num = sn % len(inet_rs_lists[gn]['list'])
    cmd = inet_radio_cmd + str(gn) + ',' + str(rs_num) + ' nop'
    subprocess.call(cmd, shell=True)
    inet_grs_idx = gn
    inet_rs_idx = rs_num
    inet_radio_pause = False

#--------------------------------------------------------------------------------------------
def check_memory_inet_radio(disp_mode_tmp):
    global disp_mode
    global total_inet_grs_num
    global last_memory_inet_radio
    global inet_grs_idx, inet_rs_idx
    global list_sel_p
    global inet_radio_playing, inet_radio_pause
    global px1, wpx1
    global last_grs_pos, last_rs_pos

    if total_inet_grs_num <= 0:
        list_sel_p = 0
    elif disp_mode_tmp == 'memory_inet_radio':
        if last_memory_inet_radio == 0:
            list_sel_p = inet_grs_idx
        else:
            list_sel_p = inet_rs_idx
    elif disp_mode.startswith('memory_inet_radio'):
        if disp_mode_tmp == 'memory_inet_radio_up': list_sel_p -= 1
        elif disp_mode_tmp == 'memory_inet_radio_down': list_sel_p += 1
        elif disp_mode_tmp == 'memory_inet_radio_channelup': list_sel_p -= 3
        elif disp_mode_tmp == 'memory_inet_radio_channeldown': list_sel_p += 3
        elif disp_mode_tmp == 'memory_inet_radio_tuningup': list_sel_p = -1
        elif disp_mode_tmp == 'memory_inet_radio_tuningdown': list_sel_p = 0
        elif disp_mode_tmp == 'memory_inet_radio_display':    # 현재 플레이 station으로 cursor 옮김
            if inet_radio_playing:
                if last_memory_inet_radio == 1:
                    last_grs_pos = inet_grs_idx
                    list_sel_p = inet_rs_idx
                else:
                    list_sel_p = inet_grs_idx
        elif disp_mode_tmp == 'memory_inet_radio_presetup':
            if last_memory_inet_radio == 1:
                if inet_radio_playing and inet_grs_idx == last_grs_pos and inet_rs_idx == last_rs_pos[last_grs_pos]:
                    list_sel_p += 1
                set_and_play_memory_inet_radio(last_grs_pos, list_sel_p)
            else:
                list_sel_p %= len(inet_grs_lists)
                if inet_radio_playing and inet_grs_idx == list_sel_p:
                    list_sel_p += 1
                    list_sel_p %= len(inet_grs_lists)
                set_and_play_memory_inet_radio(list_sel_p, 0)
        elif disp_mode_tmp == 'memory_inet_radio_presetdown':
            if last_memory_inet_radio == 1:
                if inet_radio_playing and inet_grs_idx == last_grs_pos and inet_rs_idx == last_rs_pos[last_grs_pos]:
                    list_sel_p -= 1
                set_and_play_memory_inet_radio(last_grs_pos, list_sel_p)
            else:
                list_sel_p %= len(inet_grs_lists)
                if inet_radio_playing and inet_grs_idx == list_sel_p:
                    list_sel_p -= 1
                    list_sel_p %= len(inet_grs_lists)
                set_and_play_memory_inet_radio(list_sel_p, 0)
        elif disp_mode_tmp == 'memory_inet_radio_right':
            if last_memory_inet_radio == 1:
                if inet_radio_playing and inet_grs_idx == last_grs_pos and inet_rs_idx == last_rs_pos[last_grs_pos]:
                    subprocess.call(inet_radio_cmd + "pause nop", shell=True)   # play/pause toggle
                    inet_radio_pause = not inet_radio_pause
                    return
                else:
                    set_and_play_memory_inet_radio(last_grs_pos, list_sel_p)
            else:
                last_memory_inet_radio = 1
                grs = list_sel_p % total_inet_grs_num
                if grs == inet_grs_idx and inet_radio_playing:
                    list_sel_p = inet_rs_idx
                else:
                    list_sel_p = last_rs_pos[grs]
        elif disp_mode_tmp == 'memory_inet_radio_left':
            if last_memory_inet_radio == 1:
                last_memory_inet_radio = 0
                list_sel_p = last_grs_pos
            else:
                subprocess.call(remote_sim_cmd.memory, shell=True)
        elif (disp_mode_tmp == 'memory_inet_radio_enter'):
            if not inet_radio_playing or inet_grs_idx != last_grs_pos or \
               (last_memory_inet_radio == 1 and inet_rs_idx != last_rs_pos[last_grs_pos]):
                if last_memory_inet_radio == 1:
                    rs_num = list_sel_p
                else:
                    rs_num = 0
                set_and_play_memory_inet_radio(last_grs_pos, rs_num)
            disp_mode = "inet_radio"
            return

    px1 = wpx1 = 0
    disp_mode = 'memory_inet_radio'
#
#----------------------------------------------------------------------------------------
#
def check_memory_fm(disp_mode_tmp):
    global disp_mode
    global list_sel_p
    global px1, wpx1

    if disp_mode_tmp == 'memory_fm':
        get_tuned_fm_station_num()
        disp_mode = 'memory_fm'
    elif disp_mode.startswith('memory_fm'):
        global fm_station_num, total_fm_station_num

        if disp_mode_tmp == 'memory_fm_up': list_sel_p -= 1
        elif disp_mode_tmp == 'memory_fm_down': list_sel_p += 1
        elif disp_mode_tmp == 'memory_fm_channelup': list_sel_p -= 3
        elif disp_mode_tmp == 'memory_fm_channeldown': list_sel_p += 3
        elif disp_mode_tmp == 'memory_fm_tuningup': list_sel_p = -1
        elif disp_mode_tmp == 'memory_fm_tuningdown': list_sel_p = 0
        elif disp_mode_tmp == 'memory_fm_display':    # 현재 플레이 station으로 cursor 옮김
            if list_sel_p != fm_station_num:
                list_sel_p = fm_station_num
            else: return
        elif disp_mode_tmp == 'memory_fm_presetup':
            list_sel_p += 1
            fm_station_num = list_sel_p % total_fm_station_num
            subprocess.call(fm_radio_cmd + str(fm_station_num+1) + " > /dev/null 2>&1", shell=True)
        elif disp_mode_tmp == 'memory_fm_presetdown':
            list_sel_p -= 1
            fm_station_num = list_sel_p % total_fm_station_num
            subprocess.call(fm_radio_cmd + str(fm_station_num+1) + " > /dev/null 2>&1", shell=True)
        elif disp_mode_tmp == 'memory_fm_right':
            if fm_station_num == list_sel_p:
                subprocess.call(fm_radio_cmd + "toggle", shell=True)   # play/pause toggle
            else:
                fm_station_num = list_sel_p % total_fm_station_num
                subprocess.call(fm_radio_cmd + str(list_sel_p+1) + " > /dev/null 2>&1", shell=True)
            return
        elif disp_mode_tmp == 'memory_fm_left':
            subprocess.call(remote_sim_cmd.memory, shell=True)
        elif disp_mode_tmp == 'memory_fm_enter':
            fm_station_num = list_sel_p % total_fm_station_num
            subprocess.call(fm_radio_cmd + str(fm_station_num+1) + " > /dev/null 2>&1", shell=True)
            disp_mode = "fm"
            return
        px1 = wpx1 = 0
    else:
        get_tuned_fm_station_num()
        disp_mode = "memory_fm"

#
# signal hander
#
#----------------------------------------------------------------------------------------
#def sig_handler(signum, frame):
#    global lock
#
#    lock.acquire()
#    get_disp_mode()
#    save_disp_mode(disp_mode)
#    lock.release()
#
#    disp_sleep.wake()

#=======================================================================================

# 대기 상태 표시
def air_disp(page=0):
    kisang.getAirStat()
    with canvas(device) as draw:
        kisang.air_disp(draw, page)
    disp_sleep.sleep(600)

#--------------------------------------------------------------------------------------

wfore_num = [[4, 6, 6, 5, 5, 5, 5, 4],[15, 22, 21, 20, 19, 18, 17, 16]]

def weather_disp():
    global weather_mode, wturn, weather_mode_change

    # ntp time check
    if not kisang.NtpStat:
        if not kisang.does_ntp_work():
            with canvas(device) as draw:
                draw.text((0,0), u"날씨정보 오류", font=fonts.gulim14, fill='white')
                draw.text((0,20), u"시간 설정 중입니다.", font=fonts.gulim14, fill='white')
                draw.text((0,40), u"잠시 기다려 주세요.", font=fonts.gulim14, fill='white')
            disp_sleep.sleep(10)
            return

    now = datetime.datetime.now()
    kisang.getWeatherFore(now)
    if kisang.weatherForeTime:
        turn_cycle = kisang.weatherForeTime.hour / 3
    else:
        turn_cycle = 0

    if weather_mode_change:
        weather_mode_change = False
        mode = wturn % (wfore_num[weather_mode][turn_cycle] + 1)
        if weather_mode == 0:
            if mode > 1:
                mode = (mode - 1) * 4 + 1
            weather_mode = 1
        else:
            if mode > 1:
                mode = (mode - 1) / 4 + 1
            weather_mode = 0
        wturn = mode

    mode = wturn % (wfore_num[weather_mode][turn_cycle] + 1)

    # 현재 날씨 표시
    if mode == 0:
        kisang.getWeatherCur(now)
        with canvas(device) as draw:
            kisang.weather_cur_disp(draw, now)

    # 날씨 예보 표시
    else:
        mode -= 1
        mode %= wfore_num[weather_mode][turn_cycle]
        with canvas(device) as draw:
            if weather_mode == 0: # 'multi':
                kisang.weather_fore_multi_disp(draw, mode)
            else:
                kisang.weather_fore_disp(draw, mode)

    disp_sleep.sleep(600)

#
#----------------------------------------------------------------------
#

last_time = "Unknown"
CALENDAR_DISP_MODE_NUM = 5
calendar_disp_mode = 0
prev_calendar_disp_mode = 0
anniversary_disp_pos = -1

def calendar_disp():
    global last_time
    global calendar_disp_mode, prev_calendar_disp_mode

    now =  datetime.datetime.now()

    mode = calendar_disp_mode % CALENDAR_DISP_MODE_NUM
    if mode == 2: # 일출
        with canvas(device) as draw:
            kisang.astral_sun_disp(draw, now)
        disp_sleep.sleep(60)

    elif mode == 3:  # 월출
        with canvas(device) as draw:
            kisang.astral_moon_disp(draw, now)
        disp_sleep.sleep(60)

    elif mode == 4: # 기념일
        with canvas(device) as draw:
            kisang.anniversary_disp(draw, anniversary_disp_pos)
        disp_sleep.sleep(60)

    elif mode == 0 or mode == 1:
        now_time = now.strftime("%H:%M:%S")
        if now_time == last_time and prev_calendar_disp_mode == calendar_disp_mode:
            disp_sleep.sleep(0.1)
            return
        last_time = now_time
        with canvas(device) as draw:
            kisang.calendar_disp(draw, now, mode)
        disp_sleep.sleep(0.1)

    prev_calendar_disp_mode = calendar_disp_mode

#---------------------------------------------------------------
#
# 네트웍 상태 표시
#

init_net_mesg = "init network"
init_net_mesg_cond = ""

def network_disp():
    global init_net_mesg, init_net_mesg_cond

    with canvas(device) as draw:
        mesg = subprocess.check_output('hostname', shell=True).splitlines()[0]
        draw.text((0, -1), unicode(mesg), font=fonts.gulim16, fill='white')

        try:
            mesg = subprocess.check_output('hostname -I', shell=True).splitlines()[0]
            mesg = mesg.split(' ')
        except: mesg = [""]
        mlen = len(mesg)
        if (mlen > 0) and (mesg[0] != ''):
            if mlen > 2: py = 18
            else: py = 24
            for ip_addr in mesg:
                if ip_addr:
                    draw.text((0, py), ip_addr, font=fonts.gulim16, fill='white')
                    py += 16
            try:
                mesg = subprocess.check_output("get_wifi_info.py", shell=True)
                draw.text((0, 53), mesg, font=fonts.gulim12, fill='white')
            except: pass
            init_net_mesg = "init network"
            init_net_mesg_cond = ""
        else:
            draw.text((0, 32), init_net_mesg, font=fonts.gulim16, fill='white')
            slen = draw.textsize(init_net_mesg, font=fonts.gulim16)[0]
            if slen > 128:
                init_net_mesg_cond += '.'
                draw.text((0, 44), init_net_mesg_cond, font=fonts.gulim16, fill='white')
                time.sleep(1)
            else:
                init_net_mesg += '.'

    return mesg

#---------------------------------------------------------------

def tda7439_disp():
    with canvas(device) as draw:
        tda7439.tda7439_disp(draw)
    disp_sleep.sleep(60)

#---------------------------------------------------------------

if os.path.exists("/var/local/www/db/moode-sqlite3.db"):
    moode_db_mute_state = "sqlite3 /var/local/www/db/moode-sqlite3.db " + "'select value from cfg_system where id='36'" + "'"
else:
    moode_db_mute_state = "sqlite3 /var/www/db/player.db " + "'select value from cfg_engine where id='36'" + "'"

prev_vol = None

def volume_disp(draw, vol = 999):
    global prev_vol

    if (vol == 999):
        try:
            #vol_str = subprocess.check_output("amixer get Digital | egrep -o '[0-9]+%' | awk -F % '{print $1}'", shell=True).splitlines()[0]
            #vol = int(subprocess.check_output("/var/www/vol.sh", shell=True).splitlines()[0])
            vol = int(subprocess.check_output("mpc volume | egrep -om 1 '[0-9]+%' | awk -F % '{print $1}'", shell=True).splitlines()[0])
        except: vol = None

    db_mute = 0
    '''
    try:
        db_mute = int(subprocess.check_output(moode_db_mute_state, shell=True).splitlines()[0])
    except: db_mute = 0
    '''

    if tda7439.get_tda7439_mute() == 1 or db_mute or (vol != None and vol < 0):
        disp_str = "Mute"
    else:
        if (vol == None): # 볼륨 정보가 없음을 의미함
            vol = prev_vol
        if (vol == None): # 여전히 볼륨 정보가 없음
            disp_str = "V ≡"
        else:
            gain = tda7439.get_tda7439_gain()
            tda_vol = tda7439.get_tda7439_volume()
            vol += gain + tda_vol
            if tda7439.get_tda7439_power() == 0:
                if (gain > 0 or tda_vol): disp_str = "∧:" + str(vol)
                else: disp_str = "V " + str(vol)
            else:
                if (gain > 0 or tda_vol): disp_str = "V:" + str(vol)
                else: disp_str = "V " + str(vol)

    if (vol > 99):
        slen = draw.textsize(unicode(disp_str), font=fonts.gulim13)[0]
        draw.text((128-slen, 52), unicode(disp_str), font=fonts.gulim13, fill='white')
    else:
        slen = draw.textsize(unicode(disp_str), font=fonts.gulim14)[0]
        draw.text((128-slen, 51), unicode(disp_str), font=fonts.gulim14, fill='white')

    prev_vol = vol

    return slen


#---------------------------------------------------------------

def gain_disp(draw, vol = 999):
    if tda7439.get_tda7439_mute() == 1:
        disp_str = "Mute"
    elif ((vol != None) and (vol < 0)):
        disp_str = "mute"
    else:
        gain = tda7439.get_tda7439_gain()
        tda_vol = tda7439.get_tda7439_volume()
        vol = gain + tda_vol
        if tda7439.get_tda7439_power() == 0:
            disp_str = "∧:" + str(vol)
        else:
            disp_str = "G:" + str(vol)

    slen = draw.textsize(unicode(disp_str), font=fonts.gulim14)[0]
    draw.text((128-slen, 51), unicode(disp_str), font=fonts.gulim14, fill='white')

#
#---------------------------------------------------------------
#
def try_to_get_inet_radio_station_name(inet_radio_mesg = ""):
    global inet_grs_idx, inet_rs_idx

    mesg = ""
    try:
        if not inet_radio_mesg:
            with open(inet_radio_mesg_file, 'r') as fd:
                inet_radio_mesg = fd.read()

        mesgs = re.findall('[\s]*Name[\s]*:[^\n]+', inet_radio_mesg, re.IGNORECASE)
        if mesgs:
            mesg = mesgs[0].split(':',1)[1].strip()
        if not mesg:
            mesgs = re.findall('[\s]*copyright[\s]*:[^\n]+', inet_radio_mesg, re.IGNORECASE)
            if mesgs:
                mesg = mesgs[0].split(':',1)[1].strip()
        if mesg:
            station_addr = inet_rs_lists[inet_grs_idx]['addr'][inet_rs_idx]
            if inet_radio_sub.hangul_code_check(station_addr):
                mesg = mesg.decode('cp949') 
    except: pass

    return mesg

#---------------------------------------------------------------
prev_hscroll_mode = 0

def check_hscroll_lr(hscroll1, hscroll2, hscroll3, slen1, slen2, slen3):
    global px1, wpx1, px2, wpx2, px3, wpx3
    global hscroll1_move, hscroll2_move, hscroll3_move
    global prev_hscroll_mode

    # 수평 스크롤이 2개 이상이면
    if hscroll1 + hscroll2 + hscroll3 > 1:
        prev_hscroll_mode = 1
        return True

    if prev_hscroll_mode == 1:
        if hscroll1 and px1 < (device.width - slen1):
            px1 = wpx1 = 0
        if hscroll2 and px2 < (device.width - slen2):
            px2 = wpx2 = 0
        if hscroll3 and px3 < (device.width - slen3):
            px3 = wpx3 = 0
        prev_hscroll_mode = 0

    if hscroll1:
        if px1 >= 0 and wpx1 == 0:
            hscroll1_move = 1
        elif px1 <= (device.width - slen1) and hscroll1_move > 0:
            wpx1 = 0
            hscroll1_move = -1
        elif px1 >= 0 and hscroll1_move < 0:
            wpx1 = 0
            hscroll1_move = 1
    if hscroll2:
        if px2 >= 0 and wpx2 == 0:
            hscroll2_move = 1
        elif px2 <= (device.width - slen2) and hscroll2_move > 0:
            wpx2 = 0
            hscroll2_move = -1
        elif px2 >= 0 and hscroll2_move < 0:
            wpx2 = 0
            hscroll2_move = 1
    if hscroll3:
        if px3 >= 0 and wpx3 == 0:
            hscroll3_move = 1
        elif px3 <= (device.width - slen3) and hscroll3_move > 0:
            wpx3 = 0
            hscroll3_move = -1
        elif px3 >= 0 and hscroll3_move < 0:
            wpx3 = 0
            hscroll3_move = 1

    return False

#---------------------------------------------------------------
prev_radio_station_name = ""
prev_radio_song_info = ""
prev_radio_album_info = ""
hscroll1_move = 1
hscroll2_move = 1
hscroll3_move = 1

inet_radio_buf_stat = sysv_ipc.SharedMemory(0x6693, sysv_ipc.IPC_CREAT, 0666, 1)

def inet_radio_disp(mode = 0):
    global inet_grs_idx, inet_rs_idx
    global music_note_pos
    global px1, wpx1, px2, wpx2, px3, wpx3
    global prev_sec
    global prev_radio_station_name
    global prev_radio_song_info
    global prev_radio_album_info
    global inet_radio_playing, inet_radio_pause
    global hscroll1_move, hscroll2_move, hscroll3_move

    # check bluetooth
    try:
        if not subprocess.call(pactl_running_check_cmd, stderr=subprocess.PIPE, shell=True):
            bluetooth_inner_disp(1)
            return
    except: pass

    sec = int(time.time())
    if (sec != prev_sec):
        prev_sec = sec
        new_sec = True # do something every second

        # 현재 플레이 중 인지 체크
        try:
            subprocess.check_output(mplayer_check_cmd, shell=True)
            inet_radio_playing = True   # inet radio 플레이 중
        except:
            inet_radio_playing = False  # inet radio STOP
            inet_radio_pause = False
    else: new_sec = False

    try:
        with open(inet_radio_mesg_file, 'r') as fd:
            inet_radio_mesg = fd.read()
    except: inet_radio_mesg = ""

    # get Inet Radio Title
    if len(inet_rs_lists[inet_grs_idx]['addr']) < 0:
        mesg = "방송국 파일 오류"
    elif inet_rs_idx < len(inet_rs_lists[inet_grs_idx]['addr']):
        if inet_rs_lists[inet_grs_idx]['flag'][inet_rs_idx]:
            mesg = inet_rs_lists[inet_grs_idx]['name'][inet_rs_idx]
        else:
            mesg = try_to_get_inet_radio_station_name(inet_radio_mesg)
            if mesg:
                inet_rs_lists[inet_grs_idx]['list'][inet_rs_idx] = mesg
                inet_rs_lists[inet_grs_idx]['name'][inet_rs_idx] = mesg
            else:
                mesg = inet_rs_lists[inet_grs_idx]['name'][inet_rs_idx]
    else:
        mesg = try_to_get_inet_radio_station_name(inet_radio_mesg)
        if not mesg:
            mesg = "Radio Stream"

    if (prev_radio_station_name != mesg) or not inet_radio_playing:
        prev_radio_station_name = mesg
        px1 = wpx1 = 0

    with canvas(device) as draw:
        # display Inet Radio Title
        mesg = my_unicode(mesg)
        slen1 = draw.textsize(mesg, font=fonts.gulim14)[0]
        if slen1 > device.width + 2: # 2 is margin
            hscroll1 = 1
        else:
            px1 = (device.width - slen1) / 2
            hscroll1 = 0

        station_addr = inet_rs_lists[inet_grs_idx]['addr'][inet_rs_idx]

        # try to get song info
        song, album = inet_radio_sub.get_inet_song_info(inet_radio_mesg, station_addr)
        song = my_unicode(song)
        album = my_unicode(album)

        if (prev_radio_song_info != song) or not inet_radio_playing:
            prev_radio_song_info = song
            px2 = wpx2 = 0

        if prev_radio_album_info != album or not inet_radio_playing:
            prev_radio_album_info = album
            px3 = wpx3 = 0

        # song info display
        hscroll2 = 0
        if song:
            song = my_unicode(song)
            slen2 = draw.textsize(song, font=fonts.gulim14)[0]
            if slen2 > device.width + 2: # 2 is margin
                hscroll2 = 1
            else:
                px2 = (device.width - slen2) / 2
        else: slen2 = 0 # 의미없음, to avoid errors

        # Album 또는 Stream 정보
        hscroll3 = 0
        if album and mode != 1:
            slen3 = draw.textsize(album, font=fonts.gulim14)[0]
            if slen3 > device.width + 2: # 2 is margin
                hscroll3 = 1
            else:
                px3 = (device.width - slen3) / 2
        else:
            slen3 = device.width # 의미없음, to avoid errors
            info = inet_radio_sub.get_inet_stream_info(inet_radio_mesg, inet_radio_playing)
            try:
                info0_val = int(info[0]) # 정수 아니면 예외 발생
                kbps_unit = 'kbps'
                try:
                    ekbps = inet_rs_lists[inet_grs_idx]['kbps'][inet_rs_idx]
                    if ekbps != 0 and info[0]:
                        if abs(ekbps - info0_val) * 100 / ekbps > 10: # 차이가 10% 이상이면
                            kbps_unit = '?bps' # means expected kbps 10% diff
                except: pass
            except:
                kbps_unit = ''
            stream_info_draw(draw, 0, 34, info, kbps_unit, unicode('㎑'))

        if check_hscroll_lr(hscroll1, hscroll2, hscroll3, slen1, slen2, slen3):
            if hscroll1:
                mesg += "…     " + mesg
                if (px1 <= -(slen1 + 39)): px1 = wpx1 = 0
                hscroll1_move = 1
            if hscroll2:
                song += "…     " + song
                if (px2 <= -(slen2 + 39)): px2 = wpx2 = 0
                hscroll2_move = 1
            if hscroll3:
                album += "…     " + album
                if (px3 <= -(slen3 + 39)): px3 = wpx3 = 0
                hscroll3_move = 1

        draw.text((px1, -1), mesg, font=fonts.gulim14, fill='white')
        draw.text((px2, 16), song, font=fonts.gulim14, fill='white')
        if album and mode != 1:
            draw.text((px3, 32), album, font=fonts.gulim14, fill='white')

        spectrum_present = False

        # Inet Radio 플레이 중일 경우 Heart beat
        if inet_radio_playing:
            if inet_radio_pause:
                draw.text((2, 55), '2', font=fonts.guifx, fill='white') # pause
            elif mode != 2: # spectrum
                if new_sec:
                    prev_sec = sec
                    music_note_pos = (music_note_pos + 1) % 2
                draw.text((0, 52), unicode(music_note[music_note_pos]), font=fonts.gulim12, fill='white')
            else:
                spectrum_disp(draw)
                spectrum_present = True
        else:
            draw.text((0, 52), "STOP", font=fonts.gulim12, fill='white')

        if not spectrum_present:
            # 현재 번호 / 전체 station 수 
            total_len = len(inet_rs_lists[inet_grs_idx]['list'])
            mesg = str(inet_rs_idx+1) + '/' + str(total_len)
            if total_len > 99:
                slen = draw.textsize(mesg, font=fonts.gulim13)[0]
                draw.text((86-slen, 52), mesg, font=fonts.gulim13, fill='white')
            else:
                slen = draw.textsize(mesg, font=fonts.gulim14)[0]
                draw.text((80-slen, 51), mesg, font=fonts.gulim14, fill='white')

            # 볼륨 표시
            volume_disp(draw)

            # Line for mplayer buffer stat
            if inet_radio_playing:
                px = ord(inet_radio_buf_stat.read()[0]) * device.width / 100
                draw.line(((0, 50),(px,50)), fill='white')
                draw.rectangle((px-1, 49, px+1, 51), outline='white', fill='white')

        if spectrum_present or (inet_radio_playing and not inet_radio_pause and (hscroll1 or hscroll2 or hscroll3)):
            if spectrum_present: hx = 1
            else: hx = 2

            if hscroll1:
                wpx1 += 1
                if (wpx1 > HSCROLL_STOP_TIME): px1 -= hscroll1_move * hx
            if hscroll2:
                wpx2 += 1
                if (wpx2 > HSCROLL_STOP_TIME): px2 -= hscroll2_move * hx
            if hscroll3:
                wpx3 += 1
                if (wpx3 > HSCROLL_STOP_TIME): px3 -= hscroll3_move * hx

            if spectrum_present:
                disp_sleep.sleep(0.03)
            else:
                disp_sleep.sleep(0.10)
        else: disp_sleep.sleep(1)

#---------------------------------------------------------------

bluetooth_icon = Image.open("/home/pi/data/bluetooth_icon.png")
pactl_running_check_cmd = "pactl list sources short | grep -i RUNNING > /dev/null 2>&1"

def bluetooth_disp():
    with canvas(device) as draw:
        draw.text((0, 0), unicode("Bluetooth 입력"), font=fonts.gulim14, fill='white')
        draw.text((0, 26), unicode("CSR8645"), font=fonts.gulim14, fill='white')
        draw.bitmap((108, 2), bluetooth_icon, fill="white")

        # 볼륨 표시
        gain_disp(draw)

        if tda7439.get_tda7439_power() == 0:
            draw.text((0, 51), unicode("셀렉터 없음"), font=fonts.gulim12, fill='white')

    disp_sleep.sleep(3600)

#---------------------------------------------------------------

def bluetooth_inner_disp(running=0, vol=999):
    global music_note_pos

    source_name = "Bluetooth 입력 없음"
    source = False
    try:
        try:
            mesg = subprocess.check_output("hcitool con | grep -i 'SLAVE'", shell=True)
        except:
            # MASTER이면 SLAVE로 바꿈. 그러면 소리 끊김 줄어듬
            mesg = subprocess.check_output("hcitool con | grep -i 'MASTER'", shell=True)
            blue_mac = mesg.split(None,3)[2]
            if subprocess.call("hcitool sr " + blue_mac + " slave > /dev/null 2>&1", shell=True):
                # SLAVE로 바뀌지 않으면, 다음 명령 실행
                subprocess.call("pkill -9 -x rfcomm", shell=True)
                subprocess.call("rfcomm connect /dev/rfcomm0 " + blue_mac + " 1 > /dev/null 2>&1 &", shell=True)
            disp_sleep.sleep(1)
            return
        source = True
        blue_mac = mesg.split(None,3)[2]
        mesg = subprocess.check_output("hcitool info " + blue_mac, shell=True)
        source_name = re.findall('Device Name:[\s]*[^\n]+', mesg)[0].split(':',1)[1].strip()
        company = re.findall('OUI Company:[\s]*[^\s]+', mesg)[0].split(':',1)[1].strip()
        mesg = subprocess.check_output("hcitool rssi " + blue_mac, shell=True)
        rssi = mesg.split(':', 1)[1][:-1]
    except: pass
    #try:
    #    source_name = subprocess.check_output("pactl list sources | grep -i 'bluez.alias'", shell=True)
    #    source = True
    #    source_name = source_name.split('"', 1)[1]
    #    source_name = source_name[:-2]
    #except: pass

    with canvas(device) as draw:
        draw.text((0, 0), unicode(source_name), font=fonts.gulim14, fill='white')
        if source:
            draw.bitmap((110, 16), bluetooth_icon, fill="white")
            try:
                draw.text((0, 16), unicode(company), font=fonts.gulim14, fill='white')
                mesg = rssi + ' dBm'
                slen = draw.textsize(mesg, font=fonts.gulim14)[0]
                draw.text((87-slen, 51), mesg, font=fonts.gulim14, fill='white')
            except: pass
        else:
            draw.bitmap((36, 28), bluetooth_icon, fill="white")

        if running or not subprocess.call(pactl_running_check_cmd, shell=True):
            # Heart beat
            music_note_pos = (music_note_pos + 1) % 2
            draw.text((0, 52), unicode(music_note[music_note_pos]), font=fonts.gulim12, fill='white')
        elif source:
            # pause
            draw.text((2, 55), '2', font=fonts.guifx, fill='white')

        # 볼륨 표시
        volume_disp(draw, vol)

    disp_sleep.sleep(1)

#---------------------------------------------------------------

def aux_disp():
    img_path = "/home/pi/data/aux_icon.png"
    logo = Image.open(img_path)

    with canvas(device) as draw:
        draw.text((0, 0), unicode("AUX 입력"), font=fonts.gulim14, fill='white')
        draw.bitmap((14, 24), logo, fill="white")

        # 볼륨 표시
        gain_disp(draw)

        if tda7439.get_tda7439_power() == 0:
            draw.text((0, 51), unicode("셀렉터 없음"), font=fonts.gulim12, fill='white')

    disp_sleep.sleep(3600)

#---------------------------------------------------------------

def stream_info_draw(draw, px, py, info, unit1, unit2):
    if len(info) == 3:
        try:
            if info[0] == '' or info[1] == '' or info[2] == '': return
        except: return

        slen0 = draw.textsize(info[0], font=fonts.gulim12)[0]
        slen1 = draw.textsize(info[1], font=fonts.gulim12)[0]
        slen2 = draw.textsize(info[2], font=fonts.gulim12)[0]

        # most of the case, unit1('kbps')과 unit2('㎑')의 길이는 고정
        if unit1 == 'kbps': ulen1 = 26
        elif unit1 == '': ulen1 = -1
        else: ulen1 = draw.textsize(unit1, font=fonts.gulim12)[0] - 1 # 26
        ulen2 = 11 #draw.textsize(unit2, font=fonts.gulim12)[0] - 1

        spc = 130 - px - slen0 - ulen1 - slen1 - ulen2 - slen2 - 6
        spc2 = spc/2
        if spc2 > 3:
            spc1 = 3
        elif spc2 > 2:
            spc1 = 2; spc2 = 4
        elif spc2 > 1:
            spc1 = 2; spc2 = 3
        elif spc2 > 0:
            spc1 = 1; spc2 = 3
        else:
            spc1 = 1; spc2 = 2

        draw.text((px, py), info[0], font=fonts.gulim12, fill='white') # kbps val
        px += spc1 + slen0
        draw.text((px, py), unit1, font=fonts.gulim12, fill='white') # kbps
        px += spc2 + ulen1
        draw.text((px, py), info[1], font=fonts.gulim12, fill='white') # Mhz val
        px += spc1 + slen1
        draw.text((px-1, py), unit2, font=fonts.gulim12, fill='white') # Mhz
        if spc2 > 2:
            draw.text((128-slen2, py), info[2], font=fonts.gulim12, fill='white') # bits:channel
        else:
            draw.text((px+ulen2+spc2, py), info[2], font=fonts.gulim12, fill='white') # bits:channel
    elif len(info) == 2:
        slen1 = draw.textsize(info[1], font=fonts.gulim12)[0]
        draw.text((-1, py), info[0] + " " + unit1, font=fonts.gulim12, fill='white') # kbps val
        draw.text((128 - slen1, py), info[1], font=fonts.gulim12, fill='white') # kbps
    elif info[0] != '0':
        draw.text((-1, py), info[0], font=fonts.gulim12, fill='white') # kbps val


#---------------------------------------------------------------

px1 = px2 = px3 = 0
wpx1 = wpx2 = wpx3 = 0
prev_album = prev_title = prev_artist = ""
music_note = ['♪','♬']
music_note_pos = 0
prev_sec = 0
mpd_disp_time = 0
ms_mesg = ['0', 'Unknown']
mpd_status = {}
mpd_spectrum_flag = 0
inet_radio_mode = 0 # 0: normal, 1: stream tech info, 2: spectrum

try:
    subprocess.check_output('pgrep -x cava', shell=True)
    if os.path.exists("/var/local/ramdisk/cava.fifo"):
        cava_fifo = open("/var/local/ramdisk/cava.fifo", 'r')
        _flag = fcntl.fcntl(cava_fifo.fileno(), fcntl.F_GETFD)
        fcntl.fcntl(cava_fifo.fileno(), fcntl.F_SETFL, _flag | os.O_NONBLOCK)
except:
    sys.stderr.write("Warnnig: CAVA is not running\n")

#---------------------------------------------------------------

spectrum_max = [0] * 43
prev_cava_data_line = ""

def spectrum_disp(draw):
    global prev_cava_data_line

    try:
        cnt = 0
        while True:
            try:
                data_line = cava_fifo.readline()
                cnt += 1
            except: break # "Resource temporarily unavailable" that means fifo is empty

        # data 없으면 이전 data 사용
        if not cnt:
            data_line = prev_cava_data_line
        else:
            prev_cava_data_line = data_line

        data = map(int, data_line.strip().split(';')[0:42])

        for i in range(0,43):
            if i < 21: val = data[i] # Left
            elif i == 21: val = (data[20] + data[21]) // 2 # Center
            elif i > 21: val = data[i-1] # Right

            draw.rectangle((i*3, 63, i*3+1, 63-val), outline='cyan', fill='cyan')
            if val > spectrum_max[i]: spectrum_max[i] = val
            elif val < spectrum_max[i]:
                draw.rectangle((i*3, 63-spectrum_max[i], i*3+1, 63-spectrum_max[i]), outline='red', fill='red')
                if (spectrum_max[i] - val) > 12: spectrum_max[i] -= 3
                elif (spectrum_max[i] - val) > 4: spectrum_max[i] -= 2
                else: spectrum_max[i] -= 1
    except: pass

#---------------------------------------------------------------

#
# MPD 상태 표시
#
def mpd_disp(spectrum = 0):
    global px1, px2, px3
    global wpx1, wpx2, wpx3
    global prev_album, prev_title, prev_artist
    global music_note_pos, prev_sec
    global mpd_disp_time, ms_mesg
    global mpd_info_mode
    global mpd_status
    global hscroll1_move, hscroll2_move, hscroll3_move
    spectrum = 1

    try:
        mode = mpd_status['mode']
        album = mpd_status['album']
        title = mpd_status['title']
        artist = mpd_status['artist']
        state = mpd_status['state']
        eltime = mpd_status['eltime']
        play_time = mpd_status['play_time']
        vol = mpd_status['volume']
    except:
        disp_sleep.sleep(1)
        return

    # check bluetooth 
    try:
        if not subprocess.call(pactl_running_check_cmd, stderr=subprocess.PIPE, shell=True):
            bluetooth_inner_disp(1, vol)
            return
    except: pass

    if ((state != 'airplay') and eltime == 0):
        px1 = wpx1 = px2 = wpx2 = px3 = wpx3 = 0
        mpd_disp_time = 0
    else:
        if (not (mode & 0x01) and album != prev_album):
            px3 = wpx3 = 0
            mpd_disp_time = 0
        if (title != prev_title):
            px1 = wpx1 = 0
            mpd_disp_time = 0
        if (artist != prev_artist):
            px2 = wpx2 = 0
            mpd_disp_time = 0
    prev_album = album
    prev_title = title
    prev_artist = artist

    with canvas(device) as draw:
        # title
        title = my_unicode(title)
        slen1 = draw.textsize(title, font=fonts.gulim14)[0]
        if slen1 > device.width + 2: # 2 is margin
            hscroll1 = True
        else:
            px1 = (device.width - slen1) / 2
            hscroll1 = False

        # artist
        artist = my_unicode(artist)
        slen2 = draw.textsize(artist, font=fonts.gulim14)[0]
        if slen2 > device.width + 2: # 2 is margin
            hscroll2 = True
        else:
            px2 = (device.width - slen2) / 2
            hscroll2 = False

        # album
        hscroll3 = False
        if mode & 0x01: # radio stream의 경우 kbps, khz 정보 표시
            slen3 = device.width # 의미없음, to avoid errors
            try:
                # VBS의 경우 bps 정보의 자릿수가 변경됨에 따른 디스플레이 흔들림 방지를 위해...
                if (mpd_disp_time % 10) == 0: # 1초마다 표시
                    ms_mesg = album.split(' ') # ms_mesg[0]:kbps, ms_mesg[1]:khz bits ch

                kbps_val = int(ms_mesg[0])
                mesg0 = ms_mesg[0]
                if kbps_val > 100000:
                    kbps_val /= 1000
                    mesg0 = str(kbps_val)
                    bps_unit = 'Mbps'
                else: bps_unit = 'kbps'

                if kbps_val < 5000: px = 24
                else: px = 30
                px = px - len(mesg0) * 6
                if px < -1: px = -1
                ms_mesg[0] = mesg0
                stream_info_draw(draw, px, 34, ms_mesg, bps_unit, unicode('㎑'))
            except: pass
        else:
            album = my_unicode(album)
            slen3 = draw.textsize(album, font=fonts.gulim14)[0]
            if slen3 > device.width + 2: # 2 is margin
                hscroll3 = True
            else:
                px3 = (device.width - slen3) / 2

        if check_hscroll_lr(hscroll1, hscroll2, hscroll3, slen1, slen2, slen3):
            if hscroll1:
                title += "…     " + title
                if (px1 <= -(slen1 + 39)): px1 = wpx1 = 0
                hscroll1_move = 1
            if hscroll2:
                artist += "…     " + artist
                if (px2 <= -(slen2 + 39)): px2 = wpx2 = 0
                hscroll2_move = 1
            if hscroll3:
                album += "…     " + album
                if (px3 <= -(slen3 + 39)): px3 = wpx3 = 0
                hscroll3_move = 1

        draw.text((px1, -1), title, font=fonts.gulim14, fill='white')
        draw.text((px2, 16), artist, font=fonts.gulim14, fill='white')
        if not mode & 0x01:
            draw.text((px3, 32), album, font=fonts.gulim14, fill='white')

        spectrum_present = False

        # Heart beat
        if (state != 'stop'):
            if state == 'play' or state == 'airplay':
                if spectrum == 1 and state == 'play':
                    # Line for 경과시간
                    if play_time[1] > 0:
                        px = play_time[0] * device.width / play_time[1]
                        draw.line(((0, 63),(px,63)), fill='white')
                    spectrum_disp(draw)
                    spectrum_present = True
                else:
                    sec = int(time.time())
                    if (sec != prev_sec):
                        prev_sec = sec
                        music_note_pos = (music_note_pos + 1) % 2
                    draw.text((0, 52), unicode(music_note[music_note_pos]), font=fonts.gulim12, fill='white')
            else:
                draw.text((2, 55), '2', font=fonts.guifx, fill='white') # pause

        if not spectrum_present:
            # 볼륨
            vslen = volume_disp(draw, vol)

            # 경과시간
            if (state == 'stop'): mesg = "STOP"
            elif (state == 'airplay'): mesg = "AIRPLAY"
            else:
                h = eltime // 3600
                eltime %= 3600
                m = eltime // 60
                s = eltime % 60
                if (h > 0): mesg = "%d:%02d:%02d" % (h, m, s)
                else: mesg = "%02d:%02d" % (m, s)
            if (mode & 0x02):
                mesg += " dlna"
            if len(mesg) < 6:
                draw.text((16, 51), mesg, font=fonts.gulim14, fill='white')
                slen = draw.textsize(mesg, font=fonts.gulim14)[0]
            else:
                draw.text((16, 53), mesg, font=fonts.gulim12, fill='white')
                slen = draw.textsize(mesg, font=fonts.gulim12)[0]

            # random. repeat, single, consume
            if (mode & 0x02) == 0: # not dlna
                pm_mesg = ""
                try:
                    if mpd_info_mode:
                        if (mpd_status['single'] == '1'): pm_mesg += ']'
                        if (mpd_status['consume'] == '1'): pm_mesg += 'x' #'-'
                        if not pm_mesg: pm_mesg = 'z'
                    else:
                        if (mpd_status['random'] == '1'): pm_mesg += '&'
                        if (mpd_status['repeat'] == '1'): pm_mesg += '*'
                except: pass
                if pm_mesg:
                    pslen = draw.textsize(pm_mesg, font=fonts.guifx)[0]
                    spc = 112 - vslen - slen - pslen
                    draw.text((16+slen+spc/2, 55), unicode(pm_mesg), font=fonts.guifx, fill='white')

            # Line for 경과시간
            if play_time[1] > 0:
                px = play_time[0] * device.width / play_time[1]
                draw.line(((0, 50),(px,50)), fill='white')
                draw.rectangle((px-1, 49, px+1, 51), outline='white', fill='white')

        if spectrum_present or ((state == 'play' or state == 'airplay') and (hscroll1 or hscroll2 or hscroll3)):
            if spectrum_present: hx = 1
            else: hx = 2

            if hscroll1:
                wpx1 += 1
                if (wpx1 > HSCROLL_STOP_TIME): px1 -= hscroll1_move * hx
            if hscroll2:
                wpx2 += 1
                if (wpx2 > HSCROLL_STOP_TIME): px2 -= hscroll2_move * hx
            if hscroll3:
                wpx3 += 1
                if (wpx3 > HSCROLL_STOP_TIME): px3 -= hscroll3_move * hx

            if spectrum_present:
                disp_sleep.sleep(0.03)
            else:
                disp_sleep.sleep(0.10)
            mpd_disp_time += 1
        else:
            disp_sleep.sleep(1)
            mpd_disp_time = 10

# 
#--------------------------------------------------------------------------------- 
# 
def memory_inet_radio_disp():
    global list_sel_p
    global last_memory_inet_radio
    global px1, wpx1
    global inet_grs_idx, inet_rs_idx
    global last_grs_pos, last_rs_pos

    if last_memory_inet_radio == 0:
        last_grs_pos = list_sel_p % len(inet_grs_lists)
        list_sel("인터넷 방송", list_sel_p, inet_grs_lists)

    else:
        trs_num = len(inet_rs_lists[last_grs_pos]['list'])
        if trs_num > 0:
            list_sel_p %= trs_num
        else:
            list_sel_p = -1

        if inet_grs_idx == last_grs_pos:
            name = try_to_get_inet_radio_station_name()
            if name:
                if inet_rs_lists[inet_grs_idx]['list'][inet_rs_idx] != name:
                    inet_rs_lists[inet_grs_idx]['list'][inet_rs_idx] = name
                    px1 = wpx1 = 0
                if not inet_rs_lists[inet_grs_idx]['flag'][inet_rs_idx]:
                    inet_rs_lists[inet_grs_idx]['name'][inet_rs_idx] = name
        last_rs_pos[last_grs_pos] = list_sel_p
        list_sel(inet_grs_lists[last_grs_pos], list_sel_p, inet_rs_lists[last_grs_pos]['list'])

# 
#--------------------------------------------------------------------------------- 
# 
fm_station_num = 0
def fm_station_lists_disp():
    global list_sel_p
    global fm_station_num
    global fm_station_name_lists

    slen = len(fm_station_name_lists)
    if slen > 0:
        fm_station_num %= slen
        list_sel_p %= slen
    else:
        fm_station_num = -1
        list_sel_p = -1

    list_sel("FM 방송국", list_sel_p, fm_station_name_lists)
# 
#--------------------------------------------------------------------------------- 
# 
def get_tuned_fm_station_num():
    global px1, wpx1
    global list_sel_p
    global fm_station_num
    global fm_station_name_lists

    # get tuned_freq
    try:
        with open(fm_tuned_freq_file, 'r') as fd:
            fm_station_num = fm_station_freq_lists.index(fd.readline())
    except: fm_station_num = 0
    px1 = wpx1 = 0
    list_sel_p = fm_station_num
# 
#--------------------------------------------------------------------------------- 
# 
mpd_playlists = []
cur_playlist_name = ""
cur_playlist_index = -1
mpd_playlists_songs_pos = {}

def get_mpd_playlists():
    global list_sel_p, mpd_playlists
    global px1, wpx1
    global cur_playlist_name, cur_playlist_index

    mpd_playlists = subprocess.check_output("mpc lsplaylists | sort", shell=True).splitlines()
    try:
        with open(playlist_name_file, 'r') as fd:
            cur_playlist_name = fd.readline()
        list_sel_p = mpd_playlists.index(cur_playlist_name)
    except: list_sel_p = 0

    mpd_playlists.append(extra_playlist_name)
    if cur_playlist_name == extra_playlist_name:
        list_sel_p = mpd_playlists.index(cur_playlist_name)

    cur_playlist_index = list_sel_p
    px1 = wpx1 = 0

    check_and_add_airplay_dlna()
# 
#--------------------------------------------------------------------------------- 
def check_and_add_airplay_dlna():
    global list_sel_p
    global cur_playlist_name
    global cur_playlist_index
    global px1, wpx1

    state = mpd_status['state']
    mode = mpd_status['mode']

    if state == 'airplay':
        if mpd_playlists.count('ＡIRPLAY') == 0:
            if mpd_playlists.count('ＤLNA') > 0:
                mpd_playlists.remove('ＤLNA')
            mpd_playlists.append('ＡIRPLAY')
            list_sel_p = len(mpd_playlists) - 1
            px1 = wpx1 = 0
            cur_playlist_name = 'ＡIRPLAY'
            cur_playlist_index = list_sel_p
    elif mode & 0x02:  # DLNA
        if mpd_playlists.count('ＤLNA') == 0:
            if mpd_playlists.count('ＡIRPLAY') > 0:
                mpd_playlists.remove('ＡIRPLAY')
            mpd_playlists.append('ＤLNA')
            list_sel_p = len(mpd_playlists) - 1
            px1 = wpx1 = 0
            cur_playlist_name = 'ＤLNA'
            cur_playlist_index = list_sel_p
    else:
        if mpd_playlists.count('ＤLNA') > 0: mpd_playlists.remove('ＤLNA')
        if mpd_playlists.count('ＡIRPLAY') > 0: mpd_playlists.remove('ＡIRPLAY')
#
#--------------------------------------------------------------------------------- 
#
def memory_mpd_disp():
    global last_memory_mpd
    global last_memory_mpd_pos
    global list_sel_p

    if last_memory_mpd == 0:
        check_and_add_airplay_dlna()
        last_memory_mpd_pos = list_sel_p
        list_sel("Playlists", list_sel_p, mpd_playlists)
    else:
        memory_mpd_songs_disp()
# 
#--------------------------------------------------------------------------------- 
# 
playlist_songs = []
prev_song = ""
prev_song_pos = 0
gpsi_time_prev = -1

def memory_mpd_songs_disp():
    global list_sel_p, playlist_songs
    global px1, wpx1
    global prev_song, prev_song_pos
    global cur_playlist_name
    global gpsi_time_prev
    global memory_mpd_songs_list_name
    global memory_mpd_songs_from_playlist
    global mpd_status

    state = mpd_status['state']
    mode = mpd_status['mode']

    # airplay or dlna
    if cur_playlist_name == memory_mpd_songs_list_name and (state == 'airplay' or (mode & 0x02)):
        if (state == 'airplay'): label = "ＡIRPLAY"
        else: label = "ＤLNA"

        try: artist = mpd_status['artist']
        except:  artist = ""
        try: title = mpd_status['title']
        except:  title = ""

        if artist:
            if title:
                song = artist + " - " + title
            else: song = artist
        elif title: song = title
        else: song = "[no music info]"

        list_sel(label, 0, [song])
        return 0        # means airplay or dlna

    song_pos = mpd_status['song_pos']
    gpsi_time = int(time.time())

    if memory_mpd_songs_from_playlist: # 현재 cur_playlist를 사용 중이면
        if state != 'stop':   # 현재 플레이 중이면
            # get current playing song
            songs = subprocess.check_output("mpc current", shell=True).splitlines()
            if songs: song = songs[0]
            else: song = prev_song

            if (song != prev_song or song_pos != prev_song_pos):     # 플레이하는 곡이 바뀌었으면
                prev_song = song                # 곡이름 저장
                prev_song_pos = song_pos

                # 현재 플레이 중인 곡으로 커서 옮김
                if (list_sel_p != mpd_status['song_pos']):
                    list_sel_p = mpd_status['song_pos']
                    px1 = wpx1 = 0
                # radio station의 경우 플레이 수초후에 title 바뀜, 또는 DLNA로 전환되었을 때
                try:
                    if memory_mpd_songs_list[list_sel_p] != song:
                        memory_mpd_songs_list[list_sel_p] = song
                        px1 = wpx1 = 0
                except: pass

            elif (gpsi_time % 2) == 0 and gpsi_time != gpsi_time_prev: # 2초 마다
                gpsi_time_prev = gpsi_time
                get_playlist_songs(False, cur_playlist_name, False)
        else:
            if (gpsi_time % 2) == 0 and gpsi_time != gpsi_time_prev: # 2초 마다
                gpsi_time_prev = gpsi_time
                get_playlist_songs(False, cur_playlist_name, False)
    elif memory_mpd_songs_list_name == extra_playlist_name:
        if (gpsi_time % 2) == 0 and gpsi_time != gpsi_time_prev: # 2초 마다
            gpsi_time_prev = gpsi_time
            get_playlist_songs(False, memory_mpd_songs_list_name, False)

    list_sel(memory_mpd_songs_list_name, list_sel_p, memory_mpd_songs_list)

    return 1    # means mpd
# 
#--------------------------------------------------------------------------------- 
#

list_sel_p = 0

def list_sel(title, pos, lists):
    global px1, wpx1
    global prev_sec, music_note_pos
    global cur_playlist_index
    global disp_mode
    global inet_radio_playing, inet_radio_pause
    global last_memory_inet_radio
    global inet_grs_idx, inet_rs_idx
    global last_grs_pos, last_rs_pos
    global last_memory_mpd
    global memory_mpd_songs_from_playlist
    global mpd_status
    global hscroll1_move

    if not disp_mode.startswith('memory_'):
        disp_sleep.sleep(1)
        return # Todo: Unknown 오류, 아마도 multiprocessing으로 인한 문제

    title = my_unicode(title)
    list_len = len(lists)
    if list_len == 0:
        with canvas(device) as draw:
            draw.text((0, -1), title, font=fonts.gulim14, fill='white')
            draw.text((0, 28), "Empty Lists", font=fonts.gulim14, fill='white')
        disp_sleep.sleep(0.9)
        return

    pos %= list_len
    with canvas(device) as draw:
        # list 번호 표시
        num_str = "%d" % (pos+1) + '/' + "%d" % list_len
        slen = draw.textsize(num_str, font=fonts.gulim13)[0]
        num_dp = 128 - slen
        draw.text((num_dp, -1), num_str, font=fonts.gulim13, fill='white')

        # title 표시
        dsp_space = num_dp - 10
        slen = draw.textsize(title, font=fonts.gulim14)[0]
        if (slen <=  dsp_space):
            draw.text((0, -1), title, font=fonts.gulim14, fill='white')
        else:
            i = 3
            for i in range(5, len(title)):
                if (draw.textsize(title[0:i], font=fonts.gulim14)[0] > dsp_space): break
            title = title[0:i-1] + '…'
            draw.text((0, 0), title, font=fonts.gulim14, fill='white')

        si = int(pos / 3) * 3
        #if (si + 3 > list_len): si = list_len - 3

        # 현재 플레이 중인 index 가져오기
        if disp_mode.startswith("memory_inet_radio"):
            try:
                subprocess.check_output(mplayer_check_cmd, shell=True)
                inet_radio_playing = True
                if last_memory_inet_radio == 0:
                    play_pos = inet_grs_idx
                elif inet_grs_idx == last_grs_pos:
                    play_pos = inet_rs_idx
                else:
                    play_pos = -1
            except:
                inet_radio_playing = False
                inet_radio_pause = False
                play_pos = -1
        elif disp_mode.startswith("memory_fm"):
            play_pos = fm_station_num
        else: # memory_mpd
            if last_memory_mpd == 1:
                if memory_mpd_songs_from_playlist and mpd_status['state'] != 'stop':
                    play_pos = mpd_status['song_pos']
                else: play_pos = -1
            else:
                if mpd_status['state'] != 'stop':
                    play_pos = cur_playlist_index
                else: play_pos = -1

        y = 16
        for i in range(si,si+3):
            if i >= list_len: break

            list_str = my_unicode(lists[i])
            if i == pos:  # 커서 위치
                fcol = 0
                draw.rectangle((0,y,device.width-1,y+15), outline='white', fill='white')
                slen = draw.textsize(list_str, font=fonts.gulim14)[0]
                if slen > 112:
                    #list_str += "…     " + list_str
                    #if (px1 <= -(slen + 39)): px1 = wpx1 = 0
                    if px1 >= 0 and wpx1 == 0:
                        hscroll1_move = 2
                    elif (device.width - slen - 16) >= px1 and hscroll1_move > 0:
                        wpx1 = 0
                        hscroll1_move = -2
                    elif px1 >= 0 and hscroll1_move < 0:
                        wpx1 = 0
                        hscroll1_move = 2
                    hscroll1 = True
                else: hscroll1 = False
                draw.text((16+px1, y), list_str, font=fonts.gulim14, fill='black')
                draw.rectangle((0,y,15,y+15), outline='white', fill='white')
            else:
                fcol = 255
                draw.text((16, y), list_str, font=fonts.gulim14, fill='white')

            # music note flash or pause
            if i == play_pos:
                show_music_note = flash_music_note = False
                # memory_inet_radio
                if disp_mode.startswith("memory_inet_radio"):
                    show_music_note = True
                    if not inet_radio_pause:
                        flash_music_note = True
                # memory_fm
                elif disp_mode.startswith("memory_fm"):
                    try:
                        with open(fm_tuned_status_file, 'r') as fd:
                            fm_status = fd.read().splitlines()
                            if fm_status[5] == '0': # standby
                                show_music_note = flash_music_note = True
                            else:
                                show_music_note = flash_music_note = False
                            if fm_status[4] == '0': # unmute
                                flash_music_note = True
                            else:
                                flash_music_note = False
                    except:
                        show_music_note = flash_music_note = True
                # memory_mpd
                else:
                    show_music_note = True
                    state = mpd_status['state']
                    if state == 'play' or state == 'airplay':
                        flash_music_note = True

                if show_music_note:
                    if flash_music_note:
                        sec = int(time.time())
                        if sec != prev_sec:
                            prev_sec = sec
                            music_note_pos = (music_note_pos + 1) % 2
                        draw.text((0, y+1),  unicode(music_note[music_note_pos]), font=fonts.gulim12, fill=fcol)
                    else:
                        draw.text((3, y+4), '2', font=fonts.guifx, fill=fcol) # pause

            y += 16

    if hscroll1:
        wpx1 += 1
        if (wpx1 > HSCROLL_STOP_TIME): px1 -= hscroll1_move
        disp_sleep.sleep(0.1)
    elif disp_mode.startswith('memory_fm'):
        disp_sleep.sleep(3600)  # ssd1306 i2c 신호가 i2c FM module에 노이즈를 유발시킬 가능성 차단.
    else:
        disp_sleep.sleep(1)

#---------------------------------------------------------------

def fm_disp():
    try:
        with open(fm_tuned_status_file, 'r') as fd:
            tune_status = fd.read().splitlines()
    except:
        subprocess.call(fm_radio_cmd + " > /dev/null 2>&1", shell=True)
        with canvas(device) as draw:
            draw.text((0, 0), unicode("FM 수신기 없음"), font=fonts.gulim14, fill='white')
        disp_sleep.sleep(1)
        return

    with canvas(device) as draw:
        # tuned 주파수
        freq = tune_status[0]
        draw.text((0, 0), freq + " MHz", font=fonts.gulim14, fill='white')

        # 방송국 이름과 preset 번호 
        if len(tune_status[1]) > 0:
            station_info = tune_status[1].rsplit(None, 1)
            if len(station_info) > 1:
                draw.text((0, 18), my_unicode(station_info[0]), font=fonts.gulim14, fill='white')
                pos = 1
            else: pos = 0
            slen = draw.textsize(station_info[pos], font=fonts.gulim13)[0]
            draw.text((128-slen, 1), station_info[pos], font=fonts.gulim13, fill='white')

        # standby
        if tune_status[5] == '0':
            # 신호 세기
            pos = 109 * int(tune_status[2]) /15
            draw.rectangle((0,44,pos,44+1), outline='white', fill='white')
            draw.text((pos+4, 34), tune_status[2], font=fonts.gulim13, fill='white')
            if tune_status[4] == "0": # radio module unmute
                draw.text((0, 54), '1', font=fonts.guifx, fill='white') # play
                draw.text((14, 50), tune_status[3], font=fonts.gulim14, fill='white') # stereo/mono
            else:
                draw.text((2, 53), '2', font=fonts.guifx, fill='white') # pause
        else:
            draw.text((0, 53), '3', font=fonts.guifx, fill='white')
            draw.text((14, 50), "STOP", font=fonts.gulim14, fill='white') # STOP

        # 볼륨 표시
        gain_disp(draw)

    disp_sleep.sleep(3600)

#---------------------------------------------------------------

def alarm_disp():
    with canvas(device) as draw:
        alarm.alarm_list_disp(draw)

    disp_sleep.sleep(60)

#---------------------------------------------------------------

prev_sleep_wait = 0
sleep_wait = 0
sleep_mode = 0
sleep_time = None
def sleep_disp():
    global prev_sleep_wait, sleep_wait, sleep_mode, sleep_time

    with canvas(device) as draw:
        draw.text((0, 0), unicode("Sleep 설정"), font=fonts.gulim14, fill='white')
        s_time = (sleep_wait % 7) * 30
        if s_time == 0:
            sleep_time = None
            draw.text((0, 28), unicode("설정값이 없습니다"), font=fonts.gulim14, fill='white')
        else:
            localtime = time.localtime(time.time())
            now = datetime.datetime(localtime.tm_year, localtime.tm_mon, localtime.tm_mday, localtime.tm_hour, localtime.tm_min)
            if prev_sleep_wait != sleep_wait or sleep_time == None:
                sleep_time = now + datetime.timedelta(minutes=s_time)
                prev_sleep_wait = sleep_wait

            r_time = sleep_time - now
            sec = r_time.total_seconds()
            hour = int(sec / 3600)
            minute = int((sec % 3600) / 60)
            tmp_str = ""
            if hour > 0:
                tmp_str += str(hour) + "시간 "
            if minute > 0:
                tmp_str += str(minute) + "분 "
            if hour > 0 or minute > 0:
                tmp_str += "후"
            draw.text((0, 20), unicode(tmp_str), font=fonts.gulim14, fill='white')
            if sleep_mode % 2:
                draw.text((0, 38), unicode("Power OFF"), font=fonts.gulim14, fill='white')
            else:
                draw.text((0, 38), unicode("Sound OFF"), font=fonts.gulim14, fill='white')

    disp_sleep.sleep(60)

#=====================================================================

raw_disp_mode = ""
lock = thread.allocate_lock()
def get_disp_mode_thread():
    global raw_disp_mode

    # disp_mesg fifo 생성
    if not os.path.exists(disp_mode_fifo):
        os.mkfifo(disp_mode_fifo)

    # keep waiting for fifo input
    while True:
        fd = open(disp_mode_fifo, 'r')
        while True:
            data = fd.read()
            if len(data) == 0: break

            lock.acquire()
            raw_disp_mode = data
            lock.release()
            disp_sleep.wake()

#--------------------------------------------------------------------
def usb_monitor_thread():
    global pds_lock

    time.sleep(2)
    pds_lock.acquire()
    subprocess.call("mpc -q update USB", shell=True)
    pds_lock.release()

    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem='usb')

    for device in iter(monitor.poll, None):
        if device.action == 'add' or device.action == 'remove':
            if device.get('DEVNAME') is not None:
                #print 'event {0} on device {1}'.format(device.action, device)
                time.sleep(2)
                pds_lock.acquire()
                subprocess.call("mpc -q update --wait USB", shell=True)
                pds_lock.release()


#--------------------------------------------------------------------
expected_now = None
def sleep_mode_thread():
    global sleep_mode, sleep_wait, sleep_time
    global disp_sleep
    global disp_mode_recv_cnt
    global expected_now
    global device_hide

    prev_led_color = None
    prev_disp_mode_recv_cnt = disp_mode_recv_cnt
    contrast = 128
    dimm_time_cnt = 0

    while True:
        localtime = time.localtime(time.time())
        now = datetime.datetime(localtime.tm_year, localtime.tm_mon, localtime.tm_mday, localtime.tm_hour, localtime.tm_min)
        if not kisang.NtpStat:
            kisang.does_ntp_work()

        if kisang.NtpStat and sleep_time:
            if not prev_led_color:
                prev_led_color = subprocess.check_output("led.py read", shell=True).splitlines()[0]
            subprocess.call("led.py red", shell=True)
            if sleep_time <= now:
                disp_sleep.wake()
                sleep_wait = 0
                sleep_time = None

                # 소리 크기 점차 감소
                for vol in range(-1,-41,-1):
                    subprocess.call("tda7439 no 0 volume " + str(vol), shell=True)
                    time.sleep(0.1)

                # sound play all off
                subprocess.call("mpc -q stop", shell=True)
                subprocess.call("fm_radio off > /dev/null 2>&1", shell=True)
                subprocess.call("pkill -9 -x mplayer", shell=True)
                subprocess.call("tda7439 no 0 sel 1 > /dev/null 2>&1", shell=True)

                subprocess.call("tda7439 no 0 volume 0", shell=True)

                if sleep_mode == 1:
                    subprocess.call("sync", shell=True)
                    time.sleep(2)
                    subprocess.call("sound_poweroff.py 0", shell=True) # 종료 sound off
                    sys.exit(0)
        else:
            if prev_led_color:
                 led = subprocess.check_output("led.py read", shell=True).splitlines()[0]
                 if led == 'red':
                     subprocess.call("led.py " + prev_led_color, shell=True)
                 prev_led_color = None

        # dimming
        if prev_disp_mode_recv_cnt == disp_mode_recv_cnt:
            dimm_time_cnt += 1
            if dimm_time_cnt > 10:
                contrast -= 6
                if contrast < 0:
                    contrast = 0
                device.contrast(contrast)
            if dimm_time_cnt > 60:
                device.hide()
                device_hide = True
        else:
            contrast = 128
            dimm_time_cnt = 0
            prev_disp_mode_recv_cnt = disp_mode_recv_cnt

        # alarm
        if kisang.NtpStat:
            if expected_now and now > expected_now:
                while now >= expected_now:
                    alarm.check_alarm(expected_now)
                    expected_now += datetime.timedelta(minutes=1)
            else:
                alarm.check_alarm(now)
            expected_now = now + datetime.timedelta(minutes=1)

        time.sleep(60)

#=====================================================================

poller = mpdc.MPDPoller()

#----------------------------------------
def mpd_poll():
    global mpd_status

    mpd_status = poller.poll()
    if mpd_status:
        return True
    else:
        mpd_status['state'] = 'stop'
        mpd_status['mode'] = 0
        mpd_status['song_pos'] = 0
        return False

#----------------------------------------

HSCROLL_STOP_TIME = 14
#import timeit

def main():
    global poller
    global disp_mode, disp_mode_once
    global disp_sleep
    global inet_radio_playing, inet_radio_pause
    global network_disp_flag
    global device_hide
    global last_memory_mpd
    global anniversary_len
    global mpd_status

    # signal handler 등록
    #signal.signal(signal.SIGUSR1, sig_handler)

    # alarm 정보 읽기
    alarm.read_alarm()

    # abortable sleep()
    disp_sleep = Sleep()

    # disp_mode를 받기위해 named pipe read 대기하는 thread
    thread.start_new_thread(get_disp_mode_thread, ())

    # sleep thread
    thread.start_new_thread(sleep_mode_thread, ())

    read_inet_rs_info()
    read_fm_station_info()

    anniversary_len = kisang.anniversary_read_info()

    # wait for localhost network ready
    while True:
        try:
            mesg = subprocess.check_output('hostname -i', shell=True).splitlines()[0]
            mesg = mesg.split(' ')
            if (len(mesg) > 0) and (mesg[0] != ''):
                break
        except: pass
        network_disp()
        time.sleep(1)

    # mpd 준비
    for _ in range(0,3):
        if (poller.connect() == 0):
            break;
        time.sleep(1)

    mpd_poll()

    # disp_mode 조정
    if (len(sys.argv) > 1):
        disp_mode = sys.argv[1]
    else:
        disp_mode = read_disp_mode()

    if disp_mode == 'memory_fm':
        get_tuned_fm_station_num()
    elif disp_mode.startswith('inet_radio'):
        disp_mode = 'inet_radio'
    elif disp_mode == 'memory_mpd' and mpd_status is not None:
        get_mpd_playlists()
        if last_memory_mpd == 1:
            global cur_playlist_name
            get_playlist_songs(False, cur_playlist_name)

    tda7439.get_tda7439()

    try:
        subprocess.check_output(mplayer_check_cmd, shell=True)
        inet_radio_playing = True   # inet radio 플레이 중
        try:
            command1 = "echo get_time_pos > " + inet_radio_sub.inet_radio_fifo_file
            subprocess.call(command1, shell=True)
            command2 = "egrep ANS_TIME_POSITION " + inet_radio_mesg_file
            mesg = subprocess.check_output(command2, shell=True).splitlines()
            mesg1 = mesg[-1].split('=',1)[1]
            time.sleep(0.3)
            subprocess.call(command1, shell=True)
            mesg = subprocess.check_output(command2, shell=True).splitlines()
            mesg2 = mesg[-1].split('=',1)[1]
            if mesg1 == mesg2:
                inet_radio_pause = True
            else:
                inet_radio_pause = False
        except:
            inet_radio_pause = False
    except:
        inet_radio_playing = False  # inet radio STOP
        inet_radio_pause = False

    # USB monitor thread
    thread.start_new_thread(usb_monitor_thread, ())

    # DLNA stream을 radio stream과 구분하기 위해 network 주소를 알 필요가 있음
    my_network = "http://192.168"
    mesg = subprocess.check_output('hostname -I', shell=True).splitlines()[0]
    mesg = mesg.split(' ')
    mlen = len(mesg)
    if (mlen < 1) or (mesg[0] == ''):
        # 실패시 한번만 더 시도.
        time.sleep(3)
        mesg = subprocess.check_output('hostname -I', shell=True).splitlines()[0]
        mesg = mesg.split(' ')
        mlen = len(mesg)
    if (mlen > 0) and (mesg[0] != ''):
        my_network = "http://" + mesg[0].rsplit('.', 2)[0]
        poller.set_my_network(my_network)

    disp_mode_once_time = 0

    # let it work
    while True:
        global lock
        global raw_disp_mode

        lock.acquire()
        received_disp_mode = raw_disp_mode
        raw_disp_mode = ""
        lock.release()
        if received_disp_mode:
            get_disp_mode(received_disp_mode)
            save_disp_mode(disp_mode)

        if device_hide:
            disp_sleep.sleep(60)
            continue

        # 네트워크 상태 디스플레이
        if (disp_mode_once == 'network'):
            network_disp()
            if disp_mode_once_time <= 0:
                disp_mode_once_time = 5
            else:
                disp_mode_once_time -= 1
                if disp_mode_once_time <= 0:
                    disp_mode_once = ""
            disp_sleep.sleep(1)
            continue
        disp_mode_once_time = 0

        if (disp_mode == 'network'):
            network_disp()
            disp_sleep.sleep(1)
            continue

        elif (disp_mode == 'clock'):
            #st = timeit.default_timer()
            calendar_disp()
            #et = timeit.default_timer()
            #print(et-st)
            continue

        elif (disp_mode == 'sleep'):
            sleep_disp()
            continue

        elif (disp_mode == 'alarm'):
            alarm_disp()
            continue

        elif (disp_mode == 'weather'):
            weather_disp()
            continue

        elif (disp_mode == 'air'):
            air_disp(air_page)
            continue

        elif (disp_mode == 'tda7439'):
            tda7439_disp()
            continue

        elif (disp_mode == 'inet_radio'):
            inet_radio_disp(inet_radio_mode)
            continue

        elif (disp_mode == 'fm'):
            fm_disp()
            continue

        elif (disp_mode == 'bluetooth'):
            bluetooth_disp()
            continue

        elif (disp_mode == 'bluetooth_inner'):
            bluetooth_inner_disp()
            continue

        elif (disp_mode == 'aux'):
            aux_disp()
            continue

        # memory_mpd
        elif (disp_mode == 'memory_mpd'):
            result = mpd_poll()
            if result is False:
                disp_sleep.sleep(1)
                continue
            memory_mpd_disp()
            continue

        # memory_inet_radio
        elif (disp_mode == 'memory_inet_radio'):
            memory_inet_radio_disp()
            continue

        # memory_inet_fm
        elif (disp_mode == 'memory_fm'):
            fm_station_lists_disp()
            continue

        # auto 모드는 처음 부팅하고 나서 다른 모드로 전환하기 전까지 임시로 사용.
        if (disp_mode == 'auto'):
            if inet_radio_playing:
                inet_radio_disp(inet_radio_mode)
                continue

            mpd_poll()
            if mpd_status['state'] == 'stop':
                calendar_disp()
                continue

            mpd_disp(mpd_spectrum_flag)
            continue

        # else mpd 모드
        result = mpd_poll()
        if result is False:
            disp_sleep.sleep(1)
            continue

        # MPD stream 정보 표시 여부
        mpd_disp(mpd_spectrum_flag)

import RPi.GPIO as GPIO
GPIO.setwarnings(False)

if __name__ == "__main__":
    try:
        pids = subprocess.check_output('pgrep -x ssd1306_mesg.py', shell=True).splitlines()
        subprocess.call('sudo pkill -9 -x ssd1306_mesg.py', shell=True)
        time.sleep(0.1)
    except: pass

    try:
        device = sh1106(i2c(port=1, address=0x3c))
        #device = ssd1306(i2c(port=1, address=0x3c))
    except IOError:
        try:
            device = sh1106(spi(device=0, port=0))
        except IOError:
            subprocess.call("pkill -9 -x ssd1306_disp_watchdog.py > /dev/null 2>&1", shell=True)
            sys.exit(1)

    main()

    #try:

    # Catch fatal poller errors
    #except PollerError as e:
    #    pass
    #    sys.stderr.write("Fatal poller error: %s" % e)
    #    sys.exit(1)

    # Catch all other non-exit errors
    #except Exception as e:
    #    pass
    #    sys.stderr.write("Unexpected exception: %s" % e)
    #    sys.exit(1)

    # Catch the remaining exit errors
    #except:
    #    pass
    #    sys.exit(0)
