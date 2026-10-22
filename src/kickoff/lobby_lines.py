"""What the lobby host says, in each language the lobby speaks, and how it understands the answers.

The host asks for the viewer's language first, in all three at once, and carries on in that one. Text for the
host's voice can mark a run of another language, [zh]...[/zh] or [es]...[/es], so its one multilingual voice says
each part with the right accent; the markers are taken out before anything is shown.
"""

from __future__ import annotations

import re

LANGUAGES = ("English", "Mandarin", "Spanish")
TAG = {"Mandarin": "zh", "Spanish": "es"}

WELCOME = ("Welcome to Nemesys. [zh]欢迎来到 Nemesys。[/zh] [es]Bienvenido a Nemesys.[/es] "
           "English, [zh]中文[/zh], or [es]español[/es]?")
LANGUAGE_AGAIN = "English, [zh]中文[/zh], or [es]español[/es]?"

# What a viewer says to choose a language: its own name for itself, mostly.
LANGUAGE_WORDS = {
    "Mandarin": re.compile(r"中文|汉语|漢語|普通话|普通話|国语|國語|华语|chinese|mandarin|zhongwen|putonghua", re.I),
    "Spanish": re.compile(r"español|espanol|spanish|castellano", re.I),
    "English": re.compile(r"english|inglés|ingles|英语|英文", re.I),
}
DETECTED = {"zh": "Mandarin", "es": "Spanish", "en": "English"}  # speech recognition's language, by prefix


def language_from(text: str, detected: str | None = None) -> str | None:
    """The language the viewer chose: named outright, or (failing that) the language they spoke in."""
    named = [lang for lang, words in LANGUAGE_WORDS.items() if words.search(text)]
    if len(named) == 1:
        return named[0]
    if detected and detected[:2] in ("zh", "es"):
        return DETECTED[detected[:2]]
    return None


def spoken(text: str, language: str) -> str:
    """A line for the host's voice in this language."""
    tag = TAG.get(language)
    return f"[{tag}]{text}[/{tag}]" if tag else text


def plain(text: str) -> str:
    return re.sub(r"\[/?(zh|es)\]", "", text)


CLUB = {"Mandarin": {"kil": "基尔本竞技", "hbr": "港城"}}
TOWN = {"Mandarin": {"Shanghai": "上海", "Beijing": "北京"}, "Spanish": {"Mexico City": "Ciudad de México"}}
RIVAL = {"Mandarin": {"chen": "陈", "lin": "林"}}

LINES = {
    "English": {
        "ask_name": "Great. Who am I talking to?",
        "name_again": "What's your name?",
        "back": "{name}! Welcome back.",
        "nice": "Nice to meet you.",
        "fixture": "{home} against {away} is about to kick off. Who are you supporting?",
        "side_again": "{home}, or {away}?",
        "rematch": "{club}. {rival} from {town} is online, and you watched together last time. Rematch, or someone new?",
        "rematch_again": "Rematch with {rival}, or someone new?",
        "online": "{club}. {rivals} are online. Who do you want to be matched with?",
        "also": "Also online: {rivals}. Who do you want to be matched with?",
        "which": "Who would you like: {rivals}?",
        "one": "{club}. {rival} from {town} is online. Want to watch with them?",
        "only": "Right now it's just {rival}. Want to watch with them?",
        "connecting": "Connecting you to {rival}.",
        "sorry": "Sorry, I didn't catch that.",
    },
    "Mandarin": {
        "ask_name": "好的，我们说中文。请问怎么称呼？",
        "name_again": "你叫什么名字？",
        "back": "{name}，欢迎回来！",
        "nice": "很高兴认识你。",
        "fixture": "{home}对{away}，比赛马上开始。你支持哪个队？",
        "side_again": "{home}，还是{away}？",
        "rematch": "{club}。来自{town}的{rival}在线，上次你们一起看过球。再战一场，还是换个人？",
        "rematch_again": "和{rival}再战一场，还是换个人？",
        "one": "{club}。来自{town}的{rival}在线。要和{rival}一起看吗？",
        "only": "现在只有{rival}在线。要和{rival}一起看吗？",
        "connecting": "正在为你连接{rival}。",
        "sorry": "抱歉，我没听清。",
    },
    "Spanish": {
        "ask_name": "Perfecto, en español. ¿Cómo te llamas?",
        "name_again": "¿Cómo te llamas?",
        "back": "¡{name}! Bienvenido de nuevo.",
        "nice": "Encantado de conocerte.",
        "fixture": "{home} contra {away} está a punto de empezar. ¿Con quién vas?",
        "side_again": "¿{home} o {away}?",
        "rematch": "{club}. {rival}, de {town}, está en línea, y la última vez vieron el partido juntos. "
                   "¿Revancha, o alguien nuevo?",
        "rematch_again": "¿Revancha con {rival}, o alguien nuevo?",
        "one": "{club}. {rival}, de {town}, está en línea. ¿Quieres ver el partido con {rival}?",
        "only": "Ahora mismo solo está {rival}. ¿Quieres ver el partido con {rival}?",
        "connecting": "Te conecto con {rival}.",
        "sorry": "Perdona, no te entendí.",
    },
}

# Yes, and someone else, in each language. "Someone else" is checked first: 不要 contains 要.
YES = {
    "Mandarin": re.compile(r"好|行|可以|是的|对|要|来吧|当然|没问题|再来|一起|再战"),
    "Spanish": re.compile(r"\b(s[ií]|claro|vale|venga|dale|revancha|por favor|de acuerdo|bueno|vamos|perfecto)\b", re.I),
}
NEW = {
    "Mandarin": re.compile(r"不要|不用|不想|换|别人|其他|新的|别的"),
    "Spanish": re.compile(r"\b(no|otro|otra|alguien m[aá]s|alguien nuevo|nuevo|nueva|diferente)\b", re.I),
}

NAME_INTRO_ZH = re.compile(r"(?:我叫|我是|叫我|我的名字是)\s*([一-鿿]{1,4}|[A-Za-z][\w'-]*)")
CJK = re.compile(r"^[一-鿿]{1,4}$")
