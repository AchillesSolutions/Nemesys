"""Fixed text in each language: concept labels and instant headlines.

Headlines are spoken the instant a big moment happens, before any model has
answered, so they come from templates rather than agents.
"""

from __future__ import annotations

from ..models import EventType
from .viewer import Language

EN, ZH, ES = Language.EN, Language.ZH, Language.ES

CONCEPT_LABELS: dict[str, dict[Language, str]] = {
    "kick_off": {EN: "Kick-off", ZH: "开球", ES: "Saque inicial"},
    "goal": {EN: "Goals", ZH: "进球", ES: "Goles"},
    "offside": {EN: "Offside", ZH: "越位", ES: "Fuera de juego"},
    "foul": {EN: "Fouls", ZH: "犯规", ES: "Faltas"},
    "free_kick": {EN: "Free kicks", ZH: "任意球", ES: "Tiros libres"},
    "direct_free_kick": {EN: "Free kick and the wall", ZH: "任意球与人墙", ES: "Tiro libre y barrera"},
    "corner": {EN: "Corners", ZH: "角球", ES: "Tiros de esquina"},
    "throw_in": {EN: "Throw-ins", ZH: "界外球", ES: "Saques de banda"},
    "goal_kick": {EN: "Goal kicks", ZH: "球门球", ES: "Saques de meta"},
    "yellow_card": {EN: "Yellow cards", ZH: "黄牌", ES: "Tarjeta amarilla"},
    "second_yellow": {EN: "Two yellows make a red", ZH: "两黄变一红", ES: "Doble amarilla"},
    "red_card": {EN: "Red cards", ZH: "红牌", ES: "Tarjeta roja"},
    "ten_men": {EN: "Playing with ten", ZH: "十人应战", ES: "Jugar con diez"},
    "substitution": {EN: "Substitutions", ZH: "换人", ES: "Cambios"},
    "var": {EN: "VAR", ZH: "视频助理裁判", ES: "VAR"},
    "penalty": {EN: "Penalties", ZH: "点球", ES: "Penales"},
    "half_time": {EN: "Half-time", ZH: "中场休息", ES: "Medio tiempo"},
    "added_time": {EN: "Added time", ZH: "补时", ES: "Tiempo añadido"},
    "counter_attack": {EN: "Counter-attacks", ZH: "反击", ES: "Contraataque"},
    "possession": {EN: "Possession", ZH: "控球率", ES: "Posesión"},
}

KIND_LABELS = {
    "story": {EN: "The bigger picture", ZH: "比赛大局", ES: "El panorama"},
    "stakes": {EN: "What's at stake", ZH: "积分形势", ES: "Lo que está en juego"},
}

HEADLINES: dict[EventType, dict[Language, str]] = {
    EventType.GOAL: {EN: "Goal for {team}! {score}.", ZH: "{team}进球了！{score}。", ES: "¡Gol de {team}! {score}."},
    EventType.GOAL_DISALLOWED: {EN: "No goal! It's ruled out. {score}.", ZH: "进球无效！{score}。",
                                ES: "¡Gol anulado! {score}."},
    EventType.PENALTY: {EN: "Penalty to {team}!", ZH: "{team}获得点球！", ES: "¡Penal para {team}!"},
    EventType.RED_CARD: {EN: "Red card! {player} is sent off.", ZH: "红牌！{player}被罚下。",
                         ES: "¡Tarjeta roja! {player} es expulsado."},
    EventType.FULL_TIME: {EN: "Full-time: {score}.", ZH: "全场比赛结束：{score}。", ES: "Final del partido: {score}."},
}

HEADLINE_TITLES: dict[EventType, dict[Language, str]] = {
    EventType.GOAL: {EN: "GOAL!", ZH: "进球！", ES: "¡GOL!"},
    EventType.GOAL_DISALLOWED: {EN: "Goal ruled out", ZH: "进球无效", ES: "Gol anulado"},
    EventType.PENALTY: {EN: "Penalty!", ZH: "点球！", ES: "¡Penal!"},
    EventType.RED_CARD: {EN: "Red card", ZH: "红牌", ES: "Tarjeta roja"},
    EventType.FULL_TIME: {EN: "Full-time", ZH: "全场结束", ES: "Final"},
}


def concept_label(concept: str, lang: Language) -> str:
    return CONCEPT_LABELS.get(concept, {}).get(lang, concept)


def kind_label(kind: str, lang: Language) -> str:
    return KIND_LABELS.get(kind, {}).get(lang, "")
