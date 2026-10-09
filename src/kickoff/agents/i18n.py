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
    "team_size": {EN: "Eleven a side", ZH: "每队十一人", ES: "Once por equipo"},
    "points": {EN: "Points for a win", ZH: "积分规则", ES: "Puntos por partido"},
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

# Headlines a supporter hears when the moment is their own team's ("ours") or the other side's ("theirs").
FAN_HEADLINES: dict[tuple[EventType, str], dict[Language, str]] = {
    (EventType.GOAL, "ours"): {EN: "Goal! We've scored! {score}.", ZH: "进球了！我们进球了！{score}。",
                               ES: "¡Gol! ¡Hemos marcado! {score}."},
    (EventType.GOAL, "theirs"): {EN: "{team} have scored. {score}.", ZH: "{team}进球了。{score}。",
                                 ES: "Ha marcado {team}. {score}."},
    (EventType.PENALTY, "ours"): {EN: "Penalty to us!", ZH: "我们获得点球！", ES: "¡Penal a favor nuestro!"},
    (EventType.PENALTY, "theirs"): {EN: "Penalty to {team}.", ZH: "{team}获得点球。", ES: "Penal para {team}."},
    (EventType.GOAL_DISALLOWED, "ours"): {EN: "Oh no, our goal is ruled out. {score}.", ZH: "可惜，我们的进球被判无效。{score}。",
                                          ES: "¡No! Nos anulan el gol. {score}."},
    (EventType.GOAL_DISALLOWED, "theirs"): {EN: "Their goal is ruled out! {score}.", ZH: "{team}的进球无效！{score}。",
                                            ES: "¡Les anulan el gol! {score}."},
    (EventType.RED_CARD, "ours"): {EN: "Red card for {player}. We're down to ten.", ZH: "{player}被红牌罚下，我们只剩十人。",
                                   ES: "Roja para {player}. Nos quedamos con diez."},
    (EventType.RED_CARD, "theirs"): {EN: "Red card! {player} is sent off. They're down to ten!",
                                     ZH: "红牌！{player}被罚下，{team}只剩十人！",
                                     ES: "¡Roja! {player} expulsado. ¡Se quedan con diez!"},
}

# Play-by-play calls: short lines spoken the instant something happens, before (or instead of) the agents.
# Keyed by event type and a variant (shot outcome, VAR reason, half); "" is the default.
CALLS: dict[tuple[EventType, str], dict[Language, str]] = {
    (EventType.SHOT, "saved"): {EN: "{player} shoots… saved by {keeper}!", ZH: "{player}射门……被{keeper}扑出！",
                                ES: "¡Disparo de {player}… lo para {keeper}!"},
    (EventType.SHOT, "off_target"): {EN: "{player} shoots, just wide!", ZH: "{player}射门，稍稍偏出！",
                                     ES: "¡Disparo de {player}, se va desviado!"},
    (EventType.SHOT, "blocked"): {EN: "{player}'s shot is blocked!", ZH: "{player}的射门被挡出！",
                                  ES: "¡Bloquean el disparo de {player}!"},
    (EventType.CORNER, ""): {EN: "Corner to {team}.", ZH: "{team}获得角球。", ES: "Córner para {team}."},
    (EventType.OFFSIDE, ""): {EN: "Flag's up! {player} is offside.", ZH: "边裁举旗！{player}越位了。",
                              ES: "¡Bandera arriba! {player} está en fuera de juego."},
    (EventType.YELLOW_CARD, ""): {EN: "Yellow card for {player}.", ZH: "{player}吃到黄牌。",
                                  ES: "Tarjeta amarilla para {player}."},
    (EventType.SUBSTITUTION, ""): {EN: "{team} change: {on} comes on for {off}.", ZH: "{team}换人：{on}替换{off}。",
                                   ES: "Cambio en {team}: entra {on} por {off}."},
    (EventType.VAR_CHECK, "possible penalty"): {EN: "VAR check! They're looking at a possible penalty.",
                                                ZH: "VAR介入！正在检查是否是点球。",
                                                ES: "¡Revisión del VAR! Están mirando un posible penal."},
    (EventType.VAR_CHECK, "possible offside"): {EN: "Hold on, VAR is checking this for offside.",
                                                ZH: "等一下，VAR正在检查是否越位。",
                                                ES: "Atención, el VAR revisa un posible fuera de juego."},
    (EventType.VAR_CHECK, ""): {EN: "VAR is taking a look at this.", ZH: "VAR正在查看回放。",
                                ES: "El VAR está revisando la jugada."},
    (EventType.VAR_DECISION, "penalty"): {EN: "VAR decision: it's a penalty to {team}!", ZH: "VAR判定：{team}获得点球！",
                                          ES: "¡Decisión del VAR: penal para {team}!"},
    (EventType.KICK_OFF, "1"): {EN: "We're under way! {kits}", ZH: "比赛开始！{kits}", ES: "¡Arranca el partido! {kits}"},
    (EventType.KICK_OFF, "2"): {EN: "The second half is under way.", ZH: "下半场开始。", ES: "Comienza la segunda parte."},
    (EventType.HALF_TIME, ""): {EN: "Half-time. {score}.", ZH: "中场休息。{score}。", ES: "Descanso. {score}."},
    (EventType.FREE_KICK, "wall"): {EN: "Free kick to {team}, in shooting range.", ZH: "{team}获得前场任意球，有射门机会。",
                                    ES: "Tiro libre para {team}, en zona de disparo."},
}

# Kit colours, so a newcomer knows which dots are which.
KITS: dict[str, dict[Language, str]] = {
    "kil": {EN: "teal and white", ZH: "青绿色和白色", ES: "verde azulado y blanco"},
    "hbr": {EN: "navy and orange", ZH: "藏青色和橙色", ES: "azul marino y naranja"},
}
KIT_LINE = {EN: "{a} are in {ka}, {b} in {kb}.", ZH: "{a}身穿{ka}，{b}身穿{kb}。", ES: "{a} viste de {ka} y {b} de {kb}."}
KIT_LINE_FAN = {EN: "We're in {ka}, {b} are in {kb}.", ZH: "我们身穿{ka}，{b}身穿{kb}。",
                ES: "Nosotros vestimos de {ka} y {b} de {kb}."}

# Scores as a commentator says them in each language: who leads, or that it is level.
SCORE_LEAD = {EN: "{team} lead {hi}-{lo}", ZH: "{team}以{hi}比{lo}领先", ES: "{team} gana {hi} a {lo}"}
SCORE_WIN = {EN: "{team} win {hi}-{lo}", ZH: "{team}以{hi}比{lo}获胜", ES: "victoria de {team} por {hi} a {lo}"}
SCORE_LEVEL = {EN: "it's {n}-{n}", ZH: "{n}比{n}平", ES: "empate a {n}"}


def score_phrase(lang: Language, home: str, home_goals: int, away: str, away_goals: int, final: bool = False) -> str:
    """'Harbour City lead 1-0', 'empate a uno', '1比1平': never two team names around a score."""
    if home_goals == away_goals:
        return SCORE_LEVEL[lang].format(n=home_goals)
    team, hi, lo = (home, home_goals, away_goals) if home_goals > away_goals else (away, away_goals, home_goals)
    return (SCORE_WIN if final else SCORE_LEAD)[lang].format(team=team, hi=hi, lo=lo)


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
