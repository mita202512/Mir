from httpx import AsyncClient
from asyncio import wait_for, Event
from functools import partial
from time import time
from yt_dlp import YoutubeDL
from swibots import CommandHandler, CallbackQueryHandler, regexp, user

from bot import DOWNLOAD_DIR, bot, config_dict, LOGGER
from bot.helper.ext_utils.bot_utils import (
    new_task,
    sync_to_async,
    arg_parser,
    COMMAND_USAGE,
)
from bot.helper.ext_utils.links_utils import is_url
from bot.helper.ext_utils.status_utils import get_readable_file_size, get_readable_time
from bot.helper.listeners.task_listener import TaskListener
from bot.helper.mirror_leech_utils.download_utils.yt_dlp_download import YoutubeDLHelper
from bot.helper.switch_helper.bot_commands import BotCommands
from bot.helper.switch_helper.button_build import ButtonMaker
from bot.helper.switch_helper.filters import CustomFilters
from bot.helper.switch_helper.message_utils import (
    sendMessage,
    editMessage,
    deleteMessage,
)


@new_task
async def select_format(ctx, obj):
    data = ctx.event.callback_data.split()
    message = ctx.event.message

    if data[1] == "dict":
        b_name = data[2]
        await obj.qual_subbuttons(b_name)
    elif data[1] == "mp3":
        await obj.mp3_subbuttons()
    elif data[1] == "audio":
        await obj.audio_format()
    elif data[1] == "aq":
        if data[2] == "back":
            await obj.audio_format()
        else:
            await obj.audio_quality(data[2])
    elif data[1] == "back":
        await obj.back_to_main()
    elif data[1] == "cancel":
        await editMessage(message, "Task has been cancelled.")
        obj.qual = None
        obj.listener.isCancelled = True
        obj.event.set()
    else:
        if data[1] == "sub":
            obj.qual = obj.formats[data[2]][data[3]][1]
        elif "|" in data[1]:
            obj.qual = obj.formats[data[1]]
        else:
            obj.qual = data[1]
        obj.event.set()


class YtSelection:
    def __init__(self, listener):
        self.listener = listener
        self._is_m4a = False
        self._reply_to = None
        self._time = time()
        self._timeout = 120
        self._is_playlist = False
        self._main_buttons = None
        self.event = Event()
        self.formats = {}
        self.qual = None

    async def _event_handler(self):
        pfunc = partial(select_format, obj=self)
        handler = CallbackQueryHandler(
            pfunc, filter=regexp("^ytq") & user(self.listener.userId)
        )
        self.listener.client.add_handler(handler)
        try:
            await wait_for(self.event.wait(), timeout=self._timeout)
        except:
            await editMessage(self._reply_to, "Timed Out. Task has been cancelled!")
            self.qual = None
            self.listener.isCancelled = True
            self.event.set()
        finally:
            self.listener.client.remove_handler(handler)

    async def get_quality(self, result):
        buttons = ButtonMaker()
        if "entries" in result:
            self._is_playlist = True
            for i in ["144", "240", "360", "480", "720", "1080", "1440", "2160"]:
                video_format = f"bv*[height<=?{i}][ext=mp4]+ba[ext=m4a]/b[height<=?{i}]"
                b_data = f"{i}|mp4"
                self.formats[b_data] = video_format
                buttons.ibutton(f"{i}-mp4", f"ytq {b_data}")
                video_format = f"bv*[height<=?{i}][ext=webm]+ba/b[height<=?{i}]"
                b_data = f"{i}|webm"
                self.formats[b_data] = video_format
                buttons.ibutton(f"{i}-webm", f"ytq {b_data}")
            buttons.ibutton("MP3", "ytq mp3")
            buttons.ibutton("Audio Formats", "ytq audio")
            buttons.ibutton("Best Videos", "ytq bv*+ba/b")
            buttons.ibutton("Best Audios", "ytq ba/b")
            buttons.ibutton("Cancel", "ytq cancel", "footer")
            self._main_buttons = buttons.build_menu(3)
            msg = f"Choose Playlist Videos Quality:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        else:
            format_dict = result.get("formats")
            if format_dict is not None:
                for item in format_dict:
                    if item.get("tbr"):
                        format_id = item["format_id"]

                        if item.get("filesize"):
                            size = item["filesize"]
                        elif item.get("filesize_approx"):
                            size = item["filesize_approx"]
                        else:
                            size = 0

                        if item.get("video_ext") == "none" and (
                            item.get("resolution") == "audio only"
                            or item.get("acodec") != "none"
                        ):
                            if item.get("audio_ext") == "m4a":
                                self._is_m4a = True
                            b_name = f"{item.get('acodec') or format_id}-{item['ext']}"
                            v_format = format_id
                        elif item.get("height"):
                            height = item["height"]
                            ext = item["ext"]
                            fps = item["fps"] if item.get("fps") else ""
                            b_name = f"{height}p{fps}-{ext}"
                            ba_ext = (
                                "[ext=m4a]" if self._is_m4a and ext == "mp4" else ""
                            )
                            v_format = f"{format_id}+ba{ba_ext}/b[height=?{height}]"
                        else:
                            continue

                        self.formats.setdefault(b_name, {})[f"{item['tbr']}"] = [
                            size,
                            v_format,
                        ]

                for b_name, tbr_dict in self.formats.items():
                    if len(tbr_dict) == 1:
                        tbr, v_list = next(iter(tbr_dict.items()))
                        buttonName = f"{b_name} ({get_readable_file_size(v_list[0])})"
                        buttons.ibutton(buttonName, f"ytq sub {b_name} {tbr}")
                    else:
                        buttons.ibutton(b_name, f"ytq dict {b_name}")
            buttons.ibutton("MP3", "ytq mp3")
            buttons.ibutton("Audio Formats", "ytq audio")
            buttons.ibutton("Best Video", "ytq bv*+ba/b")
            buttons.ibutton("Best Audio", "ytq ba/b")
            buttons.ibutton("Cancel", "ytq cancel", "footer")
            self._main_buttons = buttons.build_menu(2)
            msg = f"Choose Video Quality:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        self._reply_to = await sendMessage(
            self.listener.message, msg, self._main_buttons
        )
        await self._event_handler()
        if not self.listener.isCancelled:
            await deleteMessage(self._reply_to)
        return self.qual

    async def back_to_main(self):
        if self._is_playlist:
            msg = f"Choose Playlist Videos Quality:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        else:
            msg = f"Choose Video Quality:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        await editMessage(self._reply_to, msg, self._main_buttons)

    async def qual_subbuttons(self, b_name):
        buttons = ButtonMaker()
        tbr_dict = self.formats[b_name]
        for tbr, d_data in tbr_dict.items():
            button_name = f"{tbr}K ({get_readable_file_size(d_data[0])})"
            buttons.ibutton(button_name, f"ytq sub {b_name} {tbr}")
        buttons.ibutton("Back", "ytq back", "footer")
        buttons.ibutton("Cancel", "ytq cancel", "footer")
        subbuttons = buttons.build_menu(2)
        msg = f"Choose Bit rate for <b>{b_name}</b>:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        await editMessage(self._reply_to, msg, subbuttons)

    async def mp3_subbuttons(self):
        i = "s" if self._is_playlist else ""
        buttons = ButtonMaker()
        audio_qualities = [64, 128, 320]
        for q in audio_qualities:
            audio_format = f"ba/b-mp3-{q}"
            buttons.ibutton(f"{q}K-mp3", f"ytq {audio_format}")
        buttons.ibutton("Back", "ytq back")
        buttons.ibutton("Cancel", "ytq cancel")
        subbuttons = buttons.build_menu(3)
        msg = f"Choose mp3 Audio{i} Bitrate:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        await editMessage(self._reply_to, msg, subbuttons)

    async def audio_format(self):
        i = "s" if self._is_playlist else ""
        buttons = ButtonMaker()
        for frmt in ["aac", "alac", "flac", "m4a", "opus", "vorbis", "wav"]:
            audio_format = f"ba/b-{frmt}-"
            buttons.ibutton(frmt, f"ytq aq {audio_format}")
        buttons.ibutton("Back", "ytq back", "footer")
        buttons.ibutton("Cancel", "ytq cancel", "footer")
        subbuttons = buttons.build_menu(3)
        msg = f"Choose Audio{i} Format:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        await editMessage(self._reply_to, msg, subbuttons)

    async def audio_quality(self, format):
        i = "s" if self._is_playlist else ""
        buttons = ButtonMaker()
        for qual in range(11):
            audio_format = f"{format}{qual}"
            buttons.ibutton(qual, f"ytq {audio_format}")
        buttons.ibutton("Back", "ytq aq back")
        buttons.ibutton("Cancel", "ytq aq cancel")
        subbuttons = buttons.build_menu(5)
        msg = f"Choose Audio{i} Qaulity:\n0 is best and 10 is worst\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        await editMessage(self._reply_to, msg, subbuttons)


def extract_info(link, options):
    with YoutubeDL(options) as ydl:
        result = ydl.extract_info(link, download=False)
        if result is None:
            raise ValueError("Info result is None")
        return result


class YtDlp(TaskListener):
    def __init__(
        self,
        client,
        message,
        _=None,
        isLeech=False,
        __=None,
        sameDir=None,
        bulk=None,
        multiTag=None,
        options="",
    ):
        if sameDir is None:
            sameDir = {}
        if bulk is None:
            bulk = []
        self.message = message
        self.client = client
        self.multiTag = multiTag
        self.options = options
        self.sameDir = sameDir
        self.bulk = bulk
        super().__init__()
        self.isYtDlp = True
        self.isLeech = isLeech

    @new_task
    async def newEvent(self):
        text = self.message.message.split("\n")
        input_list = text[0].split(" ")
        qual = ""

        args = {
            "-s": False,
            "-b": False,
            "-z": False,
            "-sv": False,
            "-f": False,
            "-fd": False,
            "-fu": False,
            "-i": 0,
            "-sp": 0,
            "link": "",
            "-m": "",
            "-opt": "",
            "-n": "",
            "-up": "",
            "-rcf": "",
            "-t": "",
            "-ca": "",
            "-cv": "",
            "-ns": "",
        }

        arg_parser(input_list[1:], args)

        try:
            self.multi = int(args["-i"])
        except:
            self.multi = 0

        self.select = args["-s"]
        self.name = args["-n"]
        self.upDest = args["-up"]
        self.rcFlags = args["-rcf"]
        self.link = args["link"]
        self.compress = args["-z"]
        self.thumb = args["-t"]
        self.splitSize = args["-sp"]
        self.sampleVideo = args["-sv"]
        self.forceRun = args["-f"]
        self.forceDownload = args["-fd"]
        self.forceUpload = args["-fu"]
        self.convertAudio = args["-ca"]
        self.convertVideo = args["-cv"]
        self.nameSub = args["-ns"]

        isBulk = args["-b"]
        folder_name = args["-m"]

        bulk_start = 0
        bulk_end = 0
        reply_to = None
        opt = args["-opt"]

        if not isinstance(isBulk, bool):
            dargs = isBulk.split(":")
            bulk_start = dargs[0] or None
            if len(dargs) == 2:
                bulk_end = dargs[1] or None
            isBulk = True

        if not isBulk:
            if folder_name:
                folder_name = f"/{folder_name}"
                if not self.sameDir:
                    self.sameDir = {
                        "total": self.multi,
                        "tasks": set(),
                        "name": folder_name,
                    }
                self.sameDir["tasks"].add(self.mid)
            elif self.sameDir:
                self.sameDir["total"] -= 1
        else:
            await self.initBulk(input_list, bulk_start, bulk_end, YtDlp)
            return

        if len(self.bulk) != 0:
            del self.bulk[0]

        path = f"{DOWNLOAD_DIR}{self.mid}{folder_name}"

        await self.getTag(text)

        opt = opt or self.userDict.get("yt_opt") or config_dict["YT_DLP_OPTIONS"]

        if not self.link and (reply_to := self.message.replied_to):
            self.link = reply_to.message.split("\n", 1)[0].strip()

        if not is_url(self.link):
            await sendMessage(
                self.message, COMMAND_USAGE["yt"][0], COMMAND_USAGE["yt"][1]
            )
            self.removeFromSameDir()
            return

        if "mdisk.me" in self.link:
            await self._mdisk()

        try:
            await self.beforeStart()
        except Exception as e:
            await sendMessage(self.message, e)
            self.removeFromSameDir()
            return

        options = {"usenetrc": True, "cookiefile": "cookies.txt"}
        if opt:
            yt_opts = opt.split("|")
            for ytopt in yt_opts:
                key, value = map(str.strip, ytopt.split(":", 1))
                if key == "postprocessors":
                    continue
                if key == "format" and not self.select:
                    if value.startswith("ba/b-"):
                        qual = value
                        continue
                    else:
                        qual = value
                if value.startswith("^"):
                    if "." in value or value == "^inf":
                        value = float(value.split("^")[1])
                    else:
                        value = int(value.split("^")[1])
                elif value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
                elif value.startswith(("{", "[", "(")) and value.endswith(
                    ("}", "]", ")")
                ):
                    value = eval(value)
                options[key] = value
        options["playlist_items"] = "0"

        try:
            result = await sync_to_async(extract_info, self.link, options)
        except Exception as e:
            msg = str(e).replace("<", " ").replace(">", " ")
            await sendMessage(self.message, f"{self.tag} {msg}")
            self.removeFromSameDir()
            return
        finally:
            self.run_multi(input_list, folder_name, YtDlp)

        if not qual:
            qual = await YtSelection(self).get_quality(result)
            if qual is None:
                self.removeFromSameDir()
                return

        LOGGER.info(f"Downloading with YT-DLP: {self.link}")
        playlist = "entries" in result
        ydl = YoutubeDLHelper(self)
        await ydl.add_download(path, qual, playlist, opt)

    async def _mdisk(self):
        key = self.link.split("/")[-1]
        async with AsyncClient(verify=False) as client:
            resp = await client.get(
                f"https://diskuploader.entertainvideo.com/v1/file/cdnurl?param={key}"
            )
        if resp.status_code == 200:
            resp_json = resp.json()
            self.link = resp_json["source"]
            if not self.name:
                self.name = resp_json["filename"]


async def ytdl(ctx):
    YtDlp(ctx.app, ctx.event.message).newEvent()


async def ytdlleech(ctx):
    YtDlp(ctx.app, ctx.event.message, isLeech=True).newEvent()


bot.add_handler(
    CommandHandler(BotCommands.YtdlCommand, ytdl)
)
bot.add_handler(
    CommandHandler(
        BotCommands.YtdlLeechCommand, ytdlleech
    )
)
