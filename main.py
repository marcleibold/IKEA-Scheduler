import os
import sys
import asyncio
from time import sleep
import datetime
from unittest import FunctionTestCase

from pytradfri import Gateway
from pytradfri.api.aiocoap_api import APIFactory
from pytradfri.util import load_json

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from apscheduler.schedulers.background import BackgroundScheduler

upcoming_events = {}


CONFIG_FILE = "tradfri_standalone_psk.conf"
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

sched = BackgroundScheduler()
sched.start()


async def init():
    config = load_json(CONFIG_FILE)
    host = list(config.keys())[0]
    identity = config[host]["identity"]
    psk = config[host]["key"]
    api_factory = await APIFactory.init(host=host, psk_id=identity, psk=psk)
    api = api_factory.request
    gateway = Gateway()
    devices_command = gateway.get_devices()
    devices_commands = await api(devices_command)
    devices = await api(devices_commands)
    lights = [dev for dev in devices if dev.has_light_control]
    blinds = [dev for dev in devices if dev.has_blind_control]
    return api, {"lights": lights, "blinds": blinds}


async def set_blinds(target):
    """lower the blinds

    Parameters
    ----------
    target : int, optional
        the target value to lower the blinds to, by default 100
    """
    target = int(target)
    api, devices = await init()
    left, right = devices["blinds"]
    cmd = [left.blind_control.set_state(target), right.blind_control.set_state(target)]
    await api(cmd)


async def light_on():
    """turn the light on"""
    api, devices = await init()
    light = devices["lights"][0]
    cmd = [
        light.light_control.set_state(1),
        light.light_control.set_dimmer(254),
        light.light_control.set_hex_color("f2eccf"),
        light.light_control.set_color_temp(337)
    ]
    await api(cmd)


async def light_off():
    """turn the light off"""
    api, devices = await init()
    light = devices["lights"][0]
    cmd = light.light_control.set_state(0)
    await api(cmd)


async def light_fade(direction, target):
    """fade the light brightness to a target value

    Parameters
    ----------
    direction : {-1, 1}
        the direction to fade the brightness in (-1: less bright, 1: brighter)
    target : int
        the target brightness value
    """
    direction, target = int(direction), int(target)
    api, devices = await init()
    light = devices["lights"][0]
    if direction == 1:
        curr_dim = light.light_control.lights[0].dimmer
        curr_state = light.light_control.lights[0].state
        if curr_state == False:
            curr_dim = 0
        if curr_dim <= target:
            cmd = [
                light.light_control.set_dimmer(curr_dim),
                light.light_control.set_state(1)
            ]
            await api(cmd)
            for i in range(curr_dim, target, 2 * direction):
                cmd = [light.light_control.set_dimmer(i)]
                await api(cmd)
                sleep(0.1)
    elif direction == -1:
        curr_dim = light.light_control.lights[0].dimmer
        curr_state = light.light_control.lights[0].state
        if curr_state == True:
            for i in range(curr_dim, target, 2 * direction):
                cmd = [light.light_control.set_dimmer(i)]
                await api(cmd)
                sleep(0.1)
            if target == 0:
                cmd = [light.light_control.set_state(False)]
                await api(cmd)


func_map = {
    "set_blinds": set_blinds,
    "light_on": light_on,
    "light_off": light_off,
    "light_fade": light_fade
}


def parse_event(title):
    # split actions
    actions = title.split(";")
    # extract args
    funcs = []
    for action in actions:
        parts = action.split(" ")
        func = parts[0]
        args = parts[1:]
        funcs.append((func, args))
    return funcs


def execute(actions):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for action in actions:
        func_string, args = action
        print(func_string, args)
        func = func_map[func_string]
        loop.run_until_complete(func(*args))


def init_gcalendar():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    service = build('calendar', 'v3', credentials=creds)
    return service


def update_events():
    now = datetime.datetime.utcnow().isoformat() + 'Z'
    service = init_gcalendar()
    events_result = service.events().list(calendarId='6k4b046r6f69qfm1de69ts6n3c@group.calendar.google.com', timeMin=now,
                                          maxResults=10, singleEvents=True,
                                          orderBy='startTime').execute()
    events = events_result.get('items', [])
    ids = set()
    # add new events
    for e in events:
        ids.add(e["id"])
        if e["id"] not in upcoming_events:
            print(e["start"]["dateTime"])
            date = datetime.datetime.strptime(e["start"]["dateTime"], "%Y-%m-%dT%H:%M:%S%z")
            actions = parse_event(e["summary"])
            job = sched.add_job(execute, 'date', run_date=date, args=[actions])

            upcoming_events[e["id"]] = {
                "job": job,
                "summary": e["summary"],
                "time": date
            }
            print(upcoming_events)

    # remove old events
    for _id in upcoming_events.keys():
        if _id not in ids:
            upcoming_events[_id]["job"].remove()
            del upcoming_events[_id]


if __name__ == '__main__':
    while True:
        update_events()
        sleep(60)
