"""Accurate, plain-English facts about each concept.

The Teacher rewords these for the viewer instead of explaining rules from
the model's memory, which keeps explanations correct. The offline client
also speaks them directly.
"""

RULES: dict[str, str] = {
    "team_size": (
        "Each team has 11 players on the pitch: one goalkeeper, the only player allowed to handle the ball, and "
        "only inside their own penalty area, plus ten outfield players."
    ),
    "points": "In the league, a win earns 3 points, a draw 1 point each, and a defeat none. The table is ranked by points.",
    "kick_off": "Each half starts with a kick-off from the centre spot. After a goal, the team that conceded kicks off.",
    "goal": "A goal counts when the whole ball crosses the goal line between the posts and under the bar.",
    "offside": (
        "A player is offside if, when a teammate passes the ball, they are nearer the opponent's goal line than "
        "both the ball and the second-last defender (usually the last outfield defender). It stops attackers "
        "waiting by the goal. The other team gets a free kick."
    ),
    "foul": "A foul is an unfair challenge, such as tripping, pushing or holding an opponent. The fouled team gets a free kick.",
    "free_kick": "After a foul, the fouled team restarts with a free kick from where it happened. Opponents must stand 10 yards (about 9 metres) away.",
    "direct_free_kick": (
        "A free kick near goal can be shot straight at goal. Defenders line up shoulder to shoulder as a 'wall', "
        "at least 10 yards (about 9 metres) from the ball, to block the shot."
    ),
    "corner": "When a defender is last to touch the ball before it goes over their own goal line, the attackers get a corner kick from the corner flag.",
    "throw_in": "When the ball goes over the side line, the team that did not touch it last throws it back in with both hands, from behind the head.",
    "goal_kick": "When an attacker is last to touch the ball before it goes over the goal line (and it is not a goal), the defending team restarts with a goal kick.",
    "yellow_card": (
        "The referee shows cards for serious fouls or bad behaviour. A yellow card is a warning: the player stays on, "
        "but a second yellow in the same match becomes a red. A red card, straight away or after two yellows, sends "
        "the player off, and the team plays on with one player fewer."
    ),
    "second_yellow": "Two yellow cards in one match make a red card: the player is sent off.",
    "red_card": "A red card sends a player off for the rest of the match, and nobody can replace them. The team plays with one player fewer.",
    "ten_men": "With ten players against eleven, a team usually defends deeper and has less of the ball; the other team has a spare player to attack with.",
    "substitution": "Each team can bring on up to five substitutes from the bench, in up to three stoppages plus half-time. A player who comes off cannot return.",
    "var": (
        "VAR is the video assistant referee: officials watching replays who check goals, penalties, red cards and "
        "mistaken identity. They can advise the referee to change a clear and obvious error."
    ),
    "penalty": (
        "A foul by a defender inside their own penalty area gives the attackers a penalty: one shot from the spot "
        "12 yards (11 metres) out, with only the goalkeeper to beat."
    ),
    "half_time": "A match is two halves of 45 minutes with a 15-minute break at half-time. Teams swap ends for the second half.",
    "added_time": "The referee adds extra minutes at the end of each half to make up for time lost to injuries, substitutions and goal celebrations.",
    "counter_attack": "A counter-attack is a fast break straight after winning the ball, attacking before the other team can get back into position.",
    "possession": "Possession is the share of time a team has the ball. Having more of it does not guarantee more chances.",
}
