# TopSupergroupsBot - A telegram bot for telegram public groups leaderboards
# Copyright (C) 2017-2018  Dario <dariomsn@hotmail.it> (github.com/91DarioDev)
#
# TopSupergroupsBot is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# TopSupergroupsBot is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with TopSupergroupsBot.  If not, see <http://www.gnu.org/licenses/>.

import html

from topsupergroupsbot import database
from topsupergroupsbot import get_lang
from topsupergroupsbot import keyboards
from topsupergroupsbot import utils
from topsupergroupsbot import emojis
from topsupergroupsbot import leaderboards
from topsupergroupsbot import constants as c

from telegram.error import (TelegramError, 
                            Unauthorized, 
                            BadRequest, 
                            TimedOut, 
                            ChatMigrated, 
                            NetworkError)
from telegram.ext.dispatcher import run_async


@run_async
def weekly_groups_digest(bot, job):
    near_interval = '7 days'
    far_interval = '14 days'

    query = """
        SELECT
            group_id,
            lang,
            nsfw,
            joined_the_bot
        FROM supergroups
        WHERE weekly_digest = TRUE AND bot_inside = TRUE
        ORDER BY last_date DESC
        """
    lst = database.query_r(query)

    #############
    # MESSAGES
    ############

    query = """
        SELECT 
            group_id,
            COUNT(msg_id) AS msgs, 
            RANK() OVER(PARTITION BY s.lang ORDER BY COUNT(msg_id) DESC)
        FROM messages 
        LEFT OUTER JOIN supergroups as s 
        USING (group_id)
        WHERE 
            message_date > now() - interval %s
            AND (s.banned_until IS NULL OR s.banned_until < now()) 
            AND s.bot_inside IS TRUE
        GROUP BY s.lang, group_id

    """
    msgs_this_week = database.query_r(query, near_interval)

    query = """
        SELECT 
            group_id, 
            COUNT(msg_id) AS msgs,
            RANK() OVER(PARTITION BY s.lang ORDER BY COUNT(msg_id) DESC)
        FROM messages
        LEFT OUTER JOIN supergroups as s 
        USING (group_id)
        WHERE 
            message_date BETWEEN now() - interval %s AND now() - interval %s
            AND (s.banned_until IS NULL OR s.banned_until < now()) 
            AND s.bot_inside IS TRUE
        GROUP BY s.lang, group_id
    """
    msgs_last_week = database.query_r(query, far_interval, near_interval)
    
    #############
    # MEMBERS
    ############

    query = """
         SELECT
            last_members.group_id,
            last_members.amount, 
            RANK() OVER(PARTITION BY s.lang ORDER BY last_members.amount DESC)
        FROM
            (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY group_id
                    ORDER BY updated_date DESC
                    ) AS row
            FROM members
        ) AS last_members 
        LEFT OUTER JOIN supergroups AS s 
        USING (group_id)
        WHERE 
            last_members.row=1
            AND (s.banned_until IS NULL OR s.banned_until < now())
            AND s.bot_inside IS TRUE
    """
    members_this_week = database.query_r(query)


    query = """
        SELECT 
            last_members.group_id, 
            last_members.amount,
            RANK() OVER(PARTITION BY s.lang ORDER BY last_members.amount DESC)
        FROM
            (
            SELECT 
                *, 
                ROW_NUMBER() OVER (
                    PARTITION BY group_id
                    ORDER BY updated_date DESC
                    ) AS row 
            FROM members
            WHERE updated_date <= now() - interval %s
        ) AS last_members 
        LEFT OUTER JOIN supergroups AS s 
        USING (group_id)
        WHERE 
            last_members.row=1
            AND (s.banned_until IS NULL OR s.banned_until < now())
            AND s.bot_inside IS TRUE
        """
    members_last_week = database.query_r(query, near_interval)

    ####################
    # SUM AND AVG VOTES
    ####################

    query = """
        WITH myconst AS
        (SELECT 
            s.lang,
            AVG(vote)::float AS overall_avg
        FROM votes AS v
        LEFT OUTER JOIN supergroups AS s
        ON s.group_id = v.group_id
        WHERE (s.banned_until IS NULL OR s.banned_until < now() )
        AND s.bot_inside IS TRUE
        GROUP BY s.lang
        HAVING COUNT(vote) >= %s)

        SELECT 
          *,
          RANK() OVER (PARTITION BY sub.lang  ORDER BY bayesan DESC)
          FROM (
            SELECT 
                v.group_id,
                s_ref.title, 
                s_ref.username, 
                COUNT(vote) AS amount, 
                ROUND(AVG(vote), 1)::float AS average,
                s.nsfw,
                extract(epoch from s.joined_the_bot at time zone 'utc') AS dt,
                s.lang,
                s.category,
                -- (WR) = (v ?? (v+m)) ?? R + (m ?? (v+m)) ?? C
                --    * R = average for the movie (mean) = (Rating)
                --    * v = number of votes for the movie = (votes)
                --    * m = minimum votes required to be listed in the Top 250 (currently 1300)
                --    * C = the mean vote across the whole report (currently 6.8)
                (  (COUNT(vote)::float / (COUNT(vote)+%s)) * AVG(vote)::float + (%s::float / (COUNT(vote)+%s)) * (m.overall_avg) ) AS bayesan
            FROM votes AS v
            LEFT OUTER JOIN supergroups_ref AS s_ref
            ON s_ref.group_id = v.group_id
            LEFT OUTER JOIN supergroups AS s
            ON s.group_id = v.group_id
            LEFT OUTER JOIN myconst AS m
            ON (s.lang = m.lang)
            GROUP BY v.group_id, s_ref.title, s_ref.username, s.nsfw, s.banned_until, s.lang, s.category, s.bot_inside, s.joined_the_bot, m.overall_avg
            HAVING 
                (s.banned_until IS NULL OR s.banned_until < now()) 
                AND COUNT(vote) >= %s
                AND s.bot_inside IS TRUE
          ) AS sub;
    """
    this_week_votes_avg = database.query_r(
        query, 
        leaderboards.VotesLeaderboard.MIN_REVIEWS,
        leaderboards.VotesLeaderboard.MIN_REVIEWS,
        leaderboards.VotesLeaderboard.MIN_REVIEWS,
        leaderboards.VotesLeaderboard.MIN_REVIEWS,
        leaderboards.VotesLeaderboard.MIN_REVIEWS
    )

    query = """
        SELECT 
            group_id,
            COUNT(vote) AS amount,
            ROUND(AVG(vote), 1) AS average, 
            RANK() OVER(PARTITION BY s.lang ORDER BY ROUND(AVG(VOTE), 1)DESC, COUNT(VOTE)DESC)
        FROM votes
        LEFT OUTER JOIN supergroups AS s 
        USING (group_id)
        WHERE vote_date <= now() - interval %s
        GROUP BY group_id, s.lang, s.banned_until, s.bot_inside
        HAVING 
            (s.banned_until IS NULL OR s.banned_until < now()) 
            AND COUNT(vote) >= %s 
            AND s.bot_inside IS TRUE
    """
    query = """
        WITH myconst AS
        (SELECT 
            s.lang,
            AVG(vote)::float AS overall_avg
        FROM votes AS v
        LEFT OUTER JOIN supergroups AS s
        ON s.group_id = v.group_id
        WHERE (s.banned_until IS NULL OR s.banned_until < now() ) AND vote_date <= now() - interval %s
        AND s.bot_inside IS TRUE
        GROUP BY s.lang
        HAVING COUNT(vote) >= %s)

        SELECT 
          *,
          RANK() OVER (PARTITION BY sub.lang  ORDER BY bayesan DESC)
          FROM (
            SELECT 
                v.group_id,
                s_ref.title, 
                s_ref.username, 
                COUNT(vote) AS amount, 
                ROUND(AVG(vote), 1)::float AS average,
                s.nsfw,
                extract(epoch from s.joined_the_bot at time zone 'utc') AS dt,
                s.lang,
                s.category,
                -- (WR) = (v ?? (v+m)) ?? R + (m ?? (v+m)) ?? C
                --    * R = average for the movie (mean) = (Rating)
                --    * v = number of votes for the movie = (votes)
                --    * m = minimum votes required to be listed in the Top 250 (currently 1300)
                --    * C = the mean vote across the whole report (currently 6.8)
                (  (COUNT(vote)::float / (COUNT(vote)+%s)) * AVG(vote)::float + (%s::float / (COUNT(vote)+%s)) * (m.overall_avg) ) AS bayesan
            FROM votes AS v
            LEFT OUTER JOIN supergroups_ref AS s_ref
            ON s_ref.group_id = v.group_id
            LEFT OUTER JOIN supergroups AS s
            ON s.group_id = v.group_id
            LEFT OUTER JOIN myconst AS m
            ON (s.lang = m.lang)
            WHERE vote_date <= now() - interval %s
            GROUP BY v.group_id, s_ref.title, s_ref.username, s.nsfw, s.banned_until, s.lang, s.category, s.bot_inside, s.joined_the_bot, m.overall_avg
            HAVING 
                (s.banned_until IS NULL OR s.banned_until < now()) 
                AND COUNT(vote) >= %s
                AND s.bot_inside IS TRUE
          ) AS sub;
    """
    last_week_votes_avg = database.query_r(
        query, 
        near_interval, 
        leaderboards.VotesLeaderboard.MIN_REVIEWS,
        leaderboards.VotesLeaderboard.MIN_REVIEWS,
        leaderboards.VotesLeaderboard.MIN_REVIEWS,
        leaderboards.VotesLeaderboard.MIN_REVIEWS,
        near_interval,
        leaderboards.VotesLeaderboard.MIN_REVIEWS
    )

    ##################
    # ACTIVE USERS
    ##################

    query = """
         SELECT
            group_id,
            COUNT(DISTINCT user_id), 
            RANK() OVER(PARTITION BY s.lang ORDER BY COUNT(DISTINCT user_id) DESC)
        FROM messages 
        LEFT OUTER JOIN supergroups AS s 
        USING (group_id)
        WHERE 
            message_date > (now() - interval %s)
            AND (s.banned_until IS NULL OR s.banned_until < now()) 
            AND s.bot_inside IS TRUE
        GROUP BY group_id, s.lang
        """
    this_week_active_users = database.query_r(query, near_interval)

    query = """
        SELECT 
            group_id,
            COUNT(DISTINCT user_id), 
            RANK() OVER(PARTITION BY s.lang ORDER BY COUNT(DISTINCT user_id) DESC)
        FROM messages
        LEFT OUTER JOIN supergroups AS s 
        USING (group_id)
        WHERE 
            message_date BETWEEN (now() - interval %s) AND (now() - interval %s)
            AND (s.banned_until IS NULL OR s.banned_until < now()) 
            AND s.bot_inside IS TRUE
        GROUP BY group_id, s.lang
        """
    last_week_active_users = database.query_r(query, far_interval, near_interval)

    start_in = 0
    for group in lst:
        start_in += 0.1
        group_id = group[0]
        lang = group[1]

        msgs_new = 0
        msgs_old = 0
        msgs_pos_old = 0
        msgs_pos_new = 0

        members_new = 0
        members_old = 0
        members_pos_old = 0
        members_pos_new = 0

        sum_v_new = 0
        avg_v_new = 0
        sum_v_old = 0
        avg_v_old = 0
        avg_pos_old = 0
        avg_pos_new = 0

        act_users_new = 0
        act_users_old = 0
        act_users_pos_old = 0
        act_users_pos_new = 0

        for i in msgs_this_week:
            if i[0] == group_id:
                msgs_new = i[1]
                msgs_pos_new = i[2]
                break

        for i in msgs_last_week:
            if i[0] == group_id:
                msgs_old = i[1]
                msgs_pos_old = i[2]
                break

        for i in members_this_week:
            if i[0] == group_id:
                members_new = i[1]
                members_pos_new = i[2]
                break

        for i in members_last_week:
            if i[0] == group_id:
                members_old = i[1]
                members_pos_old = i[2]
                break

        for i in this_week_votes_avg:
            if i[0] == group_id:
                sum_v_new = i[3]
                avg_v_new = i[4]
                avg_pos_new = i[10]
                break

        for i in last_week_votes_avg:
            if i[0] == group_id:
                sum_v_old = i[3]
                avg_v_old = i[4]
                avg_pos_old = i[10]
                break

        for i in this_week_active_users:
            if i[0] == group_id:
                act_users_new = i[1]
                act_users_pos_new = i[2]
                break

        for i in last_week_active_users:
            if i[0] == group_id:
                act_users_old = i[1]
                act_users_pos_old = i[2]
                break

        diff_msg, percent_msg = diff_percent(msgs_new, msgs_old, lang)
        diff_members, percent_members = diff_percent(members_new, members_old, lang) 
        diff_act, percent_act = diff_percent(act_users_new, act_users_old, lang)

        text = get_lang.get_string(lang, "weekly_groups_digest").format(
            # by messages
            utils.sep_l(msgs_old, lang),
            utils.sep_l(msgs_new, lang),
            diff_msg, percent_msg,
            utils.sep_l(msgs_pos_old, lang),
            utils.sep_l(msgs_pos_new, lang),
            # by members
            utils.sep_l(members_old, lang),
            utils.sep_l(members_new, lang),
            diff_members, percent_members,
            utils.sep_l(members_pos_old, lang),
            utils.sep_l(members_pos_new, lang),
            # by votes average
            utils.sep_l(avg_v_old, lang), emojis.STAR, utils.sep_l(sum_v_old, lang),
            utils.sep_l(avg_v_new, lang), emojis.STAR, utils.sep_l(sum_v_new, lang),
            utils.sep_l(avg_pos_old, lang),
            utils.sep_l(avg_pos_new, lang),
            # by active users
            utils.sep_l(act_users_old, lang),
            utils.sep_l(act_users_new, lang),
            diff_act, percent_act,
            utils.sep_l(act_users_pos_old, lang),
            utils.sep_l(act_users_pos_new, lang)
        )

        ##############
        # TOP n USERS
        ##############

        query_top_users = """
            SELECT 
                user_id,
                COUNT(msg_id) AS num_msgs, 
                name, 
                RANK() OVER (ORDER BY COUNT(msg_id) DESC)
            FROM messages AS m
            LEFT OUTER JOIN users_ref AS u_ref
            USING (user_id)
            WHERE group_id = %s AND m.message_date > (now() - interval %s)
            GROUP BY user_id, name
            LIMIT %s
            """
        top_users_of_the_group = database.query_r(query_top_users, group_id, near_interval, 10)
        for user in top_users_of_the_group:
            text += "{}) <a href=\"tg://user?id={}\">{}</a>: {}\n".format(
                    user[3],
                    user[0],
                    html.escape(utils.truncate(user[2], c.MAX_CHARS_LEADERBOARD_PAGE_GROUP)),
                    utils.sep_l(user[1], lang)
                    )

        text += "\n#weekly_group_digest"
        reply_markup = keyboards.disable_group_weekly_digest_kb(lang)
        # schedule send
        job.job_queue.run_once(
                send_one_by_one_weekly_group_digest, 
                start_in, 
                context=[group_id, text, reply_markup]
                )


def diff_percent(new, old, lang):
    diff = new - old
    diff_s = utils.sep_l(diff, lang) if diff < 0 else "+"+utils.sep_l(diff, lang)
    try:
        percent = round(diff*100/old, 2)
        percent_s = (utils.sep_l(percent, lang) if percent < 0 else "+"+utils.sep_l(percent, lang))+"%"
    except ZeroDivisionError:
        percent_s = "???" 
    return diff_s, percent_s


@run_async
def send_one_by_one_weekly_group_digest(bot, job):
    group_id = job.context[0]
    message = job.context[1]
    reply_markup = job.context[2]
    try:
        bot.send_message(
                chat_id=group_id,
                text=message,
                reply_markup=reply_markup,
                parse_mode='HTML',
                disable_notification=True)
    except Unauthorized:
        query = "UPDATE supergroups SET bot_inside = FALSE WHERE group_id = %s"
        database.query_w(query, group_id)
    except Exception as e:
        print("{} exception is send_one_by_one group digest".format(e))
